from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import SessionLocal, init_db
from app.security import limiter
from app.services import direct_crypto
from app.services import redeem as redeem_service
from app.services import runtime_settings as runtime
from app.services.orders import OrderError
from app.services.redeem import RedeemError

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    try:
        with SessionLocal() as db:
            cleared = redeem_service.cleanup_sensitive_tokens(db)
            if cleared:
                logger.info("Cleared %s expired session credentials", cleared)
    except Exception:
        logger.exception("Startup cleanup failed")
    logger.info(
        "%s starting: env=%s task_mode=%s payment_mode=%s upstream_mode=%s self_vcc=%s",
        settings.brand_name_en,
        settings.app_env,
        settings.task_mode,
        settings.payment_mode,
        settings.upstream_mode,
        settings.self_vcc_enabled,
    )
    yield


app = FastAPI(
    title=f"{settings.brand_name_en} API",
    description=f"{settings.brand_name_cn} · {settings.brand_subtitle}",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("X-Brand", settings.brand_name_en)
    return response


@app.exception_handler(RedeemError)
async def redeem_error_handler(_: Request, exc: RedeemError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


@app.exception_handler(OrderError)
async def order_error_handler(_: Request, exc: OrderError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    return {"status": "ok", "service": "moliply-api", "version": "1.0.0"}


@app.get("/api/v1/brand", tags=["brand"])
def brand() -> dict:
    crypto_provider = runtime.get("crypto_provider", settings.crypto_provider).lower()
    if crypto_provider == "usdt_trc20":
        enabled_coins = direct_crypto.enabled_coins()
        crypto_configured = bool(enabled_coins)
        crypto_coins = [direct_crypto.coin_label(coin) for coin in enabled_coins]
    else:
        crypto_configured = bool(
            runtime.get("nowpayments_api_key", settings.nowpayments_api_key)
            and runtime.get("nowpayments_ipn_secret", settings.nowpayments_ipn_secret)
        )
        crypto_coins = runtime.get_list("crypto_supported_coins", settings.crypto_coin_list)
    crypto_enabled = runtime.get_bool("crypto_enabled", settings.crypto_enabled) and crypto_configured
    return {
        "name_en": settings.brand_name_en,
        "name_cn": settings.brand_name_cn,
        "subtitle": settings.brand_subtitle,
        "hero_slogan": settings.brand_hero_slogan,
        "code_prefix": settings.brand_code_prefix,
        "palette": {
            "bg": settings.brand_theme_bg,
            "primary": settings.brand_theme_primary,
            "accent": settings.brand_theme_accent,
        },
        "store_url": settings.public_store_url,
        "redeem_url": settings.public_redeem_url,
        "payment_mode": runtime.get("payment_mode", settings.payment_mode),
        "mock_payment_enabled": runtime.get_bool("mock_payment_enabled", settings.mock_payment_enabled),
        "crypto_enabled": crypto_enabled,
        "crypto_ready": crypto_configured,
        "crypto_provider": crypto_provider,
        "crypto_coins": crypto_coins,
        "crypto_coin_options": (
            [{"key": coin, "label": direct_crypto.coin_label(coin)} for coin in enabled_coins]
            if crypto_provider == "usdt_trc20"
            else []
        ),
        "support_wechat": runtime.get("support_wechat", settings.support_wechat),
        "support_hours": runtime.get("support_hours", settings.support_hours),
        "catalog_seeded": settings.seed_demo_data,
    }


from app.api import admin_manage, auth, payments, public_redeem, store  # noqa: E402

app.include_router(auth.router)
app.include_router(store.router)
app.include_router(public_redeem.router)
app.include_router(payments.router)
app.include_router(admin_manage.router)
app.include_router(admin_manage.protected)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.debug)
