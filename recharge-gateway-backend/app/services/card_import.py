"""Parse pasted card batches for the admin console.

Issuers without an API means cards arrive as a copy-paste from the issuer's web
console. Two formats are accepted: a JSON array, or one delimited line per card
with ``pan,month,year,cvv[,balance[,billing_zip]]``. Header rows and ``#``
comments are skipped so a raw export can be pasted unchanged.
"""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from app.services.crypto import luhn_valid

MIN_PAN_LENGTH = 12
MIN_SANE_BALANCE = Decimal("0")
DEFAULT_BILLING_ZIP = "10001"


class CardBatchParseError(ValueError):
    pass


def _clean_pan(raw: Any) -> str:
    pan = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(pan) < MIN_PAN_LENGTH:
        raise CardBatchParseError(f"卡号位数不足：{raw}")
    return pan


def _normalize_exp_month(raw: Any) -> str:
    text = str(raw).strip().strip("0") or "0"
    month = int(text) if text.isdigit() else 0
    if not 1 <= month <= 12:
        raise CardBatchParseError(f"有效期月份无效：{raw}")
    return f"{month:02d}"


def _normalize_exp_year(raw: Any) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 4:
        return digits[-2:]
    if len(digits) == 2:
        return digits
    raise CardBatchParseError(f"有效期年份无效：{raw}")


def _balance(raw: Any, fallback: Decimal) -> Decimal:
    if raw in (None, ""):
        return fallback
    try:
        value = Decimal(str(raw).replace("$", "").replace(",", "").strip())
    except InvalidOperation as exc:
        raise CardBatchParseError(f"余额无法解析：{raw}") from exc
    if value < MIN_SANE_BALANCE:
        raise CardBatchParseError(f"余额不能为负：{raw}")
    return value


def _cvv(raw: Any) -> str:
    cvv = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(cvv) not in (3, 4):
        raise CardBatchParseError(f"CVV 应为 3-4 位：{raw}")
    return cvv


def parse_batch_cards(
    raw_text: str,
    default_balance: Decimal = Decimal("25.00"),
    encrypt: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Return card dicts ready for the ``VccCard`` model.

    ``encrypt`` defaults to identity so the parser stays unit-testable.
    """
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return []
    protect = encrypt or (lambda value: value)

    if cleaned[0] in "[{":
        rows = _parse_json(cleaned, default_balance, protect)
    else:
        rows = _parse_lines(cleaned, default_balance, protect)
    if not rows:
        raise CardBatchParseError("没有解析到任何卡片")
    return _dedupe(rows)


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeats of the same PAN inside one paste.

    Issuer exports often repeat a card, and two rows for one card both carry the
    full balance, so the scheduler happily double-spends it.
    """
    seen: dict[str, int] = {}
    unique: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        pan = row["pan"]
        if pan in seen:
            row["duplicate_of"] = seen[pan]
            continue
        seen[pan] = index
        unique.append(row)
    return unique


def _parse_json(raw: str, default_balance: Decimal, protect: Callable[[str], str]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CardBatchParseError(f"JSON 格式错误：{exc.msg}") from exc
    if isinstance(payload, dict):
        payload = payload.get("cards") or [payload]
    if not isinstance(payload, list) or not payload:
        raise CardBatchParseError("JSON 应为卡片数组")

    parsed = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise CardBatchParseError(f"第 {index} 项不是对象")
        try:
            parsed.append(
                _row(
                    pan=item.get("card_number") or item.get("pan") or item.get("number"),
                    month=item.get("exp_month") or item.get("month"),
                    year=item.get("exp_year") or item.get("year"),
                    cvv=item.get("cvv") or item.get("cvc"),
                    balance=item.get("available_balance", item.get("balance")),
                    zip_code=item.get("billing_zip") or item.get("zip"),
                    default_balance=default_balance,
                    protect=protect,
                    holder=item.get("holder_name"),
                )
            )
        except CardBatchParseError as exc:
            raise CardBatchParseError(f"第 {index} 项：{exc}") from exc
    return parsed


def _sniff_delimiter(raw: str) -> str:
    """Pick the separator from the first data line (tab exports are common)."""
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        counts = {delimiter: line.count(delimiter) for delimiter in ("\t", ";", ",")}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","
    return ","


def _parse_lines(raw: str, default_balance: Decimal, protect: Callable[[str], str]) -> list[dict[str, Any]]:
    parsed = []
    delimiter = _sniff_delimiter(raw)
    for line_no, parts in enumerate(csv.reader(io.StringIO(raw), delimiter=delimiter), start=1):
        cells = [cell.strip() for cell in parts]
        first = cells[0] if cells else ""
        if not first or first.startswith("#") or first.lower().rstrip(":") in {"pan", "card_number", "卡号"}:
            continue
        cells = [cell for cell in cells if cell]
        if len(cells) < 4:
            raise CardBatchParseError(f"第 {line_no} 行字段不足（需要 卡号,月,年,CVV）")
        try:
            parsed.append(
                _row(
                    pan=cells[0],
                    month=cells[1],
                    year=cells[2],
                    cvv=cells[3],
                    balance=cells[4] if len(cells) >= 5 else None,
                    zip_code=cells[5] if len(cells) >= 6 else None,
                    default_balance=default_balance,
                    protect=protect,
                )
            )
        except CardBatchParseError as exc:
            raise CardBatchParseError(f"第 {line_no} 行：{exc}") from exc
    return parsed


def _row(
    *,
    pan: Any,
    month: Any,
    year: Any,
    cvv: Any,
    balance: Any,
    zip_code: Any,
    default_balance: Decimal,
    protect: Callable[[str], str],
    holder: Any = None,
) -> dict[str, Any]:
    digits = _clean_pan(pan)
    if not luhn_valid(digits):
        raise CardBatchParseError(f"卡号未通过 Luhn 校验：{digits[-4:]}")
    return {
        "pan": digits,
        "card_bin": digits[:6],
        "last4": digits[-4:],
        "card_number_enc": protect(digits),
        "cvv_enc": protect(_cvv(cvv)),
        "exp_month": _normalize_exp_month(month),
        "exp_year": _normalize_exp_year(year),
        "holder_name": (str(holder).strip() if holder else "") or "MOLIPLY USER",
        "billing_zip": (str(zip_code).strip() if zip_code else "") or DEFAULT_BILLING_ZIP,
        "available_balance": _balance(balance, default_balance),
        "reserved_balance": Decimal("0.00"),
    }
