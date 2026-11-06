"""Account pipeline: import → register → subscribe → ready → deliver.

One row in ``account_jobs`` is one attempt to turn an imported mailbox into a
sellable account. The stage column is the source of truth for the UI and for
resume; every transition is written before the next remote action starts, so a
crash leaves an honest "where it stopped" instead of a silent loss.

Deliberately resumable rather than clever: an operator can re-run any row, and
rows that hit a human gate wait in ``needs_human`` instead of retrying forever.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core import card_pool, signup, subscribe
from core.browser_pool import BrowserPoolError
from core.crypto import decrypt_blob, encrypt_blob
from core.mail_reader import GmailReader, MailError
from core.profile_manager import ProfileError
from core.proxy_inspector import ProxyKillSwitchError
from db.database import Database

logger = logging.getLogger(__name__)

TERMINAL = {"ready", "failed"}
HUMAN = "needs_human"


class PipelineError(RuntimeError):
    pass


@dataclass
class ImportRow:
    email: str
    app_password: str
    ip: str
    proxy_url: str = ""
    plan: str = "pro"
    password: str = ""


def _profile_id_for(email: str) -> str:
    """Derive a stable, filename-safe profile id from the mailbox."""
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")[:40] or "acct"
    return base


def parse_import(text: str, *, default_proxy_template: str = "") -> tuple[list[ImportRow], list[str]]:
    """Parse pasted rows of `email,app_password[,ip[,proxy[,plan]]]`.

    A trailing template can build the proxy URL from the IP, so the common case
    (one static proxy per IP, same port/user) is one column instead of five.
    """
    rows: list[ImportRow] = []
    errors: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [cell.strip() for cell in line.split(",")]
        email = parts[0].lower()
        if "@" not in email:
            errors.append(f"第 {number} 行：邮箱格式无效")
            continue
        if email in seen:
            errors.append(f"第 {number} 行：邮箱重复（{email}）")
            continue
        if len(parts) < 2 or not parts[1]:
            errors.append(f"第 {number} 行：缺少 Gmail 应用专用密码")
            continue
        ip = parts[2] if len(parts) >= 3 else ""
        if not ip:
            errors.append(f"第 {number} 行：缺少 IP")
            continue
        proxy = parts[3] if len(parts) >= 4 else ""
        if not proxy and default_proxy_template:
            proxy = default_proxy_template.replace("{ip}", ip)
        if not proxy:
            errors.append(f"第 {number} 行：缺少代理地址，且未配置模板")
            continue
        seen.add(email)
        rows.append(
            ImportRow(
                email=email,
                app_password=parts[1],
                ip=ip,
                proxy_url=proxy,
                plan=(parts[4] if len(parts) >= 5 and parts[4] else "pro"),
                password=parts[5] if len(parts) >= 6 else "",
            )
        )
    return rows, errors


def _password_for(email: str) -> str:
    """Import may omit a password; registration can invent one."""
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits + "!@#%^"
    return "".join(secrets.choice(alphabet) for _ in range(18))


async def create_job(db: Database, profiles: Any, row: ImportRow) -> dict[str, Any]:
    """Create the profile and its job row. Idempotent per mailbox."""
    existing = await db.fetch_one("SELECT * FROM account_jobs WHERE lower(email) = ?", (row.email,))
    if existing is not None:
        raise PipelineError(f"{row.email} 已在流程中（阶段 {existing['stage']}）")

    profile_id = _profile_id_for(row.email)
    suffix = 1
    while await db.get_profile(profile_id) is not None:
        suffix += 1
        profile_id = f"{_profile_id_for(row.email)}-{suffix}"

    try:
        await profiles.create_profile(
            profile_id=profile_id,
            upstream_proxy_url=row.proxy_url,
            expected_ip=row.ip,
        )
    except ProfileError as exc:
        raise PipelineError(str(exc)) from exc

    password = row.password or _password_for(row.email)
    await db.execute(
        "INSERT INTO account_jobs"
        " (profile_id, email, password_enc, gmail_app_password_enc, plan, stage)"
        " VALUES (?, ?, ?, ?, ?, 'queued')",
        (
            profile_id,
            row.email,
            encrypt_blob(password.encode(), associated=profile_id.encode()),
            encrypt_blob(row.app_password.encode(), associated=profile_id.encode()),
            row.plan,
        ),
    )
    created = await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))
    assert created is not None
    return created


def _public_job(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "profile_id": row["profile_id"],
        "email": row["email"],
        "plan": row["plan"],
        "stage": row["stage"],
        "attempts": row["attempts"],
        "last_error": row["last_error"],
        "human_note": row["human_note"],
        "updated_at": row["updated_at"],
    }


async def list_jobs(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM account_jobs ORDER BY created_at DESC")
    return [job for job in (_public_job(row) for row in rows) if job]


async def _set_stage(db: Database, profile_id: str, stage: str, *, error: str = "", note: str = "") -> None:
    await db.execute(
        "UPDATE account_jobs SET stage = ?, last_error = ?, human_note = ?,"
        " updated_at = CURRENT_TIMESTAMP WHERE profile_id = ?",
        (stage, error[:400], note[:400], profile_id),
    )
    if stage in {HUMAN, "failed"}:
        # Both mean "no browser is holding this profile". Keep it claimable so
        # pressing 继续 can resume: the job row, not the profile status, is the
        # place a human gate is recorded.
        await db.set_status(profile_id, "IDLE")


MAX_AUTO_ATTEMPTS = 3


@dataclass
class _Stop:
    """A park reason that is not a SignupOutcome (proxy/browser failures)."""

    message: str
    retryable: bool = False


async def _park(db: Database, profile_id: str, outcome: Any) -> None:
    """Record a stop. Retryable stops return to the queue until the cap is hit."""
    job = await db.fetch_one("SELECT attempts FROM account_jobs WHERE profile_id = ?", (profile_id,))
    attempts = int(job["attempts"]) if job else 0
    if getattr(outcome, "retryable", False) and attempts < MAX_AUTO_ATTEMPTS:
        await _set_stage(db, profile_id, "queued", note=outcome.message)
        return
    await _set_stage(db, profile_id, HUMAN, note=outcome.message)


async def run_job(db: Database, pool: Any, profile_id: str) -> dict[str, Any]:
    """Advance one job as far as it will go.

    Registration and subscription share a single browser context: closing it
    between them would drop the session the account was just created with, and
    re-opening on a fresh IP mid-flow is exactly what looks suspicious.
    """
    job = await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))
    if job is None:
        raise PipelineError(f"unknown job: {profile_id}")
    if job["stage"] in TERMINAL:
        return _public_job(job) or {}

    profile = await db.get_profile(profile_id)
    if profile is None:
        await _set_stage(db, profile_id, "failed", error="profile 不存在")
        raise PipelineError("profile 不存在")

    await db.execute(
        "UPDATE account_jobs SET attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP"
        " WHERE profile_id = ?",
        (profile_id,),
    )
    password = decrypt_blob(job["password_enc"], associated=profile_id.encode()).decode()
    app_password = decrypt_blob(job["gmail_app_password_enc"], associated=profile_id.encode()).decode()
    email = job["email"]

    try:
        async with pool.acquire_context(profile_id, headless=True) as context:
            page = await context.new_page()
            return await _drive(db, pool, profile_id, job, email, password, app_password, page)
    except ProxyKillSwitchError as exc:
        # Retry a few times first: proxies flake. If it keeps failing the
        # upstream really is dead and only an operator can fix the URL.
        await _park(
            db,
            profile_id,
            _Stop(f"代理不可用：{exc}", retryable=getattr(exc, "reason", "") != "ip_drift"),
        )
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}
    except BrowserPoolError as exc:
        await _park(db, profile_id, _Stop(f"浏览器启动失败：{exc}", retryable=True))
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}


async def _drive(
    db: Database,
    pool: Any,
    profile_id: str,
    job: dict[str, Any],
    email: str,
    password: str,
    app_password: str,
    page: Any,
) -> dict[str, Any]:
    """The remote half of a job; runs inside an open browser context."""
    await _set_stage(db, profile_id, "registering")
    outcome = await signup.run_signup(
        page,
        email=email,
        password=password,
        gmail_app_password=app_password,
    )
    if outcome.status == "needs_human":
        await _park(db, profile_id, outcome)
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}
    if outcome.status != "created":
        await _set_stage(db, profile_id, "failed", error=outcome.message)
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

    await _set_stage(db, profile_id, "registered")

    # Card is leased only now: a registration failure must not burn one.
    try:
        leased = await card_pool.lease_card(db)
    except card_pool.CardError as exc:
        await _set_stage(db, profile_id, HUMAN, note=f"注册成功，但{exc}")
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

    await _set_stage(db, profile_id, "subscribing")
    try:
        sub = await subscribe.run_subscribe(page, card=leased.as_dict(), plan=job["plan"])
    except Exception as exc:  # noqa: BLE001
        await card_pool.release_card(db, leased.card_id)
        await _set_stage(db, profile_id, HUMAN, note=f"订阅异常：{exc.__class__.__name__}")
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

    if sub.status == "subscribed":
        await card_pool.settle_card(db, leased.card_id, success=True)
        await _set_stage(db, profile_id, "subscribed")
    elif sub.status == "needs_human":
        await card_pool.release_card(db, leased.card_id)
        await _set_stage(db, profile_id, HUMAN, note=sub.message)
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}
    else:
        await card_pool.settle_card(db, leased.card_id, success=False, reason=sub.message)
        await _set_stage(db, profile_id, HUMAN, note=f"注册成功，订阅失败：{sub.message}")
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

    # The account is paid for; capture its CLI credentials while the session is
    # fresh. A credential failure is still deliverable-by-hand, so it only warns.
    try:
        await pool.sync_claude_cli_credentials(profile_id, email=email)
        await _set_stage(db, profile_id, "ready")
    except Exception as exc:  # noqa: BLE001
        await _set_stage(
            db,
            profile_id,
            HUMAN,
            note=f"已订阅 Pro，但取凭证失败：{exc.__class__.__name__}，可点「同步凭证」重试",
        )
    final = await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))
    return _public_job(final) or {}


async def run_pending(db: Database, pool: Any, *, limit: int = 20, concurrency: int = 2) -> dict[str, Any]:
    """Drain queued jobs with a small parallel window.

    Concurrency stays low because each job holds a full browser; the pool's own
    semaphore is the hard ceiling.
    """
    rows = await db.fetch_all(
        "SELECT profile_id FROM account_jobs WHERE stage = 'queued' ORDER BY created_at LIMIT ?",
        (limit,),
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[dict[str, Any]] = []

    async def _one(profile_id: str) -> None:
        async with semaphore:
            try:
                job = await run_job(db, pool, profile_id)
                results.append(job)
            except Exception as exc:  # noqa: BLE001
                logger.warning("job %s crashed: %s", profile_id, exc.__class__.__name__)
                await _set_stage(db, profile_id, "failed", error=exc.__class__.__name__)

    await asyncio.gather(*(_one(row["profile_id"]) for row in rows))
    stages: dict[str, int] = {}
    for job in results:
        stages[job.get("stage", "failed")] = stages.get(job.get("stage", "failed"), 0) + 1
    return {"ran": len(results), "stages": stages}


async def check_mailbox(email: str, app_password: str) -> dict[str, Any]:
    from core.mail_reader import check_credentials

    return await asyncio.to_thread(check_credentials, email, app_password)


__all__ = [
    "ImportRow",
    "PipelineError",
    "check_mailbox",
    "create_job",
    "list_jobs",
    "parse_import",
    "run_job",
    "run_pending",
]

_ = (MailError, GmailReader, datetime)
