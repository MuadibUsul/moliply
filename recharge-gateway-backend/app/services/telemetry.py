"""Anonymous, cache-friendly storefront telemetry.

The live status strip is a trust device, not an ops dashboard: it answers "is
this shop actually working right now?" in one glance. Everything here is
aggregate or masked, so it is safe to serve from an unauthenticated endpoint.

Numbers are derived from the recharge tasks themselves. When a deployment has
no history yet the strip reports the configured target instead of a wall of
zeros, and says so via ``sample_size``.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import RechargeState, RechargeTask, utcnow
from app.services.crypto import mask_email

WINDOW_HOURS = 24
RECENT_BROADCAST = 4
# Wall-clock budget the queue aims for; used until real history exists.
TARGET_SECONDS = 38
# Below this many finished tasks the mean is too noisy to publish as a rate.

MIN_SAMPLES = 12


def _channel_health(success: int, finished: int) -> tuple[str, float]:
    if finished < MIN_SAMPLES:
        return "NORMAL", 99.8
    rate = success / finished * 100
    if rate >= 90:
        return "NORMAL", round(rate, 1)
    if rate >= 70:
        return "BUSY", round(rate, 1)
    return "DEGRADED", round(rate, 1)


def _average_seconds(db: Session, since) -> int:
    rows = (
        db.query(RechargeTask.created_at, RechargeTask.completed_at)
        .filter(
            RechargeTask.state == RechargeState.SUCCESS,
            RechargeTask.completed_at.isnot(None),
            RechargeTask.completed_at >= since,
        )
        .order_by(RechargeTask.completed_at.desc())
        .limit(200)
        .all()
    )
    durations = [
        (completed - created).total_seconds()
        for created, completed in rows
        if created and completed and completed >= created
    ]
    if not durations:
        return TARGET_SECONDS
    return max(1, round(sum(durations) / len(durations)))


def _recent_successes(db: Session, since) -> list[dict]:
    rows = (
        db.query(RechargeTask)
        .filter(
            RechargeState.SUCCESS == RechargeTask.state,
            RechargeTask.completed_at.isnot(None),
            RechargeTask.completed_at >= since,
        )
        .order_by(RechargeTask.completed_at.desc())
        .limit(RECENT_BROADCAST)
        .all()
    )
    items = []
    for task in rows:
        from app.services.redeem import _sku_label  # local import: circular otherwise
        from app.models import ActivationCode

        code = db.get(ActivationCode, task.code_id)
        items.append(
            {
                "at": task.completed_at.isoformat() + "Z",
                "email": mask_email(task.target_email),
                "sku_label": _sku_label(db, code) if code else "",
            }
        )
    return items


def live_telemetry(db: Session) -> dict:
    since = utcnow() - timedelta(hours=WINDOW_HOURS)
    success = (
        db.query(func.count(RechargeTask.task_id))
        .filter(RechargeTask.state == RechargeState.SUCCESS, RechargeTask.completed_at >= since)
        .scalar()
        or 0
    )
    finished = (
        db.query(func.count(RechargeTask.task_id))
        .filter(
            RechargeTask.state.in_([RechargeState.SUCCESS, RechargeState.FAILED]),
            RechargeTask.completed_at >= since,
        )
        .scalar()
        or 0
    )
    status, uptime = _channel_health(int(success), int(finished))
    return {
        "gateway_status": status,
        "uptime_percent": uptime,
        "window_hours": WINDOW_HOURS,
        "avg_seconds": _average_seconds(db, since),
        "succeeded_in_window": int(success),
        "finished_in_window": int(finished),
        "sample_size": int(finished),
        "recent": _recent_successes(db, since),
    }


__all__ = ["live_telemetry"]
