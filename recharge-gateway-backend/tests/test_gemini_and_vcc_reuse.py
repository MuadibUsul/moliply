from __future__ import annotations

from decimal import Decimal

from app.models import (
    ActivationCode,
    ChannelMode,
    CodeStatus,
    FreeAccountPool,
    FreeAccountStatus,
    Product,
    RechargeState,
    SkuType,
    VccCard,
    VccStatus,
)
from app.services import redeem as redeem_service
from app.services.code_generator import generate_code
from app.services.crypto import encrypt_text
from app.services.fulfillment import vcc_pool_scheduler


def test_shared_dual_track_vcc_issuer_mints_into_moliply_pool(client, db, admin_headers, monkeypatch):
    """ChatGPT / MOLIPLY VCC pool scheduler reuses cce_console.core.vcc_issuers for on-demand API card minting."""
    import cce_console.core.vcc_issuers as issuers

    closed_calls: list[tuple[str, str]] = []

    def fake_mint(*, spend_limit_usd: float = 22.0, zip_code: str = "97204", memo: str = ""):
        return {
            "provider": "LITHIC",
            "pan": "4111222233334444",
            "cvv": "888",
            "exp_month": "08",
            "exp_year": "2029",
            "zip_code": zip_code,
            "external_id": "lithic-tok-moliply-001",
            "holder_name": "ALEXANDER MERCER",
        }

    def fake_close(provider: str, external_id: str) -> bool:
        closed_calls.append((provider, external_id))
        return True

    monkeypatch.setattr(issuers, "mint_vcc_card", fake_mint)
    monkeypatch.setattr(issuers, "close_remote_card_sync", fake_close)

    # Admin API one-click minting into MOLIPLY VccCard pool
    resp = client.post(
        "/api/v1/admin/cards/mint?spend_limit_usd=25.0&zip_code=97204&max_bind_count=2",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "LITHIC"
    assert body["card_bin"] == "411122"
    assert body["available_balance"] == 25.0

    card = db.get(VccCard, body["id"])
    assert card is not None
    assert card.billing_address_json["state"] == "OR"
    assert card.billing_address_json["external_id"] == "lithic-tok-moliply-001"
    assert card.ph_billing_address_json["country"] == "PH"

    # Closing the card in MOLIPLY also triggers remote closure via shared vcc_issuers
    close_resp = client.post(f"/api/v1/admin/cards/{card.id}/mark-closed", headers=admin_headers)
    assert close_resp.status_code == 200, close_resp.text
    db.refresh(card)
    assert card.status == VccStatus.CLOSED
    assert closed_calls == [("LITHIC", "lithic-tok-moliply-001")]


def test_gemini_family_1_to_5_seat_allocation_reuses_free_account_pool_and_redeem(client, db):
    """Gemini Advanced Family 1-to-5 product reuses ActivationCode, /redeem, and FreeAccountPool (5 seats per host)."""
    family_prod = db.query(Product).filter(Product.slug == "gemini-advanced-family-1m").first()
    assert family_prod is not None

    # Clear existing demo hosts for deterministic seat count assertions
    db.query(FreeAccountPool).filter(FreeAccountPool.product_id == family_prod.id).delete()
    host = FreeAccountPool(
        product_id=family_prod.id,
        email="family.master.test@gmail.com",
        password_enc=encrypt_text("MasterPass!2026"),
        recovery_enc=encrypt_text("recovery@moliply.test"),
        session_raw_enc=encrypt_text("https://families.google.com/join/promo?host=test"),
        note="Google One 2TB 主号 [seats:0/5]",
        status=FreeAccountStatus.AVAILABLE,
    )
    db.add(host)
    db.commit()
    db.refresh(host)

    # Allocate 5 seats sequentially across 5 different customer Gmails
    for seat_idx in range(1, 6):
        code_str = generate_code()
        code = ActivationCode(
            code=code_str,
            sku_type=SkuType.GEMINI_ADVANCED_1M,
            channel_mode=ChannelMode.HYBRID_SELF_FIRST,
            status=CodeStatus.UNUSED,
        )
        db.add(code)
        db.commit()

        verify_resp = client.post("/api/v1/redeem/verify-code", json={"code": code_str})
        assert verify_resp.status_code == 200, verify_resp.text
        assert verify_resp.json()["session_kind"] == "gemini"

        customer_gmail = f"buyer.seat{seat_idx}@gmail.com"
        pre_resp = client.post(
            "/api/v1/redeem/parse-session",
            json={"code": code_str, "session_raw": customer_gmail},
        )
        assert pre_resp.status_code == 200, pre_resp.text
        assert pre_resp.json()["eligible"] is True

        sub_resp = client.post(
            "/api/v1/redeem/submit",
            json={"code": code_str, "session_raw": customer_gmail},
        )
        assert sub_resp.status_code == 200, sub_resp.text
        task_id = sub_resp.json()["task_id"]

        redeem_service.run_recharge_task(task_id)

        status_resp = client.get(f"/api/v1/redeem/status/{task_id}")
        assert status_resp.status_code == 200
        task_data = status_resp.json()
        assert task_data["state"] == RechargeState.SUCCESS.value

        db.refresh(host)
        assert f"[seats:{seat_idx}/5]" in host.note
        if seat_idx < 5:
            assert host.status == FreeAccountStatus.AVAILABLE
        else:
            assert host.status == FreeAccountStatus.SOLD
