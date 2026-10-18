import logging
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    AccountInventory,
    ActivationCode,
    AuditLog,
    ChannelMode,
    CodeStatus,
    InventoryStatus,
    Order,
    OrderStatus,
    Product,
    RegionPreference,
    RechargeState,
    RechargeTask,
    SKU_LABELS,
    SkuType,
    User,
    VccCard,
    VccStatus,
    utcnow,
)
from app.schemas import (
    AccountImportRequest,
    AdminLoginRequest,
    AdminResetPasswordRequest,
    CodeGenerateRequest,
    CodeImportRequest,
    CodeStatusRequest,
    ProductUpdateRequest,
    SettingsUpdateRequest,
    VccCardBatchImportRequest,
    VccCardCreateRequest,
    RoutingPolicyRequest,
    VccCardUpdateRequest,
)
from app.services.accounts import hash_password
from app.security import client_ip, create_admin_token, limiter, require_admin, verify_admin_password
from app.services import orders as order_service
from app.services import redeem as redeem_service
from app.services import feishu
from app.services import runtime_settings
from app.services.catalog import available_stock, money, serialize_product
from app.services.code_generator import generate_code, is_valid_code_format, normalize_code
from app.services.crypto import decrypt_text, encrypt_text, luhn_valid, mask_card_number, mask_email
from app.services import vcc_pool_scheduler
from app.services.self_vcc_worker import select_vcc_card
from app.services.upstream_provider import UpstreamProvider

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin-public"])
protected = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])

CODE_STATUS_LABELS = {
    "UNUSED": "未使用",
    "LOCKED": "执行中",
    "USED": "已核销",
    "DISABLED": "已作废",
}
TASK_STATUS_LABELS = {
    "PENDING": "排队中",
    "CHECKING_ACCOUNT": "核验账号",
    "CREATING_CHECKOUT": "创建支付",
    "PAYING": "提交支付",
    "AWAITING_3DS": "等待 3DS",
    "SUCCESS": "充值成功",
    "FAILED": "充值失败",
}
VCC_STATUS_LABELS = {"ACTIVE": "可用", "EXHAUSTED": "待销卡回血", "SUSPENDED": "风控挂起", "CLOSED": "已销卡"}


