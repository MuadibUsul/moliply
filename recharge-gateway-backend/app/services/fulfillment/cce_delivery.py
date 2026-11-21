"""Automated commercial delivery bridge between MOLIPLY storefront and CCE console.

When a storefront product is configured with ``FulfillmentStrategy.CCE_ACCOUNT_KEY``,
paying an order automatically claims a ready CCE Claude account profile (``IDLE``,
with sealed credentials and no active customer delivery key), mints a time-limited
``card_key``, refreshes the tunnel gateway authorization snapshot, and returns the
customer-facing handover payload for ``OrderDelivery``.

If CCE is not installed or no ready profile is available, ``claim_and_mint_key``
returns ``None`` so the order cleanly enters ``stock_pending`` and will be
auto-fulfilled by the backorder sweeper as soon as CCE finishes provisioning.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _cce_db_path() -> Path:
    raw = os.environ.get("CCE_DB_PATH") or "data/cce.sqlite3"
    return Path(raw)


def _cce_secrets_path() -> Path:
    raw = os.environ.get("CCE_TUNNEL_SECRETS") or "data/cce.secrets"
    return Path(raw)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection | None:
    path = _cce_db_path()
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def available_cce_profiles() -> int:
    """Count ready, unassigned CCE profiles eligible for automatic delivery."""
    conn = _connect()
    if conn is None:
        return 0
    try:
        now_iso = _now_iso()
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM profiles p
            WHERE p.status = 'IDLE'
              AND p.claude_credentials_enc IS NOT NULL
              AND length(p.claude_credentials_enc) > 0
              AND NOT EXISTS (
                  SELECT 1 FROM delivery_keys k
                  WHERE k.profile_id = p.profile_id
                    AND k.is_active = 1
                    AND k.expires_at > ?
              )
            """,
            (now_iso,),
        ).fetchone()
        return int(row["cnt"] if row else 0)
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _export_tunnel_secrets(conn: sqlite3.Connection) -> None:
    """Refresh the gateway's ``cce.secrets`` snapshot atomically."""
    secrets_path = _cce_secrets_path()
    try:
        now_iso = _now_iso()
        rows = conn.execute(
            """
            SELECT d.card_key, d.tunnel_secret, p.local_proxy_port, p.profile_id, d.expires_at
            FROM delivery_keys d
            JOIN profiles p ON p.profile_id = d.profile_id
            WHERE d.is_active = 1 AND d.expires_at > ? AND p.status IN ('IDLE', 'BUSY')
            ORDER BY d.card_key
            """,
            (now_iso,),
        ).fetchall()
        lines = [
            f"{r['card_key']}\t{r['tunnel_secret']}\t{r['local_proxy_port']}\t{r['profile_id']}\t{r['expires_at']}"
            for r in rows
        ]
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = secrets_path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        os.replace(tmp, secrets_path)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to refresh CCE tunnel secrets snapshot", exc_info=True)


def claim_and_mint_key(*, plan_tier: str = "pro", valid_days: int = 30) -> dict | None:
    """Atomically claim one ready CCE profile and mint a delivery key for a paid order."""
    conn = _connect()
    if conn is None:
        return None
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        now_iso = now.isoformat()
        expires_iso = (now + timedelta(days=max(1, int(valid_days)))).isoformat()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT p.profile_id, p.local_proxy_port
            FROM profiles p
            WHERE p.status = 'IDLE'
              AND p.claude_credentials_enc IS NOT NULL
              AND length(p.claude_credentials_enc) > 0
              AND NOT EXISTS (
                  SELECT 1 FROM delivery_keys k
                  WHERE k.profile_id = p.profile_id
                    AND k.is_active = 1
                    AND k.expires_at > ?
              )
            ORDER BY p.created_at ASC
            LIMIT 1
            """,
            (now_iso,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        profile_id = str(row["profile_id"])
        card_key = f"cce_{secrets.token_urlsafe(24)}"
        tunnel_secret = secrets.token_hex(24)
        conn.execute(
            """
            INSERT INTO delivery_keys (card_key, profile_id, plan_tier, tunnel_secret, expires_at, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (card_key, profile_id, plan_tier, tunnel_secret, expires_iso),
        )
        conn.commit()
        _export_tunnel_secrets(conn)
        base = settings.public_store_url.rstrip("/")
        payload = (
            f"CCE_KEY={card_key}\n"
            f"TUNNEL_SECRET={tunnel_secret}\n"
            f"PROFILE={profile_id}\n"
            f"EXPIRES_AT={expires_iso}\n"
            f"HOST={base}/cce\n"
            f"LAUNCH=claude-box cli --key {card_key} --secret {tunnel_secret} --host {base}/cce"
        )
        return {
            "profile_id": profile_id,
            "card_key": card_key,
            "tunnel_secret": tunnel_secret,
            "expires_at": expires_iso,
            "payload": payload,
        }
    except sqlite3.Error:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        logger.exception("Failed to mint CCE delivery key")
        return None
    finally:
        conn.close()
