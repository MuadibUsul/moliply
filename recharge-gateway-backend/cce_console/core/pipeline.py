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
from datetime import datetime, timezone
from typing import Any

from core import card_pool, signup, subscribe, warmup
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
    warmup_days: int | None = None


def _looks_like_proxy(cell: str) -> bool:
    """A cell carrying a URL or credentials rather than a bare address."""
    return "://" in cell or "@" in cell


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
        proxy = parts[3] if len(parts) >= 4 else ""
        # The third column used to be an IP and nothing else. Accept it carrying
        # the proxy itself too, which is what a one-column paste looks like.
        if ip and _looks_like_proxy(ip):
            if not proxy:
                proxy, ip = ip, ""
        if not proxy and default_proxy_template and ip:
            proxy = default_proxy_template.replace("{ip}", ip)
        if not proxy:
            errors.append(f"第 {number} 行：缺少代理地址，且未配置模板")
            continue
        # An omitted IP is legal: the importer probes the proxy for it. What is
        # not legal is neither column, which the branch above already caught.
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


async def create_job(
    db: Database,
    profiles: Any,
    row: ImportRow,
    *,
    warmup_days: int | None = None,
) -> dict[str, Any]:
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

    resolved_warmup = (
        row.warmup_days
        if row.warmup_days is not None
        else (warmup_days if warmup_days is not None else warmup.default_warmup_days())
    )
    resolved_warmup = max(0, min(14, int(resolved_warmup)))
    password = row.password or _password_for(row.email)
    await db.execute(
        "INSERT INTO account_jobs"
        " (profile_id, email, password_enc, gmail_app_password_enc, plan, stage, warmup_days, warmup_completed, next_action_at)"
        " VALUES (?, ?, ?, ?, ?, 'queued', ?, 0, NULL)",
        (
            profile_id,
            row.email,
            encrypt_blob(password.encode(), associated=profile_id.encode()),
            encrypt_blob(row.app_password.encode(), associated=profile_id.encode()),
            row.plan,
            resolved_warmup,
        ),
    )
    created = await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))
    assert created is not None
    return created


