"""Minimal card pool for CCE subscriptions.

One row is one card. Cards are earmarked per attempt and released back or
consumed with the documented outcome. There is deliberately no scheduler here:
CCE runs one signup per profile at a time, so a simple "claim one unused card"
is enough, and keeping it local avoids duplicating MOLIPLY's VCC rules.

The PAN is stored encrypted with the same CCE_MASTER_KEY as credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.crypto import decrypt_blob, encrypt_blob
from db.database import Database

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


class CardError(RuntimeError):
    pass


@dataclass
class LeasedCard:
    card_id: int
    pan: str
    cvv: str
    exp: str
    zip: str

    def as_dict(self) -> dict[str, Any]:
        return {"pan": self.pan, "cvv": self.cvv, "exp": self.exp, "zip": self.zip}


def _split_exp(raw: str) -> str:
    return str(raw or "")


async def issue_card(
    db: Database,
    *,
    pan: str,
    cvv: str,
    exp_month: str,
    exp_year: str,
    billing_zip: str = "",
    holder: str = "",
    balance_usd: float = 0.0,
) -> dict[str, Any]:
    """Store one card; the PAN is sealed before it touches the row."""
    digits = "".join(ch for ch in pan if ch.isdigit())
    if not 12 <= len(digits) <= 19:
        raise CardError("卡号长度无效")
    if not 3 <= len(str(cvv)) <= 4:
        raise CardError("CVV 无效")
    month = "".join(ch for ch in str(exp_month) if ch.isdigit())
    if not 1 <= int(month or 0) <= 12:
        raise CardError("有效期月份无效")

    await db.execute(
        "INSERT INTO subscription_cards"
        " (card_bin, last4, pan_enc, cvv_enc, exp_month, exp_year, billing_zip, holder, balance_usd)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            digits[:6],
            digits[-4:],
            encrypt_blob(digits.encode()),
            encrypt_blob(str(cvv).encode()),
            month.zfill(2),
            str(exp_year)[-2:],
            billing_zip,
            holder,
            float(balance_usd or 0),
        ),
    )
    row = await db.fetch_one("SELECT * FROM subscription_cards ORDER BY id DESC LIMIT 1")
    assert row is not None
    return _public_row(row)


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "card_bin": row["card_bin"],
        "last4": row["last4"],
        "exp": f"{row['exp_month']}/{row['exp_year']}",
        "billing_zip": row["billing_zip"],
        "status": row["status"],
        "balance_usd": row["balance_usd"],
        "last_error": row["last_error"],
        "used_at": row["used_at"],
    }


async def list_cards(db: Database) -> dict[str, Any]:
    rows = await db.fetch_all("SELECT * FROM subscription_cards ORDER BY id DESC")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "items": [_public_row(row) for row in rows],
        "summary": {
            "total": len(rows),
            "available": counts.get("available", 0),
            "in_use": counts.get("in_use", 0),
            "used": counts.get("used", 0),
            "dead": counts.get("dead", 0),
        },
    }


async def lease_card(db: Database) -> LeasedCard:
    """Earmark one available card, atomically."""
    async with db.transaction() as conn:
        cursor = await conn.execute(
            "SELECT * FROM subscription_cards WHERE status = 'available' ORDER BY id LIMIT 1"
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise CardError("没有可用卡，请先在卡片页导入")
        await conn.execute(
            "UPDATE subscription_cards SET status = 'in_use', used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (row["id"],),
        )
    pan = decrypt_blob(row["pan_enc"]).decode()
    cvv = decrypt_blob(row["cvv_enc"]).decode()
    return LeasedCard(
        card_id=row["id"],
        pan=pan,
        cvv=cvv,
        exp=f"{row['exp_month']}/{row['exp_year']}",
        zip=row["billing_zip"],
    )


async def settle_card(db: Database, card_id: int, *, success: bool, reason: str = "") -> None:
    """Retire a charged card, return a soft failure, bury a dead one."""
    if success:
        await db.execute(
            "UPDATE subscription_cards SET status = 'used', last_error = '' WHERE id = ?", (card_id,)
        )
        return
    lowered = (reason or "").lower()
    dead = any(word in lowered for word in ("declined", "insufficient", "expired", "invalid"))
    await db.execute(
        "UPDATE subscription_cards SET status = ?, last_error = ? WHERE id = ?",
        ("dead" if dead else "available", (reason or "")[:200], card_id),
    )


async def release_card(db: Database, card_id: int) -> None:
    """Put a card back untouched (e.g. the browser died before payment)."""
    await db.execute(
        "UPDATE subscription_cards SET status = 'available' WHERE id = ? AND status = 'in_use'",
        (card_id,),
    )


__all__ = ["CardError", "LeasedCard", "issue_card", "lease_card", "list_cards", "release_card", "settle_card"]
