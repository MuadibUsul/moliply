from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

KEY = "test-key"
SECRET = "test-secret"


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("UPSTREAM_KEY", KEY)
    monkeypatch.setenv("UPSTREAM_SECRET", SECRET)
    monkeypatch.setenv("UPSTREAM_ADMIN_TOKEN", "admin-token")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "up.db"))
    monkeypatch.setenv("FULFILL_MODE", "manual")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    for name in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[name]
    module = importlib.import_module("app.main")
    with TestClient(module.app) as client:
        yield client, module


def signed(client, path, body="", method="POST", ts_offset=0, headers=None):
    import hashlib
    import hmac

    stamp = str(int(time.time()) + ts_offset)
    sig = hmac.new(SECRET.encode(), f"{stamp}.{body}".encode(), hashlib.sha256).hexdigest()
    head = {"x-merchant-key": KEY, "x-timestamp": stamp, "x-signature": sig, "content-type": "application/json"}
    head.update(headers or {})
    return client.request(method, path, content=body, headers=head)


def payload(session="session-abc", sku="CHATGPT_PLUS_1M"):
    return json.dumps({"session_raw": session, "sku_type": sku, "merchant": "MOLIPLY", "timestamp": 0})


def test_rejects_missing_signature(service):
    client, _ = service
    assert client.post("/api/v1/recharge", content=payload()).status_code == 401


def test_rejects_wrong_signature(service):
    client, _ = service
    response = signed(client, "/api/v1/recharge", payload(), headers={"x-signature": "deadbeef"})
    assert response.status_code == 401


def test_rejects_stale_timestamp(service):
    client, _ = service
    assert signed(client, "/api/v1/recharge", payload(), ts_offset=-10_000).status_code == 401