@router.post("/login")
@limiter.limit(settings.admin_login_rate_limit)
def admin_login(payload: AdminLoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    import secrets

    username_ok = secrets.compare_digest(payload.username, settings.admin_username)
    password_ok = verify_admin_password(payload.password)
    ip = client_ip(request)
    if not (username_ok and password_ok):
        try:
            db.add(
                AuditLog(
                    actor=payload.username[:64],
                    action="admin.login_failed",
                    entity_type="admin",
                    entity_id=payload.username[:64],
                    detail="invalid credentials",
                    ip=ip,
                )
            )
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    try:
        db.add(
            AuditLog(
                actor=payload.username,
                action="admin.login",
                entity_type="admin",
                entity_id=payload.username,
                detail="success",
                ip=ip,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    token, ttl = create_admin_token(payload.username)
    return {"token": token, "expires_in": ttl, "username": payload.username}


def _serialize_code(db: Session, code: ActivationCode) -> dict:
    from app.models import OrderItem

    product_name = ""
    if code.order_item_id:
        item = db.get(OrderItem, code.order_item_id)
        product_name = f"{item.product_name} · {item.spec_label}".strip(" ·") if item else ""
    return {
        "id": code.id,
        "code": code.code,
        "sku_type": code.sku_type.value,
        "sku_label": SKU_LABELS.get(code.sku_type.value, code.sku_type.value),
        "channel_mode": code.channel_mode.value,
        "preferred_region": code.preferred_region.value,
        "status": code.status.value,
        "status_label": CODE_STATUS_LABELS.get(code.status.value, code.status.value),
        "bound_email_masked": mask_email(code.bound_account_email) if code.bound_account_email else None,
        "product_name": product_name,
        "note": code.note,
        "created_at": code.created_at.isoformat() + "Z",
        "used_at": code.used_at.isoformat() + "Z" if code.used_at else None,
    }


def _serialize_task(db: Session, task: RechargeTask) -> dict:
    payload = redeem_service.serialize_task(db, task)
    payload["target_email_full"] = task.target_email
    payload["attempts"] = task.attempts
    payload["upstream_order_no"] = task.upstream_order_no
    payload["vcc_card_id"] = task.vcc_card_id
    payload["state_label"] = TASK_STATUS_LABELS.get(task.state.value, task.state.value)
    return payload


@protected.get("/overview")
def overview(days: int = Query(default=12, ge=7, le=30), db: Session = Depends(get_db)) -> dict:
    now = utcnow()
    today_start = datetime(now.year, now.month, now.day)
    orders_total = db.query(func.count(Order.id)).scalar() or 0
    revenue_today = (
        db.query(func.coalesce(func.sum(Order.amount), 0))
        .filter(Order.paid_at >= today_start, Order.status.in_([OrderStatus.PAID, OrderStatus.DELIVERED]))
        .scalar()
        or Decimal("0")
    )
    pending_orders = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.PENDING).scalar() or 0
    paid_orders = (
        db.query(func.count(Order.id))
        .filter(Order.status.in_([OrderStatus.PAID, OrderStatus.DELIVERED]))
        .scalar()
        or 0
    )
    delivered_orders = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.DELIVERED).scalar() or 0
    delivery_rate = round(delivered_orders / paid_orders * 100, 1) if paid_orders else 0.0

    start = today_start - timedelta(days=days - 1)
    rows = (
        db.query(func.date(Order.created_at), func.count(Order.id))
        .filter(Order.created_at >= start)
        .group_by(func.date(Order.created_at))
        .all()
    )
    counts = {str(row[0]): int(row[1]) for row in rows}
    trend = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        key = day.strftime("%Y-%m-%d")
        trend.append({"date": day.strftime("%m-%d"), "key": key, "count": counts.get(key, 0)})

    code_rows = db.query(ActivationCode.status, func.count(ActivationCode.id)).group_by(ActivationCode.status).all()
    code_counts = {status.value: int(count) for status, count in code_rows}
    codes_total = sum(code_counts.values())

    task_rows = db.query(RechargeTask.state, func.count(RechargeTask.task_id)).group_by(RechargeTask.state).all()
    task_counts = {state.value: int(count) for state, count in task_rows}

    manual_review = (
        db.query(func.count(RechargeTask.task_id))
        .join(ActivationCode, ActivationCode.id == RechargeTask.code_id)
        .filter(RechargeTask.state == RechargeState.FAILED, ActivationCode.status == CodeStatus.LOCKED)
        .scalar()
        or 0
    )

    latest = db.query(Order).order_by(Order.created_at.desc()).limit(6).all()
    upstream = UpstreamProvider().channel_status()
    self_vcc = {
        "name": "自营 VCC 通道",
        "status": "online" if runtime_settings.get_bool("self_vcc_enabled", settings.self_vcc_enabled) else "disabled",
        "label": "已启用" if runtime_settings.get_bool("self_vcc_enabled", settings.self_vcc_enabled) else "未启用",
        "detail": "演练模式" if runtime_settings.get_bool("self_vcc_dry_run", settings.self_vcc_dry_run) else "实盘模式",
    }
    return {
        "stats": {
            "orders_total": orders_total,
            "revenue_today": money(revenue_today),
            "pending_orders": pending_orders,
            "delivery_success_rate": delivery_rate,
            "codes_total": codes_total,
            "codes_unused": code_counts.get("UNUSED", 0),
            "codes_locked": code_counts.get("LOCKED", 0),
            "codes_used": code_counts.get("USED", 0),
            "tasks_total": sum(task_counts.values()),
            "tasks_success": task_counts.get("SUCCESS", 0),
            "tasks_failed": task_counts.get("FAILED", 0),
            "manual_review": manual_review,
        },
        "trend": trend,
        "channels": [
            {**upstream, "detail": "模拟模式" if settings.upstream_mode == "mock" else "实盘模式"},
            self_vcc,
            {
                "name": "待人工处理",
                "status": "attention" if manual_review else "online",
                "label": f"{manual_review} 个任务需要检查" if manual_review else "暂无积压",
                "detail": "人工复核",
            },
        ],
        "latest_orders": [
            order_service.serialize_order(db, order, reveal=False, reveal_email=False) for order in latest
        ],
    }


@protected.get("/orders")
def admin_orders(
    db: Session = Depends(get_db),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=160),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    query = db.query(Order)
    if status and status.upper() != "ALL":
        try:
            query = query.filter(Order.status == OrderStatus(status.upper()))
        except ValueError:
            raise HTTPException(status_code=400, detail="未知订单状态")
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(or_(Order.order_no.ilike(needle), Order.email.ilike(needle)))
    total = query.count()
    rows = (
        query.order_by(Order.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [order_service.serialize_order(db, order, reveal=True, reveal_email=True) for order in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@protected.get("/orders/{order_no}")
def admin_order_detail(order_no: str, db: Session = Depends(get_db)) -> dict:
    order = db.query(Order).filter(func.upper(Order.order_no) == order_no.upper()).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {"order": order_service.serialize_order(db, order, reveal=True, reveal_email=True)}


@protected.post("/orders/{order_no}/fulfill")
def admin_fulfill(order_no: str, db: Session = Depends(get_db), admin: dict = Depends(require_admin)) -> dict:
    order = db.query(Order).filter(func.upper(Order.order_no) == order_no.upper()).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status == OrderStatus.PENDING:
        raise HTTPException(status_code=409, detail="订单尚未支付，不能发货")
    order_service.fulfill_order(db, order)
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="order.fulfill",
            entity_type="order",
            entity_id=order.order_no,
            detail="manual retry",
        )
    )
    db.commit()
    return {"order": order_service.serialize_order(db, order, reveal=True, reveal_email=True)}


@protected.post("/orders/{order_no}/mark-paid")
def admin_mark_paid(order_no: str, db: Session = Depends(get_db), admin: dict = Depends(require_admin)) -> dict:
    order = db.query(Order).filter(func.upper(Order.order_no) == order_no.upper()).first()
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status != OrderStatus.PENDING:
        raise HTTPException(status_code=409, detail="订单当前状态不可标记为已支付")
    order_service.mark_order_paid(db, order, "manual", f"MANUAL-{order.order_no}")
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="order.mark_paid",
            entity_type="order",
            entity_id=order.order_no,
            detail="manual settlement",
        )
    )
    db.commit()
    return {"order": order_service.serialize_order(db, order, reveal=True, reveal_email=True)}


