from __future__ import annotations

from decimal import Decimal

import pytest

from app.database import SessionLocal as db_session
from app.services.card_import import CardBatchParseError, parse_batch_cards

DUP_TEXT = "4000000000000002,08,2029,123,40" + chr(10) + "4000000000000002,08,2029,123,40"


def test_parses_json_array():
    rows = parse_batch_cards(
        '[{"card_number": "4242 4242 4242 4242", "exp_month": "8", "exp_year": "2029", "cvv": "123", "balance": 40}]'
    )
    assert len(rows) == 1
    assert rows[0]["card_bin"] == "424242"
    assert rows[0]["last4"] == "4242"
    assert rows[0]["exp_month"] == "08"
    assert rows[0]["exp_year"] == "29"
    assert rows[0]["available_balance"] == Decimal("40")


def test_parses_csv_lines_and_skips_header_and_comments():
    rows = parse_batch_cards(
        "\n".join(
            [
                "# paste from issuer",
                "pan,month,year,cvv,balance,zip",
                "4242424242424242,08,2029,123,40.50,10001",
                "5555555555554444,11,28,999",
            ]
        )
    )
    assert len(rows) == 2
    assert rows[0]["available_balance"] == Decimal("40.50")
    assert rows[0]["billing_zip"] == "10001"
    # Missing balance falls back to the supplied default.
    assert rows[1]["available_balance"] == Decimal("25.00")


def test_accepts_tab_separated_export():
    rows = parse_batch_cards("4242424242424242\t08\t2029\t123\t40")
    assert len(rows) == 1
    assert rows[0]["available_balance"] == Decimal("40")


def test_rejects_luhn_invalid_pan():
    with pytest.raises(CardBatchParseError) as exc:
        parse_batch_cards("4242424242424241,08,2029,123")
    assert "Luhn" in str(exc.value)


def test_applies_encryptor():
    rows = parse_batch_cards("4242424242424242,08,2029,123", encrypt=lambda value: f"enc:{value}")
    assert rows[0]["card_number_enc"] == "enc:4242424242424242"
    assert rows[0]["cvv_enc"] == "enc:123"


@pytest.mark.parametrize(
    "text,message",
    [
        ("", ""),
        ("4242,08,2029,123", "卡号位数不足"),
        ("4242424242424242,13,2029,123", "有效期月份无效"),
        ("4242424242424242,08,2029,12", "CVV"),
        ("4242424242424242,08,2029,123,-5", "余额不能为负"),
        ("[not json", "JSON 格式错误"),
    ],
)
def test_rejects_bad_input(text, message):
    if not text:
        assert parse_batch_cards(text) == []
        return
    with pytest.raises(CardBatchParseError) as exc:
        parse_batch_cards(text)
    assert message in str(exc.value)


def test_paste_import_endpoint_creates_cards(client, admin_headers, db):
    from app.models import VccCard

    response = client.post(
        "/api/v1/admin/cards/paste-import",
        json={"text": "4000000000000028,08,2029,123,40" + chr(10) + "4000000000000044,11,28,999,30"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 2

    listing = client.get("/api/v1/admin/cards", headers=admin_headers).json()
    tails = {row["card_masked"][-4:] for row in listing["items"]}
    assert {"0028", "0044"} <= tails


def test_duplicate_card_numbers_collapse_within_one_paste(client, admin_headers, db):
    from app.models import VccCard

    response = client.post(
        "/api/v1/admin/cards/paste-import",
        json={"text": DUP_TEXT},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1
    db.expire_all()
    from app.services.crypto import card_fingerprint

    matching = (
        db.query(VccCard)
        .filter(VccCard.card_fingerprint == card_fingerprint("4000000000000002"))
        .count()
    )
    assert matching == 1


def test_reimporting_the_same_card_is_skipped(client, admin_headers, db):
    from app.models import VccCard

    text = "4000000000000036,11,28,999,30"
    assert client.post("/api/v1/admin/cards/paste-import", json={"text": text}, headers=admin_headers).json()["created"] == 1
    again = client.post("/api/v1/admin/cards/paste-import", json={"text": text}, headers=admin_headers)
    assert again.status_code == 200, again.text
    assert again.json()["created"] == 0
    assert again.json()["skipped"] == 1
    db.expire_all()
    from app.services.crypto import card_fingerprint

    assert (
        db.query(VccCard)
        .filter(VccCard.card_fingerprint == card_fingerprint("4000000000000036"))
        .count()
        == 1
    )


def test_single_card_entry_refuses_a_known_card(client, admin_headers):
    payload = {
        "provider": "RDVCC",
        "card_number": "4000000000000010",
        "cvv": "123",
        "exp_month": "08",
        "exp_year": "2029",
        "holder_name": "DEMO USER",
        "billing_address": {
            "line1": "1 Main St",
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "US",
        },
        "available_balance": 30,
        "max_bind_count": 2,
    }
    assert client.post("/api/v1/admin/cards", json=payload, headers=admin_headers).status_code == 200
    duplicate = client.post("/api/v1/admin/cards", json=payload, headers=admin_headers)
    assert duplicate.status_code == 409


def test_paste_import_dry_run_does_not_write(client, admin_headers, db):
    from app.models import VccCard

    before = db.query(VccCard).count()
    response = client.post(
        "/api/v1/admin/cards/paste-import",
        json={"text": "4242424242424242,08,2029,123,40", "dry_run": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["parsed"] == 1
    db.expire_all()
    assert db.query(VccCard).count() == before


def test_paste_import_rejects_unknown_gateway_country(client, admin_headers):
    response = client.post(
        "/api/v1/admin/cards/paste-import",
        json={
            "text": "4242424242424242,08,2029,123",
            "billing_address": {
                "line1": "1 Road",
                "city": "London",
                "state": "LN",
                "postal_code": "SW1A",
                "country": "US",
            },
        },
        headers=admin_headers,
    )
    assert response.status_code == 200


def test_bin_allowlist_blocks_import(client, admin_headers, db, monkeypatch):
    from app.services import runtime_settings

    with db_session() as session:
        runtime_settings.update_settings(session, {"us_bin_allowlist": "411111"})
    response = client.post(
        "/api/v1/admin/cards/paste-import",
        json={"text": "4000000000000069,08,2029,123,40"},
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "白名单" in response.json()["detail"]


def test_calibrate_revives_an_exhausted_card(client, admin_headers, db):
    from app.models import VccCard, VccStatus

    created = client.post(
        "/api/v1/admin/cards",
        json={
            "provider": "RDVCC",
            "card_number": "4000000000000051",
            "cvv": "123",
            "exp_month": "12",
            "exp_year": "2029",
            "holder_name": "DEMO USER",
            "billing_address": {
                "line1": "1 Main St",
                "city": "New York",
                "state": "NY",
                "postal_code": "10001",
                "country": "US",
            },
            "available_balance": 30,
            "max_bind_count": 2,
        },
        headers=admin_headers,
    ).json()
    card = db.get(VccCard, created["id"])
    card.status = VccStatus.EXHAUSTED
    db.commit()

    response = client.post(
        f"/api/v1/admin/cards/{created['id']}/calibrate",
        json={"available_balance": 45, "note": "充值"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    card = db.get(VccCard, created["id"])
    assert card.available_balance == Decimal("45.00")
    assert card.status == VccStatus.ACTIVE