def test_accepts_signed_request_and_parks_it(service):
    client, _ = service
    response = signed(client, "/api/v1/recharge", payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["order_no"].startswith("UP")


def test_same_session_reuses_pending_order(service):
    client, _ = service
    first = signed(client, "/api/v1/recharge", payload()).json()
    second = signed(client, "/api/v1/recharge", payload()).json()
    assert first["order_no"] == second["order_no"]


def test_different_session_gets_a_new_order(service):
    client, _ = service
    first = signed(client, "/api/v1/recharge", payload("a")).json()
    second = signed(client, "/api/v1/recharge", payload("b")).json()
    assert first["order_no"] != second["order_no"]


def test_status_requires_signature(service):
    client, _ = service
    order = signed(client, "/api/v1/recharge", payload()).json()["order_no"]
    assert client.get(f"/api/v1/recharge/{order}").status_code == 401
    ok = signed(client, f"/api/v1/recharge/{order}", method="GET")
    assert ok.status_code == 200 and ok.json()["status"] == "pending"


def test_unknown_order_is_404(service):
    client, _ = service
    assert signed(client, "/api/v1/recharge/NOPE", method="GET").status_code == 404


def test_operator_settles_order(service):
    client, _ = service
    order = signed(client, "/api/v1/recharge", payload()).json()["order_no"]
    admin = {"x-admin-token": "admin-token"}
    settled = client.post(f"/admin/api/orders/{order}/settle?status=success", headers=admin)
    assert settled.status_code == 200
    assert signed(client, f"/api/v1/recharge/{order}", method="GET").json()["status"] == "success"


def test_terminal_status_cannot_flip(service):
    client, _ = service
    order = signed(client, "/api/v1/recharge", payload()).json()["order_no"]
    admin = {"x-admin-token": "admin-token"}
    client.post(f"/admin/api/orders/{order}/settle?status=success", headers=admin)
    again = client.post(f"/admin/api/orders/{order}/settle?status=failed", headers=admin)
    assert again.json()["status"] == "success"


def test_admin_endpoints_reject_bad_token(service):
    client, _ = service
    assert client.get("/admin/api/orders", headers={"x-admin-token": "nope"}).status_code == 401


def test_stock_mode_claims_a_code(service, monkeypatch):
    client, module = service
    monkeypatch.setattr(module, "FULFILL_MODE", "stock")
    admin = {"x-admin-token": "admin-token"}
    client.post("/admin/api/stock?sku=CHATGPT_PLUS_1M", headers=admin, content="CODE-1\nCODE-2")

    first = signed(client, "/api/v1/recharge", payload("s1")).json()
    assert first["status"] == "success"
    second = signed(client, "/api/v1/recharge", payload("s2")).json()
    assert second["status"] == "success"
    assert second["message"] != first["message"]

    third = signed(client, "/api/v1/recharge", payload("s3")).json()
    assert third["status"] == "pending"

    stock = client.get("/admin/api/stock", headers=admin).json()
    assert stock["items"][0]["available"] == 0
    assert stock["items"][0]["used"] == 2


def test_healthz_reports_key_state(service):
    client, _ = service
    body = client.get("/healthz").json()
    assert body["keys"] is True


class FakeSupplier:
    name = "fake"
    status = "success"
    error = None

    def quote(self, sku, contact):
        return 1000

    def deliver(self, sku, session_raw, request_no, contact):
        from app.suppliers.base import SupplierResult

        if self.error:
            raise self.error("boom")
        return SupplierResult(self.status, "fake", "TRADE-1")

    def poll(self, trade_no, secret, sku, tries=1):
        from app.suppliers.base import SupplierResult

        if self.status == "pending":
            self.status = "success"
            return SupplierResult("success", "late")
        return SupplierResult(self.status, "")


@pytest.fixture()
def supplier_mode(service, monkeypatch):
    client, module = service
    from app.suppliers.base import SupplierRejected, SupplierUncertain

    monkeypatch.setattr(module, "FULFILL_MODE", "supplier")
    fake = FakeSupplier()
    monkeypatch.setattr(module, "build_supplier", lambda: fake)
    return client, module, fake, SupplierRejected, SupplierUncertain


def test_supplier_success_settles_the_order(supplier_mode):
    client, _, _, _, _ = supplier_mode
    body = signed(client, "/api/v1/recharge", payload()).json()
    assert body["status"] == "success"


def test_supplier_rejection_fails_the_order(supplier_mode):
    client, _, fake, rejected, _ = supplier_mode
    fake.error = rejected
    assert signed(client, "/api/v1/recharge", payload()).json()["status"] == "failed"


def test_supplier_uncertainty_stays_pending(supplier_mode):
    client, _, fake, _, uncertain = supplier_mode
    fake.error = uncertain
    body = signed(client, "/api/v1/recharge", payload()).json()
    assert body["status"] == "pending"
    assert "结果不明" in body["message"]


def test_pending_supplier_order_is_repolled(supplier_mode):
    client, _, fake, _, _ = supplier_mode
    fake.status = "pending"
    order = signed(client, "/api/v1/recharge", payload()).json()["order_no"]
    assert signed(client, "/api/v1/recharge/" + order, method="GET").json()["status"] == "success"


def test_healthz_reports_supplier_name(service, monkeypatch):
    client, module = service
    monkeypatch.setattr(module, "SUPPLIER_NAME", "yunshu")
    assert client.get("/healthz").json()["supplier"] == "yunshu"


# -- channel management ----------------------------------------------------
def test_supplier_config_roundtrip(service, monkeypatch):
    client, module = service
    monkeypatch.setattr(module, "SUPPLIER_NAME", "yunshu")
    body = json.dumps({"token": "ysu_secret_value", "sku_map": "CHATGPT_PLUS_1M=CODE1"})
    assert signed(client, "/api/v1/supplier", body).status_code == 200

    read = signed(client, "/api/v1/supplier", method="GET").json()
    assert read["configured"] is True
    assert read["source"] == "database"
    assert read["token_hint"] == "ysu_secr"
    assert read["sku_map"] == "CHATGPT_PLUS_1M=CODE1"


def test_supplier_config_requires_signature(service):
    client, _ = service
    assert client.get("/api/v1/supplier").status_code == 401


def test_empty_token_keeps_the_stored_one(service, monkeypatch):
    client, module = service
    monkeypatch.setattr(module, "SUPPLIER_NAME", "yunshu")
    signed(client, "/api/v1/supplier", json.dumps({"token": "ysu_keep"}))
    signed(client, "/api/v1/supplier", json.dumps({"token": "", "catalog_race": "PH"}))
    read = signed(client, "/api/v1/supplier", method="GET").json()
    assert read["token_hint"] == "ysu_keep"
    assert read["catalog_race"] == "PH"


def test_build_supplier_prefers_the_database_row(service, monkeypatch):
    client, module = service
    monkeypatch.setattr(module, "SUPPLIER_NAME", "yunshu")
    monkeypatch.setenv("YUNSHU_TOKEN", "ysu_from_env")
    assert module.build_supplier().token == "ysu_from_env"

    signed(client, "/api/v1/supplier", json.dumps({"token": "ysu_from_db", "sku_map": "A=B"}))
    built = module.build_supplier()
    assert built.token == "ysu_from_db"
    assert built.sku_map == {"A": "B"}


def test_probe_rejects_an_unconfigured_supplier(service, monkeypatch):
    client, module = service
    monkeypatch.setattr(module, "SUPPLIER_NAME", "yunshu")
    monkeypatch.setenv("YUNSHU_TOKEN", "")
    assert signed(client, "/api/v1/supplier/probe", "{}").status_code == 400