def _public_job(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    w_days = row.get("warmup_days")
    return {
        "profile_id": row["profile_id"],
        "email": row["email"],
        "plan": row["plan"],
        "stage": row["stage"],
        "attempts": row["attempts"],
        "warmup_days": int(3 if w_days is None else w_days),
        "warmup_completed": int(row.get("warmup_completed") or 0),
        "next_action_at": row.get("next_action_at"),
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


async def run_job(
    db: Database,
    pool: Any,
    profile_id: str,
    *,
    fast_track: bool = False,
) -> dict[str, Any]:
    """Advance one job as far as it will go.

    Supports a multi-day free-account warm-up pool (`warmup_days > 0`):
    - Day 0: register account + run warm-up conversation round 1 -> schedule Day 1
    - Day 1..N: run daily warm-up conversation rounds on the same residential IP
    - Graduation: once `warmup_completed >= warmup_days` (or `fast_track=True`),
      bind a single-use VCC card (AVS-matched to the proxy state), upgrade to
      Pro, and capture CLI credentials.
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

    already_registered = (
        job["stage"] in {"registered", "subscribing"}
        or int(job.get("warmup_completed") or 0) > 0
    )
    if fast_track:
        w_total = int(job.get("warmup_days") if job.get("warmup_days") is not None else 3)
        await db.execute(
            "UPDATE account_jobs SET warmup_completed = ?, next_action_at = NULL,"
            " plan = CASE WHEN plan = 'free_pool' THEN 'pro' ELSE plan END,"
            " updated_at = CURRENT_TIMESTAMP WHERE profile_id = ?",
            (max(w_total, int(job.get("warmup_completed") or 0)), profile_id),
        )
        job = await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))
        assert job is not None

    claim_stage = "registered" if already_registered else "registering"
    await db.execute(
        "UPDATE account_jobs SET stage = ?, attempts = attempts + 1, next_action_at = NULL,"
        " updated_at = CURRENT_TIMESTAMP WHERE profile_id = ?",
        (claim_stage, profile_id),
    )
    password = decrypt_blob(job["password_enc"], associated=profile_id.encode()).decode()
    app_password = decrypt_blob(job["gmail_app_password_enc"], associated=profile_id.encode()).decode()
    email = job["email"]

    try:
        async with pool.acquire_context(profile_id) as context:
            page = await context.new_page()
            return await _drive(
                db,
                pool,
                profile_id,
                job,
                email,
                password,
                app_password,
                page,
                context=context,
                already_registered=already_registered,
                fast_track=fast_track,
            )
    except ProxyKillSwitchError as exc:
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
    *,
    context: Any | None = None,
    already_registered: bool = False,
    fast_track: bool = False,
) -> dict[str, Any]:
    """The remote half of a job; runs inside an open browser context."""
    profile_geo = await db.get_profile_geo(profile_id)
    if not already_registered and warmup.strict_isp_proxy_enabled():
        if isinstance(profile_geo, dict) and profile_geo.get("is_hosting"):
            await _set_stage(
                db,
                profile_id,
                HUMAN,
                note="防风控拦截：检测到当前代理为机房/云主机 IP（极易触发 KYC 或秒封），请更换为美国静态原生 ISP 住宅代理后再试",
            )
            return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

    if not already_registered:
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

    w_days = int(job.get("warmup_days") if job.get("warmup_days") is not None else warmup.default_warmup_days())
    w_done = int(job.get("warmup_completed") or 0)
    plan = str(job.get("plan") or "pro").strip().lower()

    if not fast_track and w_done < w_days:
        round_no = w_done + 1
        w_res = await warmup.run_warmup_session(page, round_index=round_no)
        if not w_res.get("ok") and "登录态已失效" in str(w_res.get("reason") or ""):
            await _set_stage(db, profile_id, HUMAN, note=str(w_res.get("reason")))
            return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

        if round_no < w_days:
            next_ts = warmup.next_warmup_timestamp()
            note = f"白号养号池第 {round_no}/{w_days} 天已完成自然对话，下次自动养号：{next_ts[:16].replace('T', ' ')} UTC"
        elif plan == "free_pool":
            next_ts = None
            note = f"已完成全部 {w_days}/{w_days} 天高权重养号，作为成熟白号在池待命（随时可点「立即出池开 Pro」）"
        else:
            # Final warm-up round completed; rest briefly (4h cooldown) before binding a payment card.
            next_ts = warmup.next_warmup_timestamp(base_hours=min(4.0, warmup.default_interval_hours()))
            note = f"已完成全部 {w_days}/{w_days} 天自然养号，冷却至 {next_ts[:16].replace('T', ' ')} UTC 后自动绑卡开 Pro"

        await db.execute(
            "UPDATE account_jobs SET stage = 'registered', warmup_completed = ?, next_action_at = ?,"
            " human_note = ?, last_error = '', updated_at = CURRENT_TIMESTAMP WHERE profile_id = ?",
            (round_no, next_ts, note[:400], profile_id),
        )
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

    if not fast_track and plan == "free_pool":
        note = f"成熟白号在池待命（已完成 {w_done}/{w_days} 天养号，随时可一键出池开 Pro）"
        await db.execute(
            "UPDATE account_jobs SET stage = 'registered', next_action_at = NULL,"
            " human_note = ?, updated_at = CURRENT_TIMESTAMP WHERE profile_id = ?",
            (note[:400], profile_id),
        )
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

    # Card is leased only now: a registration failure or warm-up round never burns a card.
    # Pass the profile's geo so AVS billing ZIP matches the residential proxy state.
    try:
        leased = await card_pool.lease_card(db, profile_id=profile_id, geo=profile_geo)
    except card_pool.CardError as exc:
        await _set_stage(db, profile_id, HUMAN, note=f"养号完成，但{exc}")
        return _public_job(await db.fetch_one("SELECT * FROM account_jobs WHERE profile_id = ?", (profile_id,))) or {}

    target_plan = "pro" if plan in {"", "free_pool"} else plan
    await _set_stage(db, profile_id, "subscribing")
    try:
        sub = await subscribe.run_subscribe(page, card=leased.as_dict(), plan=target_plan)
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
        try:
            await pool.sync_claude_cli_credentials(profile_id, email=email, context=context)
        except TypeError:
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


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def pool_inventory_summary(db: Database) -> dict[str, int]:
    """Summarize ready Pro stock vs. mature free accounts vs. warming free accounts."""
    now_iso = _now_utc_iso()
    ready_row = await db.fetch_one(
        """
        SELECT COUNT(*) AS n
        FROM profiles p
        WHERE p.status = 'IDLE'
          AND p.claude_credentials_enc IS NOT NULL
          AND length(p.claude_credentials_enc) > 1
          AND NOT EXISTS (
              SELECT 1 FROM delivery_keys k
              WHERE k.profile_id = p.profile_id
                AND k.is_active = 1
                AND k.expires_at > ?
          )
        """,
        (now_iso,),
    )
    mature_row = await db.fetch_one(
        """
        SELECT COUNT(*) AS n
        FROM account_jobs j
        JOIN profiles p ON p.profile_id = j.profile_id
        WHERE j.stage = 'registered'
          AND p.status = 'IDLE'
          AND COALESCE(j.warmup_completed, 0) >= CASE WHEN COALESCE(j.warmup_days, 3) < 1 THEN 1 ELSE COALESCE(j.warmup_days, 3) END
        """
    )
    warming_row = await db.fetch_one(
        """
        SELECT COUNT(*) AS n
        FROM account_jobs j
        WHERE j.stage = 'registered'
          AND COALESCE(j.warmup_completed, 0) < CASE WHEN COALESCE(j.warmup_days, 3) < 1 THEN 1 ELSE COALESCE(j.warmup_days, 3) END
        """
    )
    in_flight_pro = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM account_jobs WHERE stage IN ('subscribing', 'subscribed')"
    )
    target = warmup.hot_standby_target()
    ready_pro = int(ready_row["n"] if ready_row else 0)
    upgrading = int(in_flight_pro["n"] if in_flight_pro else 0)
    mature_free = int(mature_row["n"] if mature_row else 0)
    warming_free = int(warming_row["n"] if warming_row else 0)
    deficit = max(0, target - (ready_pro + upgrading))
    return {
        "ready_pro": ready_pro,
        "mature_free": mature_free,
        "warming_free": warming_free,
        "upgrading_pro": upgrading,
        "hot_standby_target": target,
        "hot_standby_deficit": min(deficit, mature_free),
    }


async def next_mature_free_profile_id(db: Database) -> str | None:
    """Pick the oldest mature free account ready to be promoted to Pro."""
    row = await db.fetch_one(
        """
        SELECT j.profile_id
        FROM account_jobs j
        JOIN profiles p ON p.profile_id = j.profile_id
        WHERE j.stage = 'registered'
          AND p.status = 'IDLE'
          AND COALESCE(j.warmup_completed, 0) >= CASE WHEN COALESCE(j.warmup_days, 3) < 1 THEN 1 ELSE COALESCE(j.warmup_days, 3) END
        ORDER BY j.created_at ASC
        LIMIT 1
        """
    )
    return str(row["profile_id"]) if row else None


async def has_actionable_jobs(db: Database) -> bool:
    row = await db.fetch_one(
        "SELECT profile_id FROM account_jobs"
        " WHERE stage = 'queued'"
        "    OR (stage = 'registered' AND next_action_at IS NOT NULL AND next_action_at <= ?)"
        " LIMIT 1",
        (_now_utc_iso(),),
    )
    if row is not None:
        return True
    summary = await pool_inventory_summary(db)
    return summary["hot_standby_deficit"] > 0


async def run_pending(db: Database, pool: Any, *, limit: int = 20, concurrency: int = 2) -> dict[str, Any]:
    """Drain queued jobs, due warm-up rounds, and hot-standby promotions."""
    rows = await db.fetch_all(
        "SELECT profile_id, 0 AS fast_track FROM account_jobs"
        " WHERE stage = 'queued'"
        "    OR (stage = 'registered' AND next_action_at IS NOT NULL AND next_action_at <= ?)"
        " ORDER BY COALESCE(next_action_at, created_at) ASC LIMIT ?",
        (_now_utc_iso(), limit),
    )
    picked_ids = {str(r["profile_id"]) for r in rows}
    remaining_slots = max(0, limit - len(rows))
    if remaining_slots > 0:
        summary = await pool_inventory_summary(db)
        need_promote = min(remaining_slots, summary["hot_standby_deficit"])
        if need_promote > 0:
            mature_rows = await db.fetch_all(
                """
                SELECT j.profile_id
                FROM account_jobs j
                JOIN profiles p ON p.profile_id = j.profile_id
                WHERE j.stage = 'registered'
                  AND p.status = 'IDLE'
                  AND COALESCE(j.warmup_completed, 0) >= CASE WHEN COALESCE(j.warmup_days, 3) < 1 THEN 1 ELSE COALESCE(j.warmup_days, 3) END
                ORDER BY j.created_at ASC
                LIMIT ?
                """,
                (need_promote,),
            )
            for mrow in mature_rows:
                pid = str(mrow["profile_id"])
                if pid not in picked_ids:
                    rows.append({"profile_id": pid, "fast_track": 1})
                    picked_ids.add(pid)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[dict[str, Any]] = []

    async def _one(profile_id: str, fast: bool = False) -> None:
        async with semaphore:
            try:
                job = await run_job(db, pool, profile_id, fast_track=fast)
                results.append(job)
            except Exception as exc:  # noqa: BLE001
                logger.warning("job %s crashed: %s", profile_id, exc.__class__.__name__)
                await _set_stage(db, profile_id, "failed", error=exc.__class__.__name__)

    await asyncio.gather(*(_one(row["profile_id"], bool(row.get("fast_track"))) for row in rows))
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
