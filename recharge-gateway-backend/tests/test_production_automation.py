"""Production-grade commercial automation, concurrency & security regression suite."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.config import settings
from app.models import (
    ActivationCode,
    ChannelMode,
    CodeStatus,
    Coupon,
    DeliveryType,
    FreeAccountPool,
    FreeAccountStatus,
    FulfillmentStrategy,
    JitAccountJob,
    JitJobState,
    Order,
    OrderItem,
    OrderStatus,
    PaymentEvent,
    Product,
    ProductCategory,
    ProductVariant,
    RechargeState,
    RechargeTask,
    SkuType,
    VccCard,
    VccStatus,
    utcnow,
)
from app.security import client_ip, create_admin_token, decode_admin_token, revoke_admin_token
from app.services import automation, mailer, net_guard, precheck, region_router, runtime_settings
from app.services import orders as order_service
from app.services import sms as sms_service
from app.services.chains import trc20_watcher
from app.services.code_generator import generate_code
from app.services.crypto import encrypt_text
from app.services.fulfillment import cce_delivery, jit_delivery, vcc_pool_scheduler
from app.services.sms import providers as sms_providers


def _make_vcc(db, *, balance=100, ph=True, daily_limit=None, failed=0, consecutive=0, cooldown_until=None, locked=False):
    card = VccCard(
        provider="RDVCC",
        card_bin="424242",
        card_number_enc=encrypt_text("4242424242424242"),
        cvv_enc=encrypt_text("123"),
        exp_month="12",
        exp_year="29",
        holder_name="AUTO TEST",
        billing_address_json={"line1": "1 Main", "city": "NY", "state": "NY", "postal_code": "10001", "country": "US"},
        ph_billing_address_json=(
            {"line1": "1 Ayala", "city": "Makati", "state": "MM", "postal_code": "1200", "country": "PH"}
            if ph
            else None
        ),
        available_balance=Decimal(str(balance)),
        reserved_balance=Decimal("0.00"),
        daily_limit=daily_limit,
        max_bind_count=2,
        current_bind_count=0,
        failed_count=failed,
        consecutive_failures=consecutive,
        cooldown_until=cooldown_until,
        is_locked=locked,
        locked_at=utcnow() - timedelta(minutes=30) if locked else None,
        status=VccStatus.ACTIVE,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def test_region_router_us_pro_uses_usd_amounts():
    cur, amt = region_router.amount_for(region_router.US, SkuType.GPT_PRO_100)
    assert cur == "USD"
    assert amt == Decimal("100.0")
    cur2, amt2 = region_router.amount_for(region_router.US, SkuType.GPT_PRO_200)
    assert cur2 == "USD"
    assert amt2 == Decimal("200.0")
    cur3, amt3 = region_router.amount_for(region_router.US, SkuType.GPT_PRO_500)
    assert cur3 == "USD"
    assert amt3 == Decimal("500.0")


def test_trc20_watcher_continues_past_already_seen_tx_with_same_amount(db, monkeypatch):
    wallet = "T" + "Z" * 33
    contract = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    runtime_settings.update_settings(
        db,
        {
            "crypto_enabled": True,
            "crypto_provider": "usdt_trc20",
            "usdt_trc20_wallet_address": wallet,
            "usdt_trc20_contract": contract,
        },
    )
    order = Order(
        order_no=f"MOL{uuid4().hex[:10].upper()}",
        email="chain@example.com",
        amount=Decimal("100.00"),
        status=OrderStatus.PENDING,
        payment_provider="usdt_trc20",
        payment_currency="USDT",
        payment_amount=Decimal("13.89"),
        payment_address=wallet,
        expires_at=utcnow() + timedelta(minutes=30),
    )
    db.add(order)
    db.add(
        PaymentEvent(
            provider="usdt_trc20",
            event_id="tx-old-seen",
            order_no="MOLOLD",
            amount=Decimal("13.89"),
            verified=True,
            raw_json={},
        )
    )
    db.commit()

    now_ms = int(utcnow().replace(tzinfo=timezone.utc).timestamp() * 1000)

    def fake_fetch(*_args, **_kwargs):
        return [
            {
                "transaction_id": "tx-old-seen",
                "token_info": {"address": contract, "decimals": 6},
                "to": wallet,
                "from": "T" + "B" * 33,
                "type": "Transfer",
                "confirmed": True,
                "value": "13890000",
                "block_timestamp": now_ms,
            },
            {
                "transaction_id": "tx-new-legit",
                "token_info": {"address": contract, "decimals": 6},
                "to": wallet,
                "from": "T" + "C" * 33,
                "type": "Transfer",
                "confirmed": True,
                "value": "13890000",
                "block_timestamp": now_ms,
            },
        ]

    monkeypatch.setattr(trc20_watcher, "_fetch_transfers", fake_fetch)
    assert trc20_watcher.scan_pending_orders(db) == 1
    db.expire_all()
    refreshed = db.get(Order, order.id)
    assert refreshed.status in {OrderStatus.PAID, OrderStatus.DELIVERED}
    assert refreshed.payment_trade_no == "tx-new-legit"


def test_vcc_scheduler_locks_exclusively_reuses_cooled_cards_and_recovers_stale_locks(db):
    db.query(VccCard).update({VccCard.status: VccStatus.SUSPENDED})
    db.commit()

    cooled = _make_vcc(db, balance=60, failed=1, consecutive=1, cooldown_until=utcnow() - timedelta(minutes=1))
    acquired = vcc_pool_scheduler.acquire_card(db, region_router.US, amount=Decimal("22.00"))
    assert acquired is not None and acquired.id == cooled.id
    # Locked card must not be double-acquired concurrently.
    second = vcc_pool_scheduler.acquire_card(db, region_router.US, amount=Decimal("22.00"))
    assert second is None

    # Simulate worker crash leaving card locked > 15m; reaper must recover it.
    acquired.locked_at = utcnow() - timedelta(minutes=25)
    db.add(acquired)
    db.commit()
    assert automation.recover_stale_vcc_locks(db, stale_minutes=15) == 1
    db.expire_all()
    recovered = db.get(VccCard, cooled.id)
    assert recovered.is_locked is False
    assert recovered.reserved_balance == Decimal("0.00")


def test_sms_unparsed_text_still_consumes_code_and_5sim_slugs(db, monkeypatch):
    code = ActivationCode(
        code=generate_code(),
        sku_type=SkuType.SMS_VERIFY,
        channel_mode=ChannelMode.UPSTREAM,
        status=CodeStatus.UNUSED,
    )
    db.add(code)
    db.commit()

    class UnparsedProvider:
        name = "smspool"

        def configured(self):
            return True

        def lease_number(self, service, country):
            return sms_providers.LeasedNumber("ord-1", "+14155550101", self.name)

        def poll_code(self, lease):
            return sms_providers.ReceivedSms(True, None, "Your magic link is https://example.com/verify")

        def cancel(self, lease):
            return True

    prov = UnparsedProvider()
    monkeypatch.setattr(sms_providers, "choose_provider", lambda *_a, **_k: prov)
    monkeypatch.setattr(sms_providers, "build_provider", lambda _n: prov)
    monkeypatch.setattr(sms_providers, "available_providers", lambda: ["smspool"])

    # Precheck must also accept SMS_VERIFY without a session.
    pre = precheck.precheck_session(code, "")
    assert pre.ok is True and pre.requires_session is False

    task = sms_service.lease(db, code, "claude", "US")
    polled = sms_service.poll(db, task.task_id)
    db.expire_all()
    assert polled.status.value == "RECEIVED"
    assert db.get(ActivationCode, code.id).status == CodeStatus.USED
    assert sms_providers.FiveSimProvider.COUNTRY_SLUGS["US"] == "usa"
    assert sms_providers.FiveSimProvider.COUNTRY_SLUGS["GB"] == "england"


def test_admin_jwt_revocation_on_logout_and_client_ip_spoofing_protection(client):
    token, _ = create_admin_token("admin")
    headers = {"Authorization": f"Bearer {token}"}
    assert decode_admin_token(token)["role"] == "admin"
    assert client.get("/api/v1/admin/metrics", headers=headers).status_code == 200
    res = client.post("/api/v1/admin/logout", headers=headers)
    assert res.status_code == 200
    assert client.get("/api/v1/admin/metrics", headers=headers).status_code == 401

    # Fresh token still works and revocation helper works directly.
    fresh, _ = create_admin_token("admin")
    assert decode_admin_token(fresh)["sub"] == "admin"
    revoke_admin_token(fresh)
    with pytest.raises(Exception):
        decode_admin_token(fresh)

    # Untrusted direct client cannot spoof X-Forwarded-For.
    spoof_req = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"1.2.3.4")],
            "client": ("198.51.100.20", 12345),
        }
    )
    assert client_ip(spoof_req) == "198.51.100.20"
    # Trusted reverse proxy hop extracts rightmost untrusted client IP.
    proxied_req = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"9.9.9.9, 203.0.113.55")],
            "client": ("172.18.0.2", 443),
        }
    )
    assert client_ip(proxied_req) == "203.0.113.55"


def test_production_config_guard_and_ssrf_block(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "admin_password", "change-me")
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        settings.validate_production_config()

    with pytest.raises(ValueError):
        net_guard.ensure_https("https://169.254.169.254/latest/meta-data/")
    with pytest.raises(ValueError):
        net_guard.ensure_https("https://127.0.0.1/internal")


def test_mailer_parses_display_name_envelope_cleanly():
    addr, header = mailer._resolve_from()
    assert "@" in addr and "<" not in addr and ">" not in addr
    assert addr in header


def test_order_expiry_late_payment_recovery_coupon_limit_and_refund(client, db, admin_headers):
    # Create a max_uses=1 coupon via new commercial Coupon CRUD API.
    code_str = f"ONCE{uuid4().hex[:6].upper()}"
    c_res = client.post(
        "/api/v1/admin/coupons",
        json={"code": code_str, "amount": 0.5, "min_amount": 0, "max_uses": 1, "active": True},
        headers=admin_headers,
    )
    assert c_res.status_code == 201, c_res.text

    product = db.query(Product).filter(Product.active.is_(True), Product.delivery_type == DeliveryType.ACTIVATION_CODE).first()
    assert product is not None
    sku = product.variants[0].sku_type
    # Ensure at least one unused code exists.
    db.add(ActivationCode(code=generate_code(), sku_type=sku, channel_mode=ChannelMode.UPSTREAM, status=CodeStatus.UNUSED))
    db.commit()

    def _place(email_prefix: str):
        return client.post(
            "/api/v1/store/orders",
            json={
                "email": f"{email_prefix}-{uuid4().hex[:6]}@example.com",
                "password": "StrongPass!2026",
                "items": [{"product_id": product.id, "variant_id": product.variants[0].id, "quantity": 1}],
                "coupon_code": code_str,
                "payment_method": "wechat",
            },
        )

    first = _place("buyer1")
    assert first.status_code == 201, first.text
    # Second parallel pending order must be rejected because max_uses=1 is reserved.
    second = _place("buyer2")
    assert second.status_code == 400

    order_no = first.json()["order"]["order_no"]
    order = db.query(Order).filter(Order.order_no == order_no).one()
    # Expire it via automation sweeper.
    order.expires_at = utcnow() - timedelta(minutes=5)
    db.add(order)
    db.commit()
    assert automation.expire_unpaid_orders(db) >= 1
    db.expire_all()
    order = db.query(Order).filter(Order.order_no == order_no).one()
    assert order.status == OrderStatus.CANCELLED

    # Late verified payment must recover and deliver automatically instead of raising 400!
    assert order_service.mark_order_paid(db, order, "alipay_f2f", f"LATE-{uuid4().hex[:8]}") is True
    db.expire_all()
    order = db.query(Order).filter(Order.order_no == order_no).one()
    assert order.status == OrderStatus.DELIVERED

    # Refund via commercial admin API must disable the unused activation code.
    ref = client.post(f"/api/v1/admin/orders/{order_no}/refund", json={"reason": "test refund"}, headers=admin_headers)
    assert ref.status_code == 200, ref.text
    assert ref.json()["order"]["status"] == "REFUNDED"
    assert ref.json()["revoked_codes"] == 1


def test_backorder_auto_fulfills_on_code_generation(client, db, admin_headers):
    sku = SkuType.API_CREDIT_100
    # Clear existing codes for this SKU to force backorder.
    db.query(ActivationCode).filter(ActivationCode.sku_type == sku).delete()
    product = Product(
        slug=f"backorder-test-{uuid4().hex[:6]}",
        name="Backorder Test SKU",
        subtitle="test",
        category=ProductCategory.API,
        brand="MOLIPLY",
        logo_key="api",
        delivery_type=DeliveryType.ACTIVATION_CODE,
        price=Decimal("50.00"),
        active=True,
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id, label="标准", months=1, price=Decimal("50.00"), sku_type=sku, active=True
    )
    db.add(variant)
    db.commit()

    order = order_service.create_order(
        db,
        email=f"bo-{uuid4().hex[:6]}@example.com",
        items=[type("I", (), {"product_id": product.id, "variant_id": variant.id, "quantity": 1})()],
        coupon_code=None,
        client_ip="127.0.0.1",
    )
    order_service.mark_order_paid(db, order, "mock_wechat", f"BO-{uuid4().hex[:6]}")
    db.expire_all()
    order = db.get(Order, order.id)
    assert order.status == OrderStatus.PAID and order.stock_pending is True

    # Restocking via admin_generate_codes must immediately auto-fulfill the backorder!
    gen = client.post(
        "/api/v1/admin/codes/generate",
        json={"sku_type": sku.value, "count": 2, "channel_mode": "UPSTREAM"},
        headers=admin_headers,
    )
    assert gen.status_code == 200, gen.text
    assert gen.json()["auto_fulfilled"] >= 1
    db.expire_all()
    order = db.get(Order, order.id)
    assert order.status == OrderStatus.DELIVERED and order.stock_pending is False


def test_jit_multi_quantity_creates_matching_job_count(db):
    product = Product(
        slug=f"jit-multi-{uuid4().hex[:6]}",
        name="JIT Multi",
        subtitle="jit",
        category=ProductCategory.AI_MEMBERSHIP,
        brand="OpenAI",
        logo_key="gpt",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        fulfillment_strategy=FulfillmentStrategy.JIT_ACCOUNT_RECHARGE,
        price=Decimal("100.00"),
        active=True,
    )
    db.add(product)
    db.flush()
    order = Order(
        order_no=f"MOL{uuid4().hex[:10].upper()}",
        email="jit@example.com",
        amount=Decimal("200.00"),
        status=OrderStatus.PAID,
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        product_name=product.name,
        spec_label="1M",
        unit_price=Decimal("100.00"),
        quantity=2,
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        sku_type=SkuType.CHATGPT_PLUS_1M,
    )
    db.add(item)
    db.commit()

    jit_delivery.start_jit_delivery(db, item)
    jobs = db.query(JitAccountJob).filter(JitAccountJob.order_item_id == item.id).all()
    assert len(jobs) == 2


def test_cce_automated_delivery_bridge_mints_key(db, tmp_path, monkeypatch):
    cce_db = tmp_path / "cce.sqlite3"
    cce_secrets = tmp_path / "cce.secrets"
    schema_sql = (Path(__file__).resolve().parent.parent / "cce_console" / "db" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(str(cce_db))
    conn.executescript(schema_sql)
    conn.execute(
        """
        INSERT INTO profiles (profile_id, local_proxy_port, upstream_proxy_url, expected_ip, os_type, noise_seed, fingerprint_json, claude_credentials_enc, status)
        VALUES ('claude-auto-1', 10001, 'socks5://127.0.0.1:9050', '1.2.3.4', 'windows', 42, '{}', ?, 'IDLE')
        """,
        (b"sealed-creds",),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CCE_DB_PATH", str(cce_db))
    monkeypatch.setenv("CCE_TUNNEL_SECRETS", str(cce_secrets))

    assert cce_delivery.available_cce_profiles() == 1
    minted = cce_delivery.claim_and_mint_key(plan_tier="pro", valid_days=30)
    assert minted is not None
    assert minted["profile_id"] == "claude-auto-1"
    assert minted["card_key"].startswith("cce_")
    assert cce_secrets.exists() and minted["card_key"] in cce_secrets.read_text(encoding="utf-8")
    # Second claim sees 0 remaining unassigned profiles.
    assert cce_delivery.available_cce_profiles() == 0
    assert cce_delivery.claim_and_mint_key() is None


def test_readyz_and_admin_metrics_and_cce_diagnose_ip_drift(client, admin_headers, monkeypatch):
    assert client.get("/readyz").status_code == 200
    m = client.get("/api/v1/admin/metrics", headers=admin_headers)
    assert m.status_code == 200
    assert "attention_count" in m.json()

    from cce_console.core.proxy_inspector import ProxyGeoInfo, ProxyInspector

    class DummyDB:
        pass

    inspector = ProxyInspector(DummyDB())

    async def fake_inspect(proxy_url, expected_ip=None):
        assert expected_ip is None
        return ProxyGeoInfo(
            ip="203.0.113.99",
            country_code="US",
            city="New York",
            timezone="America/New_York",
            latitude=40.7,
            longitude=-74.0,
            locale="en-US",
            isp="Comcast",
            asn="AS7922",
            is_proxy=False,
            is_hosting=False,
        )

    monkeypatch.setattr(inspector, "inspect", fake_inspect)
    verdict = asyncio.run(inspector.diagnose("socks5://user:pass@1.2.3.4:1080", "198.51.100.1"))
    assert verdict["ok"] is True
    assert verdict["reason"] == "ip_drift"
    assert verdict["ip"] == "203.0.113.99"


# ---------------------------------------------------------------------------
# CCE delivery bridge: HTTP transport
# ---------------------------------------------------------------------------


class _ctx:
    """Minimal stand-in for the redis_lock context manager."""

    def __init__(self, acquired):
        self.acquired = acquired

    def __enter__(self):
        return self.acquired

    def __exit__(self, *_exc):
        return False


def _cce_env(monkeypatch, base="http://cce.test:8010", token="tok"):
    monkeypatch.setenv("CCE_API_BASE", base)
    monkeypatch.setenv("CCE_ADMIN_TOKEN", token)


def _profile(**overrides):
    row = {
        "profile_id": "p-1",
        "status": "IDLE",
        "has_credentials": True,
        "active_key": False,
        "created_at": "2026-11-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_http_bridge_counts_only_deliverable_profiles(monkeypatch):
    from app.services.fulfillment import cce_delivery

    _cce_env(monkeypatch)
    profiles = [
        _profile(profile_id="ok"),
        _profile(profile_id="busy", status="BUSY"),
        _profile(profile_id="nokey", has_credentials=False),
        _profile(profile_id="taken", active_key=True),
    ]

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"profiles": profiles}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, path):
            assert path == "/admin/profiles"
            return FakeResponse()

    monkeypatch.setattr(cce_delivery, "_http_client", lambda: FakeClient())
    assert cce_delivery.available_cce_profiles() == 1


def test_http_bridge_mints_for_the_oldest_deliverable_profile(monkeypatch):
    from app.services.fulfillment import cce_delivery

    _cce_env(monkeypatch)
    profiles = [
        _profile(profile_id="newer", created_at="2026-11-10T00:00:00+00:00"),
        _profile(profile_id="older", created_at="2026-11-01T00:00:00+00:00"),
        _profile(profile_id="busy", status="BUSY"),
    ]
    minted_body = {}

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, _path):
            return FakeResponse({"profiles": profiles})

        def post(self, path, json):
            assert path == "/admin/keys"
            minted_body.update(json)
            return FakeResponse(
                {"card_key": "cce_abc", "tunnel_secret": "sec", "expires_at": "2026-12-21T00:00:00+00:00"}
            )

    monkeypatch.setattr(cce_delivery, "_http_client", lambda: FakeClient())
    monkeypatch.setattr(cce_delivery, "redis_lock", lambda *a, **k: _ctx(True), raising=False)

    result = cce_delivery.claim_and_mint_key(valid_days=30)
    assert minted_body["profile_id"] == "older"
    assert result["profile_id"] == "older"
    assert result["card_key"] == "cce_abc"
    assert "TUNNEL_SECRET=sec" in result["payload"]


def test_http_bridge_returns_none_when_pool_is_empty(monkeypatch):
    from app.services.fulfillment import cce_delivery

    _cce_env(monkeypatch)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"profiles": [_profile(status="BUSY")]}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, _path):
            return FakeResponse()

    monkeypatch.setattr(cce_delivery, "_http_client", lambda: FakeClient())
    monkeypatch.setattr(cce_delivery, "redis_lock", lambda *a, **k: _ctx(True), raising=False)
    assert cce_delivery.claim_and_mint_key() is None


def test_bridge_falls_back_to_sqlite_without_http_env(monkeypatch):
    from app.services.fulfillment import cce_delivery

    monkeypatch.delenv("CCE_API_BASE", raising=False)
    monkeypatch.delenv("CCE_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("CCE_DB_PATH", "/nonexistent/cce.sqlite3")
    assert cce_delivery.available_cce_profiles() == 0
    assert cce_delivery.claim_and_mint_key() is None


def test_http_bridge_prevents_double_sell_when_active_key_omitted(monkeypatch):
    """Older CCE /admin/profiles omits active_key; bridge must verify /admin/keys/{id} and fail closed."""
    from app.services.fulfillment import cce_delivery

    _cce_env(monkeypatch)
    # Deliberately omit "active_key" from all profile rows (matches unpatched CCE)
    profiles = [
        {
            "profile_id": "already-sold",
            "status": "IDLE",
            "has_credentials": True,
            "created_at": "2026-10-01T00:00:00+00:00",
        },
        {
            "profile_id": "keys-error",
            "status": "IDLE",
            "has_credentials": True,
            "created_at": "2026-10-02T00:00:00+00:00",
        },
        {
            "profile_id": "reusable-expired",
            "status": "IDLE",
            "has_credentials": True,
            "created_at": "2026-10-03T00:00:00+00:00",
        },
    ]
    minted_ids: list[str] = []

    class FakeResp:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

        def json(self):
            return self._payload

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, path):
            if path == "/admin/profiles":
                return FakeResp({"profiles": profiles})
            if path == "/admin/keys/already-sold":
                return FakeResp(
                    {"keys": [{"card_key": "cce_live", "is_active": 1, "expires_at": "2099-01-01T00:00:00+00:00"}]}
                )
            if path == "/admin/keys/keys-error":
                return FakeResp({}, status_code=500)
            if path == "/admin/keys/reusable-expired":
                return FakeResp(
                    {
                        "keys": [
                            {"card_key": "cce_old", "is_active": 1, "expires_at": "2020-01-01T00:00:00+00:00"},
                            {"card_key": "cce_revoked", "is_active": 0, "expires_at": "2099-01-01T00:00:00+00:00"},
                        ]
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

        def post(self, path, json):
            assert path == "/admin/keys"
            minted_ids.append(json["profile_id"])
            return FakeResp(
                {"card_key": "cce_fresh", "tunnel_secret": "sec_fresh", "expires_at": "2099-02-01T00:00:00+00:00"}
            )

    monkeypatch.setattr(cce_delivery, "_http_client", lambda: FakeClient())
    monkeypatch.setattr(cce_delivery, "redis_lock", lambda *a, **k: _ctx(True), raising=False)

    assert cce_delivery.available_cce_profiles() == 1
    claimed = cce_delivery.claim_and_mint_key(plan_tier="pro", valid_days=30)
    assert claimed is not None
    assert claimed["profile_id"] == "reusable-expired"
    assert minted_ids == ["reusable-expired"]


def test_cce_console_admin_profiles_exposes_active_key_and_launcher_bundle_reuses_key(tmp_path, monkeypatch):
    """CCE /admin/profiles must expose active_key, ignore 1-byte creds, and /launcher/bundle must not re-mint."""
    import base64
    import io
    import json
    import zipfile

    from fastapi.testclient import TestClient

    from cce_console.api import app as cce_app_mod
    from cce_console.db.database import Database

    db_path = tmp_path / "cce.sqlite3"
    client_dir = tmp_path / "clients"
    client_dir.mkdir()
    (client_dir / "claude-box-windows-x64.exe").write_bytes(b"MZ-fake-win-bin")
    (client_dir / "claude-box").write_bytes(b"ELF-fake-linux-bin")
    (client_dir / "tls.crt").write_bytes(b"-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")

    monkeypatch.setenv("CCE_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("CCE_ADMIN_TOKEN", "cce-test-admin-token")
    monkeypatch.setenv("CCE_TUNNEL_SECRETS", str(tmp_path / "cce.secrets"))
    monkeypatch.setattr(cce_app_mod, "CLIENT_DIR", client_dir)
    monkeypatch.setattr(cce_app_mod, "TUNNEL_HOST", "moliply.shop")
    monkeypatch.setattr(cce_app_mod, "TUNNEL_PORT", 8444)

    async def _seed():
        db = Database(db_path)
        await db.connect()
        try:
            await db.insert_profile(
                {
                    "profile_id": "probe-empty",
                    "batch_id": None,
                    "local_proxy_port": 10001,
                    "upstream_proxy_url": "socks5://127.0.0.1:9001",
                    "expected_ip": "1.1.1.1",
                    "os_type": "windows",
                    "noise_seed": 1,
                    "fingerprint_json": "{}",
                }
            )
            await db.save_credentials("probe-empty", b"\x00")
            await db.insert_profile(
                {
                    "profile_id": "real-account",
                    "batch_id": None,
                    "local_proxy_port": 10002,
                    "upstream_proxy_url": "socks5://127.0.0.1:9002",
                    "expected_ip": "1.1.1.2",
                    "os_type": "windows",
                    "noise_seed": 2,
                    "fingerprint_json": "{}",
                }
            )
            await db.save_credentials("real-account", b"real-encrypted-credentials-blob")
        finally:
            await db.close()

    asyncio.run(_seed())
    monkeypatch.setenv("CCE_DB_PATH", str(db_path))
    monkeypatch.setattr(cce_app_mod, "DB_PATH", db_path)

    test_app = cce_app_mod.create_app()
    headers = {"x-admin-token": "cce-test-admin-token"}
    with TestClient(test_app) as cce_client:
        res = cce_client.get("/admin/profiles", headers=headers)
        assert res.status_code == 200
        by_id = {p["profile_id"]: p for p in res.json()["profiles"]}
        assert by_id["probe-empty"]["has_credentials"] is False
        assert by_id["real-account"]["has_credentials"] is True
        assert by_id["real-account"]["active_key"] is None

        minted = cce_client.post(
            "/admin/keys",
            headers=headers,
            json={"profile_id": "real-account", "plan_tier": "pro", "valid_days": 30},
        )
        assert minted.status_code == 201
        key_data = minted.json()

        res2 = cce_client.get("/admin/profiles", headers=headers)
        by_id2 = {p["profile_id"]: p for p in res2.json()["profiles"]}
        assert by_id2["real-account"]["active_key"] == key_data["card_key"]

        # Customer bundle download reuses the exact same key and does NOT mint another key
        bundle = cce_client.get(
            "/launcher/bundle?os_name=linux",
            headers={
                "authorization": f"Bearer {key_data['card_key']}",
                "x-tunnel-secret": key_data["tunnel_secret"],
            },
        )
        assert bundle.status_code == 200
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as zf:
            names = set(zf.namelist())
            assert {"cce.json", "gateway-ca.pem", "claude-box", "请先看我.txt"}.issubset(names)
            manifest = json.loads(zf.read("cce.json").decode("utf-8"))
            assert manifest["key"] == key_data["card_key"]
            assert manifest["secret"] == key_data["tunnel_secret"]
            assert (zf.getinfo("claude-box").external_attr >> 16) & 0o111 == 0o111

        keys_after = cce_client.get("/admin/keys/real-account", headers=headers).json()["keys"]
        assert len(keys_after) == 1

        # Workbench: admin bundle with card_key reuses the delivered key (no second key)
        admin_bundle = cce_client.get(
            f"/admin/bundle/real-account?os_name=windows&card_key={key_data['card_key']}",
            headers=headers,
        )
        assert admin_bundle.status_code == 200
        with zipfile.ZipFile(io.BytesIO(admin_bundle.content)) as zf:
            assert json.loads(zf.read("cce.json").decode("utf-8"))["key"] == key_data["card_key"]
        assert len(cce_client.get("/admin/keys/real-account", headers=headers).json()["keys"]) == 1
        bad_key = cce_client.get(
            "/admin/bundle/real-account?os_name=windows&card_key=cce_not_real", headers=headers
        )
        assert bad_key.status_code == 404

        # Workbench status exposes published client files
        status_body = cce_client.get("/admin/status", headers=headers).json()
        assert "tls.crt" in status_body["clients"]

        # Workbench: deleting an account with a live customer key is refused unless forced
        refused = cce_client.delete("/admin/profiles/real-account", headers=headers)
        assert refused.status_code == 409
        assert cce_client.delete("/admin/profiles/probe-empty", headers=headers).status_code == 200
        assert cce_client.delete("/admin/profiles/real-account?force=true", headers=headers).status_code == 200
        remaining = {p["profile_id"] for p in cce_client.get("/admin/profiles", headers=headers).json()["profiles"]}
        assert remaining == set()

        # Workbench: dead cards can be re-enabled and removed
        cce_client.post(
            "/admin/cards",
            headers=headers,
            json={"items": [{"pan": "4242424242424242", "exp_month": "12", "exp_year": "2030", "cvv": "123"}]},
        )
        card = cce_client.get("/admin/cards", headers=headers).json()["items"][0]
        reset = cce_client.post(f"/admin/cards/{card['id']}/reset", headers=headers)
        assert reset.status_code == 200 and reset.json()["card"]["status"] == "available"
        assert cce_client.delete(f"/admin/cards/{card['id']}", headers=headers).status_code == 200
        assert cce_client.get("/admin/cards", headers=headers).json()["summary"]["total"] == 0

        # Workbench HTML is served and carries the beginner flow
        ui = cce_client.get("/ui")
        assert ui.status_code == 200 and "Claude 账号工作台" in ui.text


def test_store_customer_cce_bundle_download_over_http(client, db, monkeypatch):
    """Storefront bundle endpoint authenticates owner, fetches assets over HTTP without host mounts, and zips cleanly."""
    import io
    import json
    import zipfile

    from app.models import DeliveryType, OrderDelivery, OrderItem, OrderStatus, Product
    from app.security import create_user_token
    from app.services import accounts
    from app.services.crypto import encrypt_text
    from app.services.fulfillment import cce_delivery

    owner, _ = accounts.get_or_create_user(db, "bundle-owner@example.com", "Pass123456!")
    other, _ = accounts.get_or_create_user(db, "bundle-other@example.com", "Pass123456!")
    product = db.query(Product).first()

    order = Order(
        order_no=f"MOLBUNDLE{uuid4().hex[:6].upper()}",
        user_id=owner.id,
        email=owner.email,
        status=OrderStatus.DELIVERED,
        amount=Decimal("99.00"),
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        product_name="Claude Pro 成品号",
        spec_label="30天独享",
        delivery_type=DeliveryType.ACCOUNT_CREDENTIALS,
        quantity=1,
        unit_price=Decimal("99.00"),
    )
    db.add(item)
    db.flush()
    payload_text = (
        "CCE_KEY=cce_customer_key_1\n"
        "TUNNEL_SECRET=secret_xyz_123\n"
        "PROFILE=claude-pro-01\n"
        "EXPIRES_AT=2026-12-31T00:00:00+00:00\n"
        "HOST=https://moliply.shop/cce\n"
    )
    cce_deliv = OrderDelivery(
        order_item_id=item.id,
        kind="CCE_DELIVERY_KEY",
        payload_enc=encrypt_text(payload_text),
    )
    other_deliv = OrderDelivery(
        order_item_id=item.id,
        kind="ACTIVATION_CODE",
        payload_enc=encrypt_text("MOLI-XXXX-YYYY"),
    )
    db.add_all([cce_deliv, other_deliv])
    db.commit()

    monkeypatch.setenv("CCE_API_BASE", "http://host.docker.internal:8010")
    monkeypatch.setenv("CCE_ADMIN_TOKEN", "admin-tok")
    monkeypatch.setenv("CCE_GATEWAY_ADDR", "moliply.shop:8444")

    requested_paths: list[str] = []

    class FakeAssetResp:
        def __init__(self, status_code: int, content: bytes = b""):
            self.status_code = status_code
            self.content = content

    class FakeAssetClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, path, headers=None):
            requested_paths.append(path)
            # Simulate /launcher/client/claude-box-windows-x64.exe returning 404 (older CCE) -> falls back to /admin/client/...
            if path == "/launcher/client/claude-box-windows-x64.exe":
                return FakeAssetResp(404)
            if path == "/admin/client/claude-box-windows-x64.exe":
                assert headers and headers.get("x-admin-token") == "admin-tok"
                return FakeAssetResp(200, b"WIN-EXE-BYTES")
            if path == "/launcher/client/tls.crt":
                assert headers and headers.get("authorization") == "Bearer cce_customer_key_1"
                assert headers.get("x-tunnel-secret") == "secret_xyz_123"
                return FakeAssetResp(200, b"CA-CERT-PEM")
            if path == "/launcher/client/claude-box":
                return FakeAssetResp(200, b"LINUX-ELF-BYTES")
            return FakeAssetResp(404)

    monkeypatch.setattr(cce_delivery, "_asset_http_client", lambda: FakeAssetClient())

    owner_token, _ = create_user_token(owner)
    other_token, _ = create_user_token(other)

    # Non-owner is rejected with 404
    r_other = client.get(
        f"/api/v1/store/orders/{order.order_no}/deliveries/{cce_deliv.id}/bundle?os=windows",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert r_other.status_code == 404

    # Non-CCE delivery is rejected with 400
    r_wrong_kind = client.get(
        f"/api/v1/store/orders/{order.order_no}/deliveries/{other_deliv.id}/bundle?os=windows",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r_wrong_kind.status_code == 400

    # Unsupported OS rejected with 400
    r_bad_os = client.get(
        f"/api/v1/store/orders/{order.order_no}/deliveries/{cce_deliv.id}/bundle?os=plan9",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r_bad_os.status_code == 400

    # Owner gets Windows zip cleanly via HTTP fallback
    r_win = client.get(
        f"/api/v1/store/orders/{order.order_no}/deliveries/{cce_deliv.id}/bundle?os=windows",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r_win.status_code == 200
    assert r_win.headers["content-type"] == "application/zip"
    assert "moliply-claude-claude-pro-01-windows.zip" in r_win.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r_win.content)) as zf:
        assert zf.read("claude-box-windows-x64.exe") == b"WIN-EXE-BYTES"
        assert zf.read("gateway-ca.pem") == b"CA-CERT-PEM"
        manifest = json.loads(zf.read("cce.json").decode("utf-8"))
        assert manifest == {
            "key": "cce_customer_key_1",
            "secret": "secret_xyz_123",
            "host": "https://moliply.shop/cce",
            "gateway": "moliply.shop:8444",
            "ca": "gateway-ca.pem",
        }
        assert "双击 claude-box-windows-x64.exe" in zf.read("请先看我.txt").decode("utf-8")

    # Linux zip preserves executable permission bits
    r_linux = client.get(
        f"/api/v1/store/orders/{order.order_no}/deliveries/{cce_deliv.id}/bundle?os=linux",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert r_linux.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r_linux.content)) as zf:
        assert zf.read("claude-box") == b"LINUX-ELF-BYTES"
        assert (zf.getinfo("claude-box").external_attr >> 16) & 0o111 == 0o111


def test_customer_self_service_password_reset_and_cce_autorenew_cancel(client, db, monkeypatch):
    """Customer can recover forgotten order password via 6-digit email OTP, revoking old JWTs."""
    from app.security import create_user_token
    from app.services import accounts, mailer
    from cce_console.core import subscribe as cce_sub

    user, _ = accounts.get_or_create_user(db, "forgot-pw@example.com", "OldPass123!")
    old_token, _ = create_user_token(user)

    # Without SMTP configured, returns 409 with helpful customer-service fallback message
    monkeypatch.setattr(mailer, "smtp_configured", lambda: False)
    r_no_smtp = client.post("/api/v1/auth/forgot-password", json={"email": user.email})
    assert r_no_smtp.status_code == 409

    # With SMTP configured, sends 6-digit code and enforces verification + session revocation
    sent_codes: list[tuple[str, str]] = []
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)

    def _fake_send_reset(email: str, code: str, *, ttl_minutes: int = 10) -> bool:
        sent_codes.append((email, code))
        return True

    monkeypatch.setattr(mailer, "send_password_reset_email", _fake_send_reset)
    accounts._clear_reset_entry(user.email)

    r_forgot = client.post("/api/v1/auth/forgot-password", json={"email": user.email})
    assert r_forgot.status_code == 200
    assert len(sent_codes) == 1
    _, otp_code = sent_codes[0]
    assert len(otp_code) == 6 and otp_code.isdigit()

    # Cooldown prevents immediate resend spam
    r_cooldown = client.post("/api/v1/auth/forgot-password", json={"email": user.email})
    assert r_cooldown.status_code == 429

    # Wrong code is rejected
    r_bad_code = client.post(
        "/api/v1/auth/reset-password",
        json={"email": user.email, "code": "000000" if otp_code != "000000" else "999999", "new_password": "NewPassword123!"},
    )
    assert r_bad_code.status_code == 400

    # Correct code resets password and returns a fresh token
    r_reset = client.post(
        "/api/v1/auth/reset-password",
        json={"email": user.email, "code": otp_code, "new_password": "NewPassword123!"},
    )
    assert r_reset.status_code == 200
    new_token = r_reset.json()["token"]
    assert new_token

    # Old JWT is immediately revoked via token_version increment
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200

    # CCE auto-renew cancel helper is fault-tolerant and non-fatal
    class DummyPage:
        frames = []

        async def goto(self, *_a, **_k):
            raise RuntimeError("billing layout changed")

    assert asyncio.run(cce_sub._best_effort_cancel_autorenew(DummyPage())) is False


def test_dual_track_vcc_issuers_lithic_and_category_a_auto_mint_and_close(tmp_path, monkeypatch):
    """Both Track A (VMCard/FotonCard USDT REST) and Track B (Lithic) auto-mint, align AVS ZIP, and auto-close."""
    import base64

    from cce_console.core import card_pool, vcc_issuers
    from cce_console.db.database import Database

    monkeypatch.setenv("CCE_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    db = Database(tmp_path / "cce_vcc.sqlite3")
    asyncio.run(db.connect())

    try:
        # 1. AVS Geo -> State ZIP alignment
        assert vcc_issuers.zip_for_geo({"region": "California", "city": "San Francisco"}) == "94107"
        assert vcc_issuers.zip_for_geo({"region": "Oregon"}) == "97204"
        assert vcc_issuers.zip_for_geo({"postal": "10011", "region": "New York"}) == "10011"

        # 2. Track A (VMCard / FotonCard / Generic REST with HMAC signature)
        monkeypatch.setenv("CCE_VCC_PROVIDER", "auto")
        monkeypatch.delenv("LITHIC_API_KEY", raising=False)
        monkeypatch.setenv("VCC_PROVIDER_NAME", "VMCard")
        monkeypatch.setenv("VCC_API_URL", "https://vcc-a.example.com/v1/cards/create")
        monkeypatch.setenv("VCC_API_KEY", "vmcard_key_123")
        monkeypatch.setenv("VCC_API_SECRET", "vmcard_sec_456")
        monkeypatch.setenv("VCC_CARD_BIN", "485932")
        monkeypatch.setenv("VCC_CLOSE_URL", "https://vcc-a.example.com/v1/cards/{id}/close")

        calls: list[tuple[str, str, dict]] = []

        class FakeResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload
                self.text = str(payload)

            def json(self):
                return self._payload

        class FakeAsyncClient:
            def __init__(self, *a, **kw):
                self.base_url = kw.get("base_url", "")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def post(self, url, headers=None, json=None, content=None):
                calls.append(("POST", f"{self.base_url}{url}", headers or {}))
                if "vcc-a.example.com" in str(url) and "create" in str(url):
                    assert headers and "X-Signature" in headers
                    return FakeResp(
                        200,
                        {
                            "code": 200,
                            "data": {
                                "card_id": "vm_9001",
                                "card_number": "4859320000008899",
                                "cvv": "654",
                                "exp": "09/2029",
                                "balance": 21.0,
                            },
                        },
                    )
                if "close" in str(url):
                    return FakeResp(200, {"ok": True})
                if str(url) == "/cards":
                    assert headers and headers.get("Authorization") == "lithic_live_key"
                    return FakeResp(
                        201,
                        {
                            "token": "lithic_tok_777",
                            "pan": "5567660000001122",
                            "cvv": "321",
                            "exp_month": "11",
                            "exp_year": "2030",
                            "state": "OPEN",
                        },
                    )
                raise AssertionError(f"unexpected POST {url}")

            async def patch(self, url, headers=None, json=None):
                calls.append(("PATCH", f"{self.base_url}{url}", json or {}))
                return FakeResp(200, {"state": "CLOSED"})

        monkeypatch.setattr(vcc_issuers.httpx, "AsyncClient", FakeAsyncClient)

        assert vcc_issuers.active_provider() == "category_a"
        # Pool is empty -> lease_card automatically mints a Track A card with California ZIP
        leased_a = asyncio.run(
            card_pool.lease_card(db, profile_id="acct-ca", geo={"region": "California"})
        )
        assert leased_a.pan == "4859320000008899"
        assert leased_a.zip == "94107"
        asyncio.run(card_pool.settle_card(db, leased_a.card_id, success=True))
        assert any("vm_9001/close" in c[1] for c in calls)

        # 3. Track B (EIN arrives -> operator sets LITHIC_API_KEY; auto mode switches to Lithic!)
        monkeypatch.setenv("LITHIC_API_KEY", "lithic_live_key")
        assert vcc_issuers.active_provider() == "lithic"
        assert "Lithic" in vcc_issuers.issuer_status()["label"]

        leased_b = asyncio.run(
            card_pool.lease_card(db, profile_id="acct-or", geo={"region": "Oregon"})
        )
        assert leased_b.pan == "5567660000001122"
        assert leased_b.zip == "97204"
        asyncio.run(card_pool.settle_card(db, leased_b.card_id, success=True))
        assert any(c[0] == "PATCH" and "/cards/lithic_tok_777" in c[1] and c[2].get("state") == "CLOSED" for c in calls)
    finally:
        asyncio.run(db.close())


def test_multi_day_warmup_account_pool_and_isp_purity_gate(tmp_path, monkeypatch):
    """Accounts register into a 2-3 day warm-up pool (no card burned during warm-up), reject datacenter IPs, and graduate or fast-track to Pro."""
    import base64
    from contextlib import asynccontextmanager

    from cce_console.core import card_pool, pipeline, signup, subscribe, warmup
    from cce_console.db.database import Database

    monkeypatch.setenv("CCE_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("CCE_WARMUP_DAYS", "2")
    monkeypatch.setenv("CCE_STRICT_ISP_PROXY", "true")

    db = Database(tmp_path / "cce_warmup.sqlite3")
    asyncio.run(db.connect())

    class FakeProfiles:
        def __init__(self):
            self._port = 10010

        async def create_profile(self, *, profile_id, upstream_proxy_url, expected_ip, **_kw):
            self._port += 1
            record = {
                "profile_id": profile_id,
                "batch_id": None,
                "local_proxy_port": self._port,
                "upstream_proxy_url": upstream_proxy_url,
                "expected_ip": expected_ip,
                "os_type": "windows",
                "noise_seed": 42,
                "fingerprint_json": "{}",
            }
            await db.insert_profile(record)
            return record

    profiles = FakeProfiles()

    class FakePage:
        url = "https://claude.ai/new"
        frames = []

    class FakeContext:
        async def new_page(self):
            return FakePage()

    class FakePool:
        def __init__(self):
            self.synced: list[str] = []

        @asynccontextmanager
        async def acquire_context(self, _profile_id):
            yield FakeContext()

        async def sync_claude_cli_credentials(self, profile_id, **_kw):
            self.synced.append(profile_id)
            await db.save_credentials(profile_id, b"sealed-high-weight-credentials")
            return b"sealed-high-weight-credentials"

    pool = FakePool()
    signup_calls: list[str] = []
    warmup_rounds: list[tuple[str, int]] = []
    subscribe_calls: list[str] = []

    async def fake_signup(page, *, email, **_kw):
        signup_calls.append(email)
        return signup.SignupOutcome("created", email=email)

    async def fake_warmup(page, *, round_index=1, **_kw):
        warmup_rounds.append(("round", round_index))
        return {"ok": True, "round": round_index, "messages_sent": 2}

    async def fake_subscribe(page, *, card, plan="pro", **_kw):
        subscribe_calls.append(card["zip"])
        return subscribe.SubscribeOutcome("subscribed", "订阅成功（已关闭自动续费）")

    monkeypatch.setattr(pipeline.signup, "run_signup", fake_signup)
    monkeypatch.setattr(pipeline.warmup, "run_warmup_session", fake_warmup)
    monkeypatch.setattr(pipeline.subscribe, "run_subscribe", fake_subscribe)

    try:
        # 1. Datacenter IP is blocked by the pre-signup ISP purity gate before touching signup
        dc_row = pipeline.ImportRow(
            email="datacenter@gmail.com",
            app_password="abcd efgh ijkl mnop",
            ip="198.51.100.10",
            proxy_url="socks5://u:p@198.51.100.10:1080",
            warmup_days=2,
        )
        dc_job = asyncio.run(pipeline.create_job(db, profiles, dc_row))
        asyncio.run(
            db.save_geo(
                dc_job["profile_id"],
                '{"ip":"198.51.100.10","country_code":"US","region":"Virginia","is_hosting":true}',
            )
        )
        dc_res = asyncio.run(pipeline.run_job(db, pool, dc_job["profile_id"]))
        assert dc_res["stage"] == "needs_human"
        assert "机房" in dc_res["human_note"]
        assert signup_calls == []  # Clean Gmail was NOT burned!

        # 2. Clean residential ISP account enters the 2-day Warm-up Pool
        isp_row = pipeline.ImportRow(
            email="highweight@gmail.com",
            app_password="abcd efgh ijkl mnop",
            ip="203.0.113.55",
            proxy_url="socks5://u:p@203.0.113.55:1080",
            warmup_days=2,
        )
        job = asyncio.run(pipeline.create_job(db, profiles, isp_row))
        pid = job["profile_id"]
        asyncio.run(
            db.save_geo(
                pid,
                '{"ip":"203.0.113.55","country_code":"US","region":"Oregon","is_hosting":false}',
            )
        )
        # Seed one subscription card (must NOT be leased during Day 1 or Day 2 warm-up!)
        asyncio.run(
            card_pool.issue_card(
                db, pan="4859320000001234", cvv="789", exp_month="10", exp_year="2029", billing_zip=""
            )
        )

        # Day 1 run: registers + runs warm-up round 1 -> parks in 'registered' with warmup_completed=1
        d1 = asyncio.run(pipeline.run_job(db, pool, pid))
        assert d1["stage"] == "registered"
        assert d1["warmup_completed"] == 1
        assert d1["warmup_days"] == 2
        assert d1["next_action_at"] is not None
        assert len(signup_calls) == 1
        assert subscribe_calls == []  # Card NOT touched on Day 1!
        assert asyncio.run(card_pool.list_cards(db))["summary"]["available"] == 1

        # Day 2 run: does NOT re-register; runs warm-up round 2 -> schedules final 4h pre-payment cooldown
        d2 = asyncio.run(pipeline.run_job(db, pool, pid))
        assert d2["stage"] == "registered"
        assert d2["warmup_completed"] == 2
        assert len(signup_calls) == 1  # Still only 1 signup!
        assert len(warmup_rounds) == 2
        assert subscribe_calls == []  # Still in final cooldown before card binding!

        # Graduation run (after cooldown or fast-track): leases card with Oregon ZIP (97204), subscribes Pro, syncs creds -> 'ready'
        grad = asyncio.run(pipeline.run_job(db, pool, pid))
        assert grad["stage"] == "ready"
        assert subscribe_calls == ["97204"]
        assert pool.synced == [pid]
        assert asyncio.run(card_pool.list_cards(db))["summary"]["used"] == 1
    finally:
        asyncio.run(db.close())


def test_zero_time_loss_jit_promotion_and_hot_standby_watermark(monkeypatch):
    """Storefront counts mature free pool accounts as stock and triggers /admin/jobs/promote on payment when ready_pro=0."""
    from app.services.fulfillment import cce_delivery

    _cce_env(monkeypatch)
    # 0 ready Pro accounts on shelf, 1 mature free account in the warm-up pool (3/3 days done)
    profiles = [
        {
            "profile_id": "mature-white-1",
            "status": "IDLE",
            "has_credentials": False,
            "active_key": None,
            "job_stage": "registered",
            "warmup_days": 3,
            "warmup_completed": 3,
            "plan": "free_pool",
            "created_at": "2026-11-20T00:00:00+00:00",
        },
        {
            "profile_id": "still-warming-2",
            "status": "IDLE",
            "has_credentials": False,
            "active_key": None,
            "job_stage": "registered",
            "warmup_days": 3,
            "warmup_completed": 1,
            "plan": "free_pool",
            "created_at": "2026-11-24T00:00:00+00:00",
        },
    ]
    promoted_requests: list[dict] = []

    class FakeResp:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, path):
            assert path == "/admin/profiles"
            return FakeResp({"profiles": profiles})

        def post(self, path, json):
            assert path == "/admin/jobs/promote"
            promoted_requests.append(json)
            return FakeResp({"promoted": ["mature-white-1"], "count": 1})

    monkeypatch.setattr(cce_delivery, "_http_client", lambda: FakeClient())
    monkeypatch.setattr(cce_delivery, "redis_lock", lambda *a, **k: _ctx(True), raising=False)

    # Storefront sees 1 available account (the mature 3/3 white account; the 1/3 warming one is not yet sellable)
    assert cce_delivery.available_cce_profiles() == 1
    # Customer pays -> ready Pro shelf is 0, so claim_and_mint_key triggers JIT promotion and returns None (order waits ~2m for sweeper)
    assert cce_delivery.claim_and_mint_key(plan_tier="pro", valid_days=30) is None
    assert len(promoted_requests) == 1
    assert promoted_requests[0]["count"] == 1


def test_unified_claude_store_backend_and_non_voip_sms_and_avs_profile(client, db, admin_headers, monkeypatch):
    """Storefront claude-pro-account is unified with CCE pool overview, full US AVS billing profile, and Non-VoIP SMS client."""
    from app.models import FulfillmentStrategy, Product
    from cce_console.core import sms_client, vcc_issuers

    # 1. Full US AVS billing profile alignment
    or_avs = vcc_issuers.billing_profile_for_zip("97204", "lithic:tok_1")
    assert or_avs["holder_name"] == "ALEX MERCER"
    assert or_avs["city"] == "Portland" and or_avs["state"] == "OR"
    ca_avs = vcc_issuers.billing_profile_for_zip("94107", "JANE DOE")
    assert ca_avs["holder_name"] == "JANE DOE"
    assert ca_avs["city"] == "San Francisco" and ca_avs["state"] == "CA"

    # 2. Non-VoIP US phone normalization
    assert sms_client._normalize_us_phone("+1 (415) 555-0199") == "4155550199"

    # 3. Unified admin overview & one-click product binding
    claude_prod = db.query(Product).filter(Product.slug == "claude-pro-account").first()
    assert claude_prod is not None
    r_bind = client.post(f"/api/v1/admin/claude/bind-product/{claude_prod.id}", headers=admin_headers)
    assert r_bind.status_code == 200
    db.refresh(claude_prod)
    assert claude_prod.fulfillment_strategy == FulfillmentStrategy.CCE_ACCOUNT_KEY

    r_overview = client.get("/api/v1/admin/claude/overview", headers=admin_headers)
    assert r_overview.status_code == 200
    body = r_overview.json()
    assert "pool" in body and "products" in body
    assert any(p["slug"] == "claude-pro-account" and p["bound_to_cce"] is True for p in body["products"])

    # 4. /cce routes allow SAMEORIGIN framing for /admin/claude while other routes stay DENY
    assert client.get("/healthz").headers.get("x-frame-options") == "DENY"
    assert client.get("/cce/healthz").headers.get("x-frame-options") == "SAMEORIGIN"