@protected.post("/users/reset-password")
def admin_reset_user_password(
    payload: AdminResetPasswordRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    user = (
        db.query(User)
        .filter(func.lower(User.email) == str(payload.email).strip().lower())
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="该邮箱没有对应的订单账户")
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    db.add(user)
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="user.reset_password",
            entity_type="user",
            entity_id=str(user.id),
            detail=user.email,
        )
    )
    db.commit()
    return {"ok": True, "email": user.email, "message": "密码已重置，旧登录凭证已失效"}


@protected.get("/settings")
def admin_get_settings(db: Session = Depends(get_db)) -> dict:
    data = runtime_settings.public_settings()
    data["callbacks"] = {
        "ltzf": runtime_settings.get("ltzf_notify_url"),
        "xunhupay": runtime_settings.get("xunhupay_notify_url"),
        "nowpayments": runtime_settings.get("nowpayments_ipn_url"),
    }
    data["feishu_event_options"] = feishu.event_options()
    data["recent_notifications"] = feishu.recent_logs(db)
    return data


@protected.post("/feishu/test")
def admin_test_feishu(
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    result = feishu.send_test_message(admin.get("sub", "admin"))
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="feishu.test",
            entity_type="notification",
            entity_id="feishu",
            detail=result.get("status", ""),
        )
    )
    db.commit()
    return {"result": result, "recent_notifications": feishu.recent_logs(db)}


