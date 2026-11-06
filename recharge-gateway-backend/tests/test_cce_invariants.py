"""Invariants of the vendored CCE console.

No browser is launched here: every case works against the state machine, the
sealing format, the card pool and the gateway snapshot. Those are exactly the
parts where a silent regression costs accounts or leaks credentials, and they
are all deterministic.

The CCE tree uses bare top-level packages (``core``, ``db``), mirroring how
``cce_console/__init__.py`` puts its own directory on ``sys.path`` for the app.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest

CCE_ROOT = Path(__file__).resolve().parents[1] / "cce_console"
if str(CCE_ROOT) not in sys.path:
    sys.path.insert(0, str(CCE_ROOT))

from core import card_pool, crypto, lifecycle  # noqa: E402
from core.secret_export import export_secrets  # noqa: E402
from db.database import Database  # noqa: E402

KEY = base64.b64encode(b"k" * 32).decode()


@pytest.fixture(autouse=True)
def _master_key(monkeypatch):
    """Every case needs the sealing key, including the ones without a database."""
    monkeypatch.setenv("CCE_MASTER_KEY", KEY)


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "cce.sqlite3")
    asyncio.run(database.connect())
    yield database
    asyncio.run(database.close())


def _profile(db: Database, profile_id: str = "prof-a", status: str = "IDLE") -> None:
    asyncio.run(
        db.execute(
            "INSERT INTO profiles (profile_id, local_proxy_port, upstream_proxy_url, expected_ip,"
            " os_type, noise_seed, fingerprint_json, status)"
            " VALUES (?, ?, ?, ?, 'macos', 7, '{}', ?)",
            (profile_id, abs(hash(profile_id)) % 900 + 10001, "socks5://127.0.0.1:1", "203.0.113.7", status),
        )
    )


def _run(coro):
    return asyncio.run(coro)


# -- invariant: the ciphertext is bound to its profile ----------------------


def test_ciphertext_cannot_be_replayed_under_another_profile():
    plaintext = b'{"claudeAiOauth": {"accessToken": "sk-ant-oat01-x"}}'
    blob = crypto.encrypt_blob(plaintext, associated=b"prof-a")
    assert crypto.decrypt_blob(blob, associated=b"prof-a") == plaintext
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_blob(blob, associated=b"prof-b")


def test_ciphertext_binds_nothing_when_no_associated_data_given():
    blob = crypto.encrypt_blob(b"x")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_blob(blob, associated=b"prof-a")


def test_missing_master_key_fails_loudly(monkeypatch):
    monkeypatch.delenv("CCE_MASTER_KEY", raising=False)
    with pytest.raises(crypto.CryptoError):
        crypto.encrypt_blob(b"x")


# -- invariant: the status table is a permission, not a label ---------------


def test_banned_profile_may_only_return_to_idle(db):
    _profile(db, status="BANNED")
    with pytest.raises(lifecycle.IllegalTransition):
        _run(lifecycle.mark(db, "prof-a", "BUSY"))
    _run(lifecycle.mark(db, "prof-a", "IDLE"))
    assert _run(db.get_profile("prof-a"))["status"] == "IDLE"


def test_force_bypasses_the_transition_table(db):
    _profile(db, status="BANNED")
    _run(lifecycle.mark(db, "prof-a", "BUSY", force=True))
    assert _run(db.get_profile("prof-a"))["status"] == "BUSY"


def test_cooldown_is_clamped_to_the_ceiling():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    endless = lifecycle.cooldown_until(timedelta(days=9), now=now)
    assert datetime.fromisoformat(endless) == now + lifecycle.COOLDOWN_MAX


# -- invariant: a lost launcher cannot strand a profile ---------------------


def test_expired_lease_is_reclaimed_but_a_heartbeat_keeps_it(db):
    from datetime import datetime, timedelta, timezone

    _profile(db, profile_id="stale", status="BUSY")
    _profile(db, profile_id="alive", status="BUSY")
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    fresh = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    _run(db.execute("UPDATE profiles SET last_heartbeat_at = ? WHERE profile_id = 'stale'", (old,)))
    _run(db.execute("UPDATE profiles SET last_heartbeat_at = ? WHERE profile_id = 'alive'", (fresh,)))

    reclaimed = _run(lifecycle.reclaim_stale_busy(db))

    assert reclaimed == ["stale"]
    assert _run(db.get_profile("stale"))["status"] == "IDLE"
    assert _run(db.get_profile("alive"))["status"] == "BUSY"


def test_expired_cooldown_is_released(db):
    from datetime import datetime, timedelta, timezone

    _profile(db, status="COOLDOWN")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    _run(db.execute("UPDATE profiles SET cooldown_until = ? WHERE profile_id = 'prof-a'", (past,)))

    assert _run(lifecycle.release_due_cooldowns(db)) == ["prof-a"]
    assert _run(db.get_profile("prof-a"))["status"] == "IDLE"


def test_a_resting_cooldown_is_not_released_early(db):
    from datetime import datetime, timedelta, timezone

    _profile(db, status="COOLDOWN")
    future = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    _run(db.execute("UPDATE profiles SET cooldown_until = ? WHERE profile_id = 'prof-a'", (future,)))
    assert _run(lifecycle.release_due_cooldowns(db)) == []


# -- invariant: a card is leased once, and only a real decline buries it ---


def _card(db: Database, pan: str = "4242424242424242") -> dict:
    return _run(
        card_pool.issue_card(
            db, pan=pan, cvv="123", exp_month="12", exp_year="2029", billing_zip="10001"
        )
    )


def test_card_is_leased_exactly_once(db):
    card = _card(db)
    first = _run(card_pool.lease_card(db))
    assert first.card_id == card["id"]
    with pytest.raises(card_pool.CardError):
        _run(card_pool.lease_card(db))


def test_soft_failure_returns_the_card_to_the_pool(db):
    card = _card(db)
    leased = _run(card_pool.lease_card(db))
    _run(card_pool.release_card(db, leased.card_id))
    assert _run(card_pool.lease_card(db)).card_id == card["id"]


def test_explicit_decline_buries_the_card(db):
    card = _card(db)
    leased = _run(card_pool.lease_card(db))
    _run(card_pool.settle_card(db, leased.card_id, success=False, reason="card was declined"))
    remaining = _run(card_pool.list_cards(db))["items"]
    assert remaining[0]["status"] == "dead"


def test_missing_card_raises_instead_of_returning_none(db):
    with pytest.raises(card_pool.CardError):
        _run(card_pool.lease_card(db))


# -- invariant: the gateway snapshot is the allowlist ----------------------


def _key(db: Database, profile_id: str = "prof-a", *, days: int = 30, secret: str = "s3cret") -> str:
    from datetime import datetime, timedelta, timezone

    card_key = "cce-" + profile_id
    _run(
        db.insert_delivery_key(
            {
                "card_key": card_key,
                "profile_id": profile_id,
                "plan_tier": "pro",
                "tunnel_secret": secret,
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
            }
        )
    )
    return card_key


def test_snapshot_exports_only_live_grants(db, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    target = tmp_path / "tunnel.secrets"
    monkeypatch.setenv("CCE_TUNNEL_SECRETS", str(target))
    _profile(db, profile_id="live", status="IDLE")
    _profile(db, profile_id="banned", status="BANNED")
    _profile(db, profile_id="expired", status="IDLE")

    _key(db, "live", secret="keep-me")
    _key(db, "banned")
    expired_key = _key(db, "expired")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _run(db.execute("UPDATE delivery_keys SET expires_at = ? WHERE card_key = ?", (past, expired_key)))

    result = _run(export_secrets(db, force=True))

    body = target.read_text(encoding="utf-8")
    assert result["keys"] == 1
    assert "keep-me" in body
    assert "cce-banned" not in body
    assert expired_key not in body
    if sys.platform != "win32":
        assert (target.stat().st_mode & 0o777) == 0o600


def test_revoked_key_leaves_the_snapshot(db, tmp_path, monkeypatch):
    target = tmp_path / "tunnel.secrets"
    monkeypatch.setenv("CCE_TUNNEL_SECRETS", str(target))
    _profile(db)
    key = _key(db)
    assert _run(export_secrets(db, force=True))["keys"] == 1

    _run(db.revoke_delivery_key(key))

    assert _run(export_secrets(db, force=True))["keys"] == 0
    assert key not in target.read_text(encoding="utf-8")


def test_active_delivery_key_rejects_expired_and_revoked(db):
    from datetime import datetime, timedelta, timezone

    _profile(db)
    key = _key(db)
    now = datetime.now(timezone.utc).isoformat()
    assert _run(db.active_delivery_key(key, now)) is not None

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert _run(db.active_delivery_key(key, past)) is not None
    assert _run(db.active_delivery_key(key, (datetime.now(timezone.utc) + timedelta(days=99)).isoformat())) is None
