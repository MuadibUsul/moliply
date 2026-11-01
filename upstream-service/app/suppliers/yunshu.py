"""Yunshu (云枢) partner API adapter.

Buy-then-redeem in two steps: ``/order`` debits the shop balance and returns a
trade number plus a one-time secret, ``/check`` validates the session, then
``/redeem`` submits and ``/result`` polls. ``request_no`` makes ``/order``
idempotent, so a retry after a timeout cannot double-charge.

Only status values that are unambiguous are mapped; anything else stays
``pending`` so the order is never settled on a guess.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import SupplierRejected, SupplierResult, SupplierUnavailable, SupplierUncertain

logger = logging.getLogger(__name__)

SUCCESS_STATES = {"success", "succeeded", "completed", "done", "redeemed", "plus_active"}
FAILED_STATES = {"failed", "failure", "declined", "rejected", "refunded", "cancelled"}
PENDING_STATES = {"pending", "processing", "paying", "redeeming", "waiting", "confirmed"}


class YunshuSupplier:
    name = "yunshu"

    def __init__(self, base: str, token: str, sku_map: dict[str, str], timeout: float = 20.0,
                 catalog_race: str = "", sku_options: str = "") -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.sku_map = sku_map
        self.timeout = timeout
        self.catalog_race = catalog_race
        self.sku_options = sku_options

    # -- transport ---------------------------------------------------------
    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        body = {key: value for key, value in data.items() if value not in (None, "")}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base}{path}",
                    content=urlencode(body),
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise SupplierUncertain(f"{path} 超时，可能已扣款") from exc
        except httpx.HTTPError as exc:
            raise SupplierUnavailable(f"{path} 网络异常") from exc

        if response.status_code == 429:
            raise SupplierUnavailable("上游限流")
        if response.status_code >= 500:
            raise SupplierUnavailable(f"上游 {response.status_code}")
        if response.status_code >= 400:
            raise SupplierRejected(f"上游拒绝：HTTP {response.status_code} {response.text[:120]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise SupplierUncertain(f"{path} 返回非 JSON") from exc
        if str(payload.get("code")) != "200":
            message = str(payload.get("msg") or payload.get("message") or "上游返回失败")
            # Transport-level codes are retryable, business codes are not.
            if str(payload.get("code")) in {"429", "500", "502", "503"}:
                raise SupplierUnavailable(message)
            raise SupplierRejected(message)
        return payload.get("data") or {}

    def _code(self, sku: str) -> str:
        try:
            return self.sku_map[sku]
        except KeyError as exc:
            raise SupplierRejected(f"未配置 {sku} 的上游商品编号") from exc

    def _common(self, sku: str) -> dict[str, Any]:
        return {"code": self._code(sku), "race": self.catalog_race, "sku": self.sku_options}

    # -- interface ---------------------------------------------------------
    def quote(self, sku: str, contact: str) -> int:
        data = self._post("/quote", {**self._common(sku), "num": 1, "contact": contact})
        try:
            return int(round(float(data["price"]) * 100))
        except Exception as exc:  # noqa: BLE001
            raise SupplierRejected("上游报价异常") from exc

    def deliver(self, sku: str, session_raw: str, request_no: str, contact: str) -> SupplierResult:
        order = self._post(
            "/order",
            {**self._common(sku), "num": 1, "contact": contact, "request_no": request_no},
        )
        trade_no = str(order.get("tradeNo") or "")
        secret = str(order.get("secret") or "")
        if not trade_no or not secret:
            # Paid but unusable: do not release, the balance is likely spent.
            raise SupplierUncertain("下单成功但缺少 tradeNo/secret")

        if str(order.get("status") or "").lower() not in SUCCESS_STATES | PENDING_STATES:
            logger.warning("Yunshu order %s status=%s", trade_no, order.get("status"))

        check = self._post("/check", {"tradeNo": trade_no, "code": self._code(sku), "secret": secret,
                                      "session": session_raw})
        if str(check.get("status") or "").lower() in FAILED_STATES:
            raise SupplierRejected(str(check.get("message") or "上游预检未通过"))

        self._post("/redeem", {"tradeNo": trade_no, "code": self._code(sku), "secret": secret,
                               "session": session_raw})
        return self.poll(trade_no, secret, sku)

    def poll(self, trade_no: str, secret: str, sku: str, tries: int = 6) -> SupplierResult:
        """Poll ``/result``; returns pending while unresolved."""
        delay = 2.0
        for _ in range(tries):
            time.sleep(delay)
            delay = min(delay * 1.5, 12.0)
            try:
                data = self._post("/result", {"tradeNo": trade_no, "code": self._code(sku), "secret": secret})
            except SupplierUnavailable:
                continue
            status = str(data.get("status") or "").lower()
            message = str(data.get("message") or "")
            if status in SUCCESS_STATES:
                return SupplierResult("success", message or "上游已核销", trade_no)
            if status in FAILED_STATES:
                return SupplierResult("failed", message or "上游核销失败", trade_no)
        return SupplierResult("pending", "上游仍在处理", trade_no)
