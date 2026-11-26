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

from core import vcc_issuers
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
    holder = str(row.get("holder") or "")
    source = "manual"
    if holder.startswith("lithic:"):
        source = "lithic"
    elif holder.startswith("api:"):
        source = "category_a"
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
        "source": source,
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
        "issuer": vcc_issuers.issuer_status(),
    }


async def mint_cards_to_pool(
    db: Database,
    *,
    count: int = 1,
    amount_usd: float | None = None,
    billing_zip: str = "",
    memo: str = "CCE-Pool",
    geo: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Mint one or more virtual cards via the active A/B API provider and save them to the pool."""
    created: list[dict[str, Any]] = []
    for idx in range(max(1, min(int(count), 20))):
        try:
            issued = await vcc_issuers.mint_card(
                memo=f"{memo}-{idx + 1}",
                amount_usd=amount_usd,
                billing_zip=billing_zip,
                geo=geo,
            )
        except vcc_issuers.VccIssuerError as exc:
            raise CardError(str(exc)) from exc
        row = await issue_card(
            db,
            pan=issued.pan,
            cvv=issued.cvv,
            exp_month=issued.exp_month,
            exp_year=issued.exp_year,
            billing_zip=issued.billing_zip,
            holder=issued.holder,
            balance_usd=issued.balance_usd,
        )
        created.append(row)
    return created


async def lease_card(
    db: Database,
    *,
    profile_id: str = "",
    billing_zip: str = "",
    geo: dict[str, Any] | None = None,
) -> LeasedCard:
    """Earmark one available card, or auto-mint one via the A/B VCC API when the pool is empty."""
    row: dict[str, Any] | None = None
    async with db.transaction() as conn:
        cursor = await conn.execute(
            "SELECT * FROM subscription_cards WHERE status = 'available' ORDER BY id LIMIT 1"
        )
        fetched = await cursor.fetchone()
        await cursor.close()
        if fetched is not None:
            cursor = await conn.execute(
                "UPDATE subscription_cards SET status = 'in_use', used_at = CURRENT_TIMESTAMP"
                " WHERE id = ? AND status = 'available'",
                (fetched["id"],),
            )
            if cursor.rowcount != 1:
                raise CardError("卡片已被并发占用，请重试")
            row = dict(fetched)

    if row is None:
        if vcc_issuers.auto_mint_enabled():
            minted = await mint_cards_to_pool(
                db,
                count=1,
                billing_zip=billing_zip,
                memo=f"CCE-{profile_id or 'auto'}",
                geo=geo,
            )
            card_id = int(minted[0]["id"])
            async with db.transaction() as conn:
                cursor = await conn.execute(
                    "UPDATE subscription_cards SET status = 'in_use', used_at = CURRENT_TIMESTAMP"
                    " WHERE id = ? AND status = 'available'",
                    (card_id,),
                )
                if cursor.rowcount != 1:
                    raise CardError("自动开出的卡片已被并发占用，请重试")
            row = await db.fetch_one("SELECT * FROM subscription_cards WHERE id = ?", (card_id,))
        if row is None:
            raise CardError("没有可用卡，请先在卡片页导入或配置自动发卡 API")

    pan = decrypt_blob(row["pan_enc"]).decode()
    cvv = decrypt_blob(row["cvv_enc"]).decode()
    resolved_zip = str(row["billing_zip"] or "").strip() or vcc_issuers.zip_for_geo(geo, fallback=billing_zip)
    return LeasedCard(
        card_id=row["id"],
        pan=pan,
        cvv=cvv,
        exp=f"{row['exp_month']}/{row['exp_year']}",
        zip=resolved_zip,
    )


async def settle_card(db: Database, card_id: int, *, success: bool, reason: str = "") -> None:
    """Retire a charged card, return a soft failure, bury a dead one, and close single-use remote cards."""
    row = await db.fetch_one("SELECT holder FROM subscription_cards WHERE id = ?", (card_id,))
    holder_tag = str(row["holder"] or "") if row else ""
    if success:
        await db.execute(
            "UPDATE subscription_cards SET status = 'used', last_error = '' WHERE id = ?", (card_id,)
        )
        await vcc_issuers.close_remote_card(holder_tag)
        return
    lowered = (reason or "").lower()
    dead = any(word in lowered for word in ("declined", "insufficient", "expired", "invalid"))
    await db.execute(
        "UPDATE subscription_cards SET status = ?, last_error = ? WHERE id = ?",
        ("dead" if dead else "available", (reason or "")[:200], card_id),
    )
    if dead:
        await vcc_issuers.close_remote_card(holder_tag)


async def release_card(db: Database, card_id: int) -> None:
    """Put a card back untouched (e.g. the browser died before payment)."""
    await db.execute(
        "UPDATE subscription_cards SET status = 'available' WHERE id = ? AND status = 'in_use'",
        (card_id,),
    )


async def reset_card(db: Database, card_id: int) -> dict[str, Any]:
    """Return a dead or stranded card back to available so it can be retried."""
    row = await db.fetch_one("SELECT * FROM subscription_cards WHERE id = ?", (card_id,))
    if row is None:
        raise CardError("卡片不存在")
    if row["status"] == "in_use":
        raise CardError("卡片正在扣款中，请稍后再试")
    await db.execute(
        "UPDATE subscription_cards SET status = 'available', last_error = '' WHERE id = ?",
        (card_id,),
    )
    updated = await db.fetch_one("SELECT * FROM subscription_cards WHERE id = ?", (card_id,))
    assert updated is not None
    return _public_row(updated)


async def delete_card(db: Database, card_id: int) -> None:
    """Remove a card that is not actively leased."""
    row = await db.fetch_one("SELECT * FROM subscription_cards WHERE id = ?", (card_id,))
    if row is None:
        raise CardError("卡片不存在")
    if row["status"] == "in_use":
        raise CardError("卡片正在扣款中，不能删除")
    await db.execute("DELETE FROM subscription_cards WHERE id = ?", (card_id,))


__all__ = [
    "CardError",
    "LeasedCard",
    "delete_card",
    "issue_card",
    "lease_card",
    "list_cards",
    "mint_cards_to_pool",
    "release_card",
    "reset_card",
    "settle_card",
]