@protected.patch("/settings")
def admin_update_settings(
    payload: SettingsUpdateRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    try:
        result = runtime_settings.update_settings(
            db,
            payload.values,
            payload.clear_secrets,
            actor=admin.get("sub", "admin"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="settings.update",
            entity_type="platform_setting",
            entity_id="runtime",
            detail=f"keys={','.join(sorted(payload.values.keys()))[:450]}",
        )
    )
    db.commit()
    result["callbacks"] = {
        "ltzf": runtime_settings.get("ltzf_notify_url"),
        "xunhupay": runtime_settings.get("xunhupay_notify_url"),
        "nowpayments": runtime_settings.get("nowpayments_ipn_url"),
    }
    result["feishu_event_options"] = feishu.event_options()
    result["recent_notifications"] = feishu.recent_logs(db)
    return result


@protected.get("/codes")
def admin_codes(
    db: Session = Depends(get_db),
    status: str | None = Query(default=None),
    sku_type: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    query = db.query(ActivationCode)
    if status and status.upper() != "ALL":
        try:
            query = query.filter(ActivationCode.status == CodeStatus(status.upper()))
        except ValueError:
            raise HTTPException(status_code=400, detail="未知卡密状态")
    if sku_type and sku_type.upper() != "ALL":
        try:
            query = query.filter(ActivationCode.sku_type == SkuType(sku_type.upper()))
        except ValueError:
            raise HTTPException(status_code=400, detail="未知 SKU")
    if q:
        query = query.filter(ActivationCode.code.ilike(f"%{q.strip().upper()}%"))
    total = query.count()
    rows = (
        query.order_by(ActivationCode.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    status_rows = db.query(ActivationCode.status, func.count(ActivationCode.id)).group_by(ActivationCode.status).all()
    counts = {status_value.value: int(count) for status_value, count in status_rows}
    return {
        "items": [_serialize_code(db, code) for code in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "stats": {
            "total": sum(counts.values()),
            "unused": counts.get("UNUSED", 0),
            "locked": counts.get("LOCKED", 0),
            "used": counts.get("USED", 0),
            "disabled": counts.get("DISABLED", 0),
        },
        "sku_options": [{"key": key, "label": label} for key, label in SKU_LABELS.items()],
    }


@protected.post("/codes/generate")
def admin_generate_codes(
    payload: CodeGenerateRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    created: list[str] = []
    seen: set[str] = set()
    attempts = 0
    while len(created) < payload.count and attempts < payload.count * 20:
        attempts += 1
        value = generate_code()
        if value in seen:
            continue
        if db.query(ActivationCode.id).filter(ActivationCode.code == value).first():
            continue
        seen.add(value)
        db.add(
            ActivationCode(
                code=value,
                sku_type=SkuType(payload.sku_type),
                channel_mode=ChannelMode(payload.channel_mode),
                preferred_region=RegionPreference(payload.preferred_region),
                status=CodeStatus.UNUSED,
                note=payload.note,
            )
        )
        created.append(value)
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="codes.generate",
            entity_type="activation_code",
            entity_id="batch",
            detail=f"count={len(created)} sku={payload.sku_type}",
        )
    )
    db.commit()
    return {"created": len(created), "codes": created[:50], "sku_type": payload.sku_type}


@protected.post("/codes/import")
def admin_import_codes(
    payload: CodeImportRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    created: list[str] = []
    skipped = 0
    seen: set[str] = set()
    lines = [line.strip() for line in payload.text.splitlines() if line.strip()]
    for line in lines:
        parts = [part.strip() for part in line.replace("\t", ",").split(",")]
        raw_code = parts[0]
        upstream_code = parts[1] if len(parts) > 1 else ""
        normalized = normalize_code(raw_code)
        if is_valid_code_format(normalized):
            local_code = normalized
        else:
            local_code = generate_code()
            upstream_code = upstream_code or raw_code
        if local_code in seen:
            skipped += 1
            continue
        if db.query(ActivationCode.id).filter(ActivationCode.code == local_code).first():
            skipped += 1
            continue
        seen.add(local_code)
        db.add(
            ActivationCode(
                code=local_code,
                sku_type=SkuType(payload.sku_type),
                channel_mode=ChannelMode(payload.channel_mode),
                preferred_region=RegionPreference(payload.preferred_region),
                status=CodeStatus.UNUSED,
                upstream_code_enc=encrypt_text(upstream_code) if upstream_code else None,
                note=payload.note or "批量导入",
            )
        )
        created.append(local_code)
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="codes.import",
            entity_type="activation_code",
            entity_id="batch",
            detail=f"created={len(created)} skipped={skipped}",
        )
    )
    db.commit()
    return {"created": len(created), "skipped": skipped, "codes": created[:20]}


@protected.patch("/codes/{code_id}/status")
def admin_code_status(
    code_id: int,
    payload: CodeStatusRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    code = db.get(ActivationCode, code_id)
    if not code:
        raise HTTPException(status_code=404, detail="激活码不存在")
    target = CodeStatus(payload.status)
    if target == CodeStatus.DISABLED:
        if code.status != CodeStatus.UNUSED:
            raise HTTPException(status_code=409, detail="仅未使用的激活码可以作废")
        code.status = CodeStatus.DISABLED
    elif target == CodeStatus.UNUSED:
        if code.status != CodeStatus.DISABLED:
            raise HTTPException(status_code=409, detail="仅已作废的激活码可以恢复")
        code.status = CodeStatus.UNUSED
    db.add(code)
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="code.status",
            entity_type="activation_code",
            entity_id=str(code.id),
            detail=target.value,
        )
    )
    db.commit()
    return {"item": _serialize_code(db, code)}


@protected.get("/tasks")
def admin_tasks(
    db: Session = Depends(get_db),
    state: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=160),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    query = db.query(RechargeTask)
    if state and state.upper() != "ALL":
        try:
            query = query.filter(RechargeTask.state == RechargeState(state.upper()))
        except ValueError:
            raise HTTPException(status_code=400, detail="未知任务状态")
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(or_(RechargeTask.task_id.ilike(needle), RechargeTask.target_email.ilike(needle)))
    total = query.count()
    rows = query.order_by(RechargeTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_serialize_task(db, task) for task in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@protected.post("/tasks/{task_id}/retry")
def admin_retry_task(
    task_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    task = redeem_service.retry_task(db, task_id, client_ip(request))
    return {"task": _serialize_task(db, task)}


@protected.post("/tasks/cleanup")
def admin_cleanup_tokens(db: Session = Depends(get_db)) -> dict:
    count = redeem_service.cleanup_sensitive_tokens(db)
    return {"cleared": count}


def _create_vcc_card(
    db: Session,
    payload: VccCardCreateRequest,
    actor: str,
    ip: str = "",
) -> tuple[VccCard, str]:
    digits = "".join(ch for ch in payload.card_number if ch.isdigit())
    if not luhn_valid(digits):
        raise HTTPException(status_code=400, detail="卡号未通过 Luhn 校验")
    if payload.billing_address.country != "US":
        raise HTTPException(status_code=400, detail="美国账单地址的 country 必须为 US")
    if payload.ph_billing_address and payload.ph_billing_address.country != "PH":
        raise HTTPException(status_code=400, detail="菲律宾账单地址的 country 必须为 PH")
    allowed_bins = {
        value.strip()
        for value in runtime_settings.get("us_bin_allowlist", settings.us_bin_allowlist).split(",")
        if value.strip()
    }
    if allowed_bins and digits[:6] not in allowed_bins:
        raise HTTPException(status_code=400, detail="卡 BIN 不在已配置的美国 BIN 白名单中")
    card = VccCard(
        provider=payload.provider,
        card_bin=digits[:6],
        card_number_enc=encrypt_text(digits),
        cvv_enc=encrypt_text(payload.cvv),
        exp_month=payload.exp_month.zfill(2),
        exp_year=payload.exp_year[-2:],
        holder_name=payload.holder_name,
        billing_address_json=payload.billing_address.model_dump(),
        ph_billing_address_json=payload.ph_billing_address.model_dump() if payload.ph_billing_address else None,
        three_ds_email_account=payload.three_ds_email_account,
        available_balance=Decimal(str(payload.available_balance)),
        max_bind_count=payload.max_bind_count,
        status=VccStatus.ACTIVE,
    )
    db.add(card)
    db.add(
        AuditLog(
            actor=actor,
            action="vcc.add",
            entity_type="vcc_card",
            entity_id=digits[-4:],
            detail=f"bin={card.card_bin} ph={bool(card.ph_billing_address_json)}",
            ip=ip,
        )
    )
    db.commit()
    return card, digits


@protected.get("/cards")
def admin_cards(db: Session = Depends(get_db)) -> dict:
    cards = db.query(VccCard).order_by(VccCard.created_at.desc()).all()
    items = []
    for card in cards:
        try:
            masked = mask_card_number(decrypt_text(card.card_number_enc))
        except Exception:
            masked = "****"
        items.append(
            {
                "id": card.id,
                "provider": card.provider,
                "card_bin": card.card_bin,
                "card_masked": masked,
                "exp_month": card.exp_month,
                "exp_year": card.exp_year,
                "holder_name": card.holder_name,
                "has_ph_billing": bool(card.ph_billing_address_json),
                "three_ds_email_masked": mask_email(card.three_ds_email_account) if card.three_ds_email_account else None,
                "available_balance": money(card.available_balance),
                "max_bind_count": card.max_bind_count,
                "current_bind_count": card.current_bind_count,
                "failed_count": card.failed_count,
                "status": card.status.value,
                "status_label": VCC_STATUS_LABELS.get(card.status.value, card.status.value),
                "created_at": card.created_at.isoformat() + "Z",
            }
        )
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "active": sum(1 for item in items if item["status"] == "ACTIVE"),
            "suspended": sum(1 for item in items if item["status"] == "SUSPENDED"),
            "available": sum(1 for item in items if item["status"] == "ACTIVE" and item["current_bind_count"] < item["max_bind_count"]),
            "exhausted": sum(1 for item in items if item["status"] == "EXHAUSTED"),
            "closed": sum(1 for item in items if item["status"] == "CLOSED"),
        },
    }


@protected.post("/cards")
def admin_add_card(
    payload: VccCardCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    card, digits = _create_vcc_card(db, payload, admin.get("sub", "admin"), client_ip(request))
    return {"id": card.id, "card_masked": mask_card_number(digits), "status": card.status.value}


@protected.post("/cards/batch-import")
def admin_batch_import_cards(
    payload: VccCardBatchImportRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    created = 0
    failed: list[str] = []
    for item in payload.cards:
        try:
            _create_vcc_card(db, item, admin.get("sub", "admin"), client_ip(request))
            created += 1
        except HTTPException as exc:
            db.rollback()
            failed.append(str(exc.detail))
    return {"created": created, "failed": failed}


@protected.get("/cards/summary")
def admin_card_summary(db: Session = Depends(get_db)) -> dict:
    data = vcc_pool_scheduler.summary(db)
    exhausted = db.query(VccCard).filter(VccCard.status == VccStatus.EXHAUSTED).all()
    data["pending_close"] = [
        {
            "id": card.id,
            "card_masked": "****" + "".join(ch for ch in decrypt_text(card.card_number_enc) if ch.isdigit())[-4:],
            "balance": money(card.available_balance),
            "binds": f"{card.current_bind_count}/{card.max_bind_count}",
        }
        for card in exhausted
    ]
    return data


@protected.post("/cards/{card_id}/mark-closed")
def admin_mark_card_closed(
    card_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    card = db.get(VccCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="卡片不存在")
    vcc_pool_scheduler.mark_closed(db, card)
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="vcc.mark_closed",
            entity_type="vcc_card",
            entity_id=str(card.id),
            detail="销卡回血标记",
        )
    )
    db.commit()
    return {"id": card.id, "status": card.status.value}


@protected.patch("/routing-policy")
def admin_routing_policy(
    payload: RoutingPolicyRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    runtime_settings.update_settings(
        db,
        {"routing_policy": payload.routing_policy},
        actor=admin.get("sub", "admin"),
    )
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="routing.policy",
            entity_type="platform_setting",
            entity_id="routing_policy",
            detail=payload.routing_policy,
        )
    )
    db.commit()
    return {"routing_policy": payload.routing_policy}


@protected.patch("/cards/{card_id}")
def admin_update_card(
    card_id: int,
    payload: VccCardUpdateRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    card = db.get(VccCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="卡片不存在")
    if payload.status:
        card.status = VccStatus(payload.status)
    if payload.available_balance is not None:
        card.available_balance = Decimal(str(payload.available_balance))
    if payload.reset_failed_count:
        card.failed_count = 0
    db.add(card)
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="vcc.update",
            entity_type="vcc_card",
            entity_id=str(card.id),
            detail=f"status={card.status.value}",
        )
    )
    db.commit()
    return {"id": card.id, "status": card.status.value}


@protected.get("/products")
def admin_products(db: Session = Depends(get_db)) -> dict:
    products = db.query(Product).order_by(Product.sort_order.asc()).all()
    items = []
    for product in products:
        data = serialize_product(db, product)
        data["stock"] = available_stock(db, product)
        data["account_stock"] = (
            db.query(func.count(AccountInventory.id))
            .filter(
                AccountInventory.product_id == product.id,
                AccountInventory.status == InventoryStatus.AVAILABLE,
            )
            .scalar()
            or 0
        )
        items.append(data)
    return {"items": items}


@protected.patch("/products/{product_id}")
def admin_update_product(
    product_id: int,
    payload: ProductUpdateRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    if payload.active is not None:
        product.active = payload.active
    if payload.price is not None:
        product.price = Decimal(str(payload.price))
    if payload.badge is not None:
        product.badge = payload.badge or None
    db.add(product)
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="product.update",
            entity_type="product",
            entity_id=str(product.id),
            detail=f"active={product.active}",
        )
    )
    db.commit()
    return {"item": serialize_product(db, product)}


