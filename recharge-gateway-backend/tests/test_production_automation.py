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