@protected.post("/accounts/import")
def admin_import_accounts(
    payload: AccountImportRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> dict:
    product = db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    if product.delivery_type.value != "ACCOUNT_CREDENTIALS":
        raise HTTPException(status_code=409, detail="该商品不是账密类商品")
    lines = [line.strip() for line in payload.text.splitlines() if line.strip()]
    created = 0
    for line in lines:
        label = mask_email(line.split("----")[0].strip()) if "@" in line else "成品账号"
        db.add(
            AccountInventory(
                product_id=product.id,
                label=label,
                payload_enc=encrypt_text(line),
                status=InventoryStatus.AVAILABLE,
            )
        )
        created += 1
    db.add(
        AuditLog(
            actor=admin.get("sub", "admin"),
            action="accounts.import",
            entity_type="product",
            entity_id=str(product.id),
            detail=f"count={created}",
        )
    )
    db.commit()
    return {"created": created}


@protected.get("/channels")
def admin_channels(db: Session = Depends(get_db)) -> dict:
    manual_review = (
        db.query(func.count(RechargeTask.task_id))
        .join(ActivationCode, ActivationCode.id == RechargeTask.code_id)
        .filter(RechargeTask.state == RechargeState.FAILED, ActivationCode.status == CodeStatus.LOCKED)
        .scalar()
        or 0
    )
    card = select_vcc_card(db)
    return {
        "upstream": UpstreamProvider().channel_status(),
        "self_vcc": {
            "enabled": runtime_settings.get_bool("self_vcc_enabled", settings.self_vcc_enabled),
            "dry_run": runtime_settings.get_bool("self_vcc_dry_run", settings.self_vcc_dry_run),
            "available_card_id": card.id if card else None,
        },
        "manual_review": manual_review,
    }
