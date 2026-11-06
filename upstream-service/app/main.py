"""Upstream recharge service speaking MOLIPLY's provider contract.

MOLIPLY is the merchant; this service does the actual fulfillment. It owns its
own SQLite database so a restart or crash never loses an accepted order.

Fulfillment is pluggable via ``FULFILL_MODE``:

* ``manual`` - accept, park as pending, an operator settles in the admin UI.
* ``stock``  - claim a pre-imported code per SKU, mark it used, then success.

Every endpoint is HMAC-SHA256 authenticated with the shared secret; the body is
signed verbatim and the timestamp window stops replays.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from app.suppliers.base import SupplierRejected, SupplierUncertain, SupplierUnavailable
from app.suppliers.yunshu import YunshuSupplier
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("upstream")

DB_PATH = os.environ.get("DB_PATH", "/data/upstream.db")
MERCHANT_KEY = os.environ.get("UPSTREAM_KEY", "")
MERCHANT_SECRET = os.environ.get("UPSTREAM_SECRET", "")
ADMIN_TOKEN = os.environ.get("UPSTREAM_ADMIN_TOKEN", "")
REPLAY_WINDOW_SECONDS = int(os.environ.get("REPLAY_WINDOW_SECONDS", "300"))
FULFILL_MODE = os.environ.get("FULFILL_MODE", "manual").lower()
IDEMPOTENCY_TTL_SECONDS = int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", "86400"))
MAX_PENDING_PER_SKU = int(os.environ.get("MAX_PENDING_PER_SKU", "0"))

TERMINAL = ("success", "failed")
SUPPLIER_NAME = os.environ.get("SUPPLIER", "").lower()
SUPPLIER_POLL_ATTEMPTS = int(os.environ.get("SUPPLIER_POLL_ATTEMPTS", "6"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    sku TEXT NOT NULL,
    upstream_code TEXT,
    session_fp TEXT NOT NULL,
    session_tail TEXT NOT NULL DEFAULT '',
    supplier_ref TEXT NOT NULL DEFAULT '',
    supplier_secret TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_fp ON orders(session_fp, status, created_at);
CREATE INDEX IF NOT EXISTS ix_orders_open ON orders(status, created_at);
CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',
    order_no TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stock_sku ON stock(sku, status);
CREATE TABLE IF NOT EXISTS supplier_config (
    name TEXT PRIMARY KEY,
    token TEXT NOT NULL DEFAULT '',
    api_base TEXT NOT NULL DEFAULT '',
    sku_map TEXT NOT NULL DEFAULT '',
    catalog_race TEXT NOT NULL DEFAULT '',
    sku_options TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
"""


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as connection:
        connection.executescript(SCHEMA)


def sign(timestamp: str, body: str) -> str:
    return hmac.new(MERCHANT_SECRET.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()


def authenticate(request: Request, body: str) -> None:
    if not MERCHANT_KEY or not MERCHANT_SECRET:
        raise HTTPException(503, "upstream keys are not configured")
    timestamp = request.headers.get("x-timestamp", "")
    signature = request.headers.get("x-signature", "")
    if not timestamp.isdigit():
        raise HTTPException(401, "missing timestamp")
    if abs(time.time() - int(timestamp)) > REPLAY_WINDOW_SECONDS:
        raise HTTPException(401, "timestamp outside window")
    given_key = request.headers.get("x-merchant-key", "")
    if not hmac.compare_digest(given_key, MERCHANT_KEY):
        raise HTTPException(401, "unknown merchant key")
    if not hmac.compare_digest(signature, sign(timestamp, body)):
        raise HTTPException(401, "bad signature")


def fingerprint(session_raw: str) -> str:
    return hashlib.sha256(session_raw.encode("utf-8")).hexdigest()


class RechargeIn(BaseModel):
    session_raw: str = Field(min_length=1, max_length=262_144)
    sku_type: str = Field(min_length=1, max_length=40)
    upstream_code: str | None = Field(default=None, max_length=200)
    merchant: str = Field(default="MOLIPLY", max_length=64)
    timestamp: int = 0


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    logger.info("upstream ready: mode=%s db=%s", FULFILL_MODE, DB_PATH)
    yield


app = FastAPI(title="MOLIPLY upstream", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "mode": FULFILL_MODE,
        "keys": bool(MERCHANT_KEY and MERCHANT_SECRET),
        "supplier": SUPPLIER_NAME or None,
    }


# ---------------------------------------------------------------------------
# Fulfillment
# ---------------------------------------------------------------------------
@dataclass
class Claim:
    found: bool
    payload: str = ""


def build_supplier():
    """Construct the configured supplier, or None when running manual/stock.

    The database row wins over the environment so a key entered in the store
    admin takes effect without a redeploy; env stays the bootstrap default.
    """
    if SUPPLIER_NAME != "yunshu":
        return None
    row = supplier_config(SUPPLIER_NAME)
    token = (row or {}).get("token") or os.environ.get("YUNSHU_TOKEN", "")
    if not token:
        return None
    raw_map = (row or {}).get("sku_map") or os.environ.get("YUNSHU_SKU_MAP", "")
    mapping: dict[str, str] = {}
    for pair in raw_map.split(","):
        if "=" in pair:
            local, remote = pair.split("=", 1)
            if local.strip() and remote.strip():
                mapping[local.strip()] = remote.strip()
    return YunshuSupplier(
        (row or {}).get("api_base") or os.environ.get("YUNSHU_API_BASE", "https://yunshutoken.com/partner/v1"),
        token,
        mapping,
        catalog_race=(row or {}).get("catalog_race") or os.environ.get("YUNSHU_RACE", ""),
        sku_options=(row or {}).get("sku_options") or os.environ.get("YUNSHU_SKU", ""),
    )


def supplier_config(name: str) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            "SELECT token, api_base, sku_map, catalog_race, sku_options, enabled FROM supplier_config WHERE name=?",
            (name,),
        ).fetchone()
    return dict(row) if row else None


def save_supplier_config(name: str, values: dict[str, Any]) -> dict[str, Any]:
    """Upsert one supplier row. Empty token keeps the stored one."""
    current = supplier_config(name) or {}
    merged = {
        "token": values.get("token") or current.get("token", ""),
        "api_base": values.get("api_base", current.get("api_base", "")),
        "sku_map": values.get("sku_map", current.get("sku_map", "")),
        "catalog_race": values.get("catalog_race", current.get("catalog_race", "")),
        "sku_options": values.get("sku_options", current.get("sku_options", "")),
        "enabled": int(bool(values.get("enabled", current.get("enabled", 0)))),
    }
    with db() as connection:
        connection.execute(
            "INSERT INTO supplier_config(name, token, api_base, sku_map, catalog_race, sku_options, enabled, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET token=excluded.token, "
            "api_base=excluded.api_base, sku_map=excluded.sku_map, catalog_race=excluded.catalog_race, "
            "sku_options=excluded.sku_options, enabled=excluded.enabled, updated_at=excluded.updated_at",
            (name, merged["token"], merged["api_base"], merged["sku_map"], merged["catalog_race"],
             merged["sku_options"], merged["enabled"], time.time()),
        )
    return merged


def claim_stock(connection: sqlite3.Connection, sku: str, order_no: str) -> Claim:
    """Take one available code for the SKU and bind it to this order."""
    row = connection.execute(
        "SELECT id, payload FROM stock WHERE sku=? AND status='available' ORDER BY id ASC LIMIT 1", (sku,)
    ).fetchone()
    if row is None:
        return Claim(False)
    connection.execute(
        "UPDATE stock SET status='used', order_no=? WHERE id=? AND status='available'", (order_no, row["id"])
    )
    return Claim(True, row["payload"])


def settle(order_no: str, status: str, message: str = "") -> None:
    with db() as connection:
        connection.execute(
            "UPDATE orders SET status=?, message=?, updated_at=? WHERE order_no=? AND status NOT IN ('success','failed')",
            (status, message, time.time(), order_no),
        )


def run_fulfillment(order_no: str, payload: RechargeIn) -> None:
    if FULFILL_MODE == "stock":
        with db() as connection:
            claim = claim_stock(connection, payload.sku_type, order_no)
        if not claim.found:
            settle(order_no, "pending", "库存不足，等待人工补货")
            return
        settle(order_no, "success", f"已交付 {claim.payload}")
        return

    if FULFILL_MODE == "supplier":
        supplier = build_supplier()
        if supplier is None:
            settle(order_no, "pending", "上游未配置，转人工")
            return
        contact = payload.merchant or "MOLIPLY"
        try:
            result = supplier.deliver(payload.sku_type, payload.session_raw, order_no, contact)
        except SupplierRejected as exc:
            settle(order_no, "failed", str(exc))
            return
        except SupplierUncertain as exc:
            # Balance may be spent: park it, an operator decides.
            settle(order_no, "pending", f"上游结果不明：{exc}")
            return
        except SupplierUnavailable as exc:
            settle(order_no, "pending", f"上游暂不可用：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("supplier crashed for %s", order_no)
            settle(order_no, "pending", f"上游异常：{exc}")
            return
        persist_supplier(order_no, result)
        if result.status == "success":
            settle(order_no, "success", result.message)
        elif result.status == "failed":
            settle(order_no, "failed", result.message)
        else:
            settle(order_no, "pending", result.message)
        return

    settle(order_no, "pending", "等待人工处理")


def persist_supplier(order_no: str, result) -> None:
    if not result.reference:
        return
    with db() as connection:
        connection.execute(
            "UPDATE orders SET supplier_ref=? WHERE order_no=?", (result.reference, order_no)
        )


def repoll(order_no: str) -> None:
    """Ask the supplier again for a pending order that has a live reference."""
    supplier = build_supplier()
    if supplier is None:
        return
    with db() as connection:
        row = connection.execute(
            "SELECT sku, supplier_ref, supplier_secret FROM orders WHERE order_no=?", (order_no,)
        ).fetchone()
    if row is None or not row["supplier_ref"]:
        return
    try:
        result = supplier.poll(row["supplier_ref"], row["supplier_secret"], row["sku"], tries=1)
    except Exception:  # noqa: BLE001
        return
    if result.status == "success":
        settle(order_no, "success", result.message)
    elif result.status == "failed":
        settle(order_no, "failed", result.message)


# ---------------------------------------------------------------------------
# Provider contract
# ---------------------------------------------------------------------------
@app.post("/api/v1/recharge")
async def recharge(request: Request):
    body = (await request.body()).decode("utf-8")
    authenticate(request, body)
    try:
        payload = RechargeIn(**json.loads(body))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"invalid payload: {exc}") from exc

    session_fp = fingerprint(payload.session_raw)
    with _lock, db() as connection:
        existing = connection.execute(
            "SELECT order_no, status, message FROM orders WHERE session_fp=? AND status='pending' "
            "AND created_at > ? ORDER BY id DESC LIMIT 1",
            (session_fp, time.time() - IDEMPOTENCY_TTL_SECONDS),
        ).fetchone()
        if existing:
            logger.info("reusing pending order %s for the same session", existing["order_no"])
            return {"order_no": existing["order_no"], "status": existing["status"], "message": existing["message"]}

        if MAX_PENDING_PER_SKU:
            pending = connection.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE sku=? AND status='pending'", (payload.sku_type,)
            ).fetchone()["n"]
            if pending >= MAX_PENDING_PER_SKU:
                # Accept but queue: MOLIPLY polls, nothing is lost.
                logger.warning("sku %s has %s pending orders", payload.sku_type, pending)

        order_no = f"UP{int(time.time() * 1000)}{os.urandom(2).hex()}"
        now = time.time()
        connection.execute(
            "INSERT INTO orders(order_no, status, sku, upstream_code, session_fp, session_tail, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (order_no, "pending", payload.sku_type, payload.upstream_code, session_fp, payload.session_raw[-6:], now, now),
        )

    try:
        run_fulfillment(order_no, payload)
    except Exception as exc:  # noqa: BLE001
        logger.exception("fulfillment crashed for %s", order_no)
        settle(order_no, "pending", f"履约异常：{exc}")

    with db() as connection:
        row = connection.execute("SELECT status, message FROM orders WHERE order_no=?", (order_no,)).fetchone()
    return {"order_no": order_no, "status": row["status"], "message": row["message"]}


@app.get("/api/v1/recharge/{order_no}")
async def order_status(order_no: str, request: Request):
    authenticate(request, "")
    with db() as connection:
        row = connection.execute("SELECT status, message FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if row is None:
        raise HTTPException(404, "unknown order")
    if row["status"] == "pending" and FULFILL_MODE == "supplier":
        repoll(order_no)
        with db() as connection:
            row = connection.execute("SELECT status, message FROM orders WHERE order_no=?", (order_no,)).fetchone()
    return {"order_no": order_no, "status": row["status"], "message": row["message"]}


# ---------------------------------------------------------------------------
# Channel management, called by the store backend over the same HMAC contract
# as /api/v1 so no second credential is needed.
# ---------------------------------------------------------------------------
class SupplierIn(BaseModel):
    token: str | None = Field(default=None, max_length=200)
    api_base: str | None = Field(default=None, max_length=300)
    sku_map: str | None = Field(default=None, max_length=4000)
    catalog_race: str | None = Field(default=None, max_length=200)
    sku_options: str | None = Field(default=None, max_length=400)
    enabled: bool | None = None


@app.post("/api/v1/supplier")
async def set_supplier(request: Request):
    body = (await request.body()).decode("utf-8")
    authenticate(request, body)
    if SUPPLIER_NAME != "yunshu":
        raise HTTPException(400, f"unsupported supplier: {SUPPLIER_NAME or 'none'}")
    try:
        values = SupplierIn(**json.loads(body or "{}")).model_dump(exclude_none=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"invalid payload: {exc}") from exc
    saved = save_supplier_config(SUPPLIER_NAME, values)
    return {"supplier": SUPPLIER_NAME, "configured": bool(saved["token"]), "enabled": bool(saved["enabled"])}


@app.get("/api/v1/supplier")
def get_supplier(request: Request):
    authenticate(request, "")
    row = supplier_config(SUPPLIER_NAME) or {}
    env_token = os.environ.get("YUNSHU_TOKEN", "")
    return {
        "supplier": SUPPLIER_NAME,
        "configured": bool(row.get("token") or env_token),
        "source": "database" if row.get("token") else ("environment" if env_token else "none"),
        # The secret is only ever returned masked.
        "token_hint": (row.get("token") or env_token)[:8],
        "api_base": row.get("api_base") or os.environ.get("YUNSHU_API_BASE", ""),
        "sku_map": row.get("sku_map") or os.environ.get("YUNSHU_SKU_MAP", ""),
        "catalog_race": row.get("catalog_race") or os.environ.get("YUNSHU_RACE", ""),
        "sku_options": row.get("sku_options") or os.environ.get("YUNSHU_SKU", ""),
        "enabled": bool(row.get("enabled")),
        "fulfill_mode": FULFILL_MODE,
    }


@app.post("/api/v1/supplier/probe")
async def probe_supplier(request: Request):
    """Check the key and pull a live quote, without spending anything."""
    body = (await request.body()).decode("utf-8")
    authenticate(request, body)
    supplier = build_supplier()
    if supplier is None:
        raise HTTPException(400, "上游未配置密钥")
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{supplier.base}/connect",
                # The partner API is form-encoded and answers 415 without this.
                headers={
                    "Authorization": f"Bearer {supplier.token}",
                    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                    "Accept": "application/json",
                },
                content=b"",
            )
        if response.status_code >= 400:
            raise HTTPException(400, f"密钥校验失败：HTTP {response.status_code} {response.text[:120]}")
        payload = response.json()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"无法连接上游：{exc}") from exc
    if str(payload.get("code")) != "200":
        raise HTTPException(400, str(payload.get("msg") or "上游拒绝该密钥"))
    data = payload.get("data") or {}

    quotes: list[dict[str, Any]] = []
    for local, remote in supplier.sku_map.items():
        try:
            quotes.append({"sku": local, "code": remote, "price_cents": supplier.quote(local, "MOLIPLY")})
        except Exception as exc:  # noqa: BLE001
            quotes.append({"sku": local, "code": remote, "error": str(exc)[:160]})
    return {
        "shop": data.get("shopName"),
        "balance": data.get("balance"),
        "quotes": quotes,
        "mapped": len(supplier.sku_map),
    }


# ---------------------------------------------------------------------------
# Operator console (token auth, no session data beyond a 6-char tail)
# ---------------------------------------------------------------------------
# Failed tokens per client IP, in a sliding window. Only the admin surface is
# limited; the HMAC merchant contract has its own signature check.
ADMIN_WINDOW_SECONDS = int(os.environ.get("ADMIN_FAILURE_WINDOW_SECONDS", "900"))
ADMIN_MAX_FAILURES = int(os.environ.get("ADMIN_MAX_FAILURES", "5"))
_admin_failures: dict[str, list[float]] = {}
_admin_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune_admin_failures(key: str, now: float) -> list[float]:
    stamps = [at for at in _admin_failures.get(key, []) if now - at < ADMIN_WINDOW_SECONDS]
    if stamps:
        _admin_failures[key] = stamps
    else:
        _admin_failures.pop(key, None)
    return stamps


def require_admin(request: Request) -> None:
    """Header-only admin auth with an IP lockout.

    ``?token=`` is accepted for one release only, because it leaks the token
    into access logs and Referer headers.
    """
    if not ADMIN_TOKEN:
        raise HTTPException(503, "admin token is not configured")

    key = _client_ip(request)
    now = time.time()
    with _admin_lock:
        stamps = _prune_admin_failures(key, now)
        if len(stamps) >= ADMIN_MAX_FAILURES:
            raise HTTPException(
                429,
                f"too many failed admin attempts; retry in {ADMIN_WINDOW_SECONDS // 60} minutes",
            )

    token = request.headers.get("x-admin-token", "")
    if not token or not hmac.compare_digest(token, ADMIN_TOKEN):
        with _admin_lock:
            _admin_failures.setdefault(key, []).append(now)
        raise HTTPException(401, "bad admin token")


def reset_admin_failures() -> None:
    """Test hook: forget every lockout counter."""
    with _admin_lock:
        _admin_failures.clear()


@app.get("/admin/api/orders")
def admin_orders(request: Request, status: str = "pending", limit: int = 50):
    require_admin(request)
    limit = max(1, min(limit, 200))
    with db() as connection:
        rows = connection.execute(
            "SELECT order_no, status, message, sku, upstream_code, session_tail, created_at FROM orders "
            "WHERE (?='all' OR status=?) ORDER BY id DESC LIMIT ?",
            (status, status, limit),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/admin/api/orders/{order_no}/settle")
def admin_settle(order_no: str, request: Request, status: str = "success", message: str = ""):
    require_admin(request)
    if status not in TERMINAL:
        raise HTTPException(400, "status must be success or failed")
    with db() as connection:
        row = connection.execute("SELECT status FROM orders WHERE order_no=?", (order_no,)).fetchone()
    if row is None:
        raise HTTPException(404, "unknown order")
    if row["status"] in TERMINAL:
        return {"order_no": order_no, "status": row["status"], "note": "already terminal"}
    settle(order_no, status, message)
    return {"order_no": order_no, "status": status}


@app.get("/admin/api/stock")
def admin_stock(request: Request):
    require_admin(request)
    with db() as connection:
        rows = connection.execute(
            "SELECT sku, SUM(status='available') AS available, SUM(status='used') AS used "
            "FROM stock GROUP BY sku ORDER BY sku"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/admin/api/stock")
async def admin_add_stock(request: Request, sku: str):
    """One code per line in the body."""
    require_admin(request)
    text = (await request.body()).decode("utf-8")
    codes = [line.strip() for line in text.splitlines() if line.strip()]
    if not codes:
        raise HTTPException(400, "empty body")
    now = time.time()
    with db() as connection:
        connection.executemany(
            "INSERT INTO stock(sku, payload, created_at) VALUES(?,?,?)",
            [(sku, code, now) for code in codes],
        )
    return {"added": len(codes), "sku": sku}


@app.get("/admin", response_class=PlainTextResponse)
def admin_page() -> str:
    return ADMIN_HTML


ADMIN_HTML = """<!doctype html><meta charset=utf-8><title>MOLIPLY upstream</title>
<style>body{font:13px/1.6 ui-monospace,monospace;background:#0b1220;color:#cdd6e4;margin:0;padding:24px}
h1{font-size:16px;margin:0 0 4px}input,button{font:inherit;background:#131d31;color:#e6edf7;border:1px solid #24354f;border-radius:6px;padding:6px 9px}
button{cursor:pointer}table{width:100%;border-collapse:collapse;margin-top:10px}td,th{border-bottom:1px solid #1b2a41;padding:6px;text-align:left}
.muted{color:#6b7d95}.ok{color:#4ade80}.bad{color:#f87171}section{margin-bottom:22px}</style>
<h1>MOLIPLY 上游服务</h1><p class=muted>人工兜底控制台。Token 只存在浏览器本地，仅经 X-Admin-Token 头发送（不再用 ?token=）。</p>
<section>Token <input id=t placeholder="admin token" size=34><button onclick=save()>保存</button> <span id=msg class=muted></span></section>
<section><b>待处理订单</b> <button onclick=load()>刷新</button><table id=orders></table></section>
<section><b>库存</b><table id=stock></table>
<p><input id=sku placeholder=SKU size=22><textarea id=codes placeholder=一行一个 rows=3 style="width:100%"></textarea>
<button onclick=add()>导入库存</button></section>
<script>
const tk=()=>localStorage.getItem('tk')||'';
const save=()=>{localStorage.setItem('tk',document.getElementById('t').value);load()};
const msg=m=>document.getElementById('msg').textContent=m;
async function api(p,o={}){const r=await fetch(p,{...o,headers:{'x-admin-token':tk(),...(o.headers||{})}});
 if(!r.ok)throw new Error(r.status+' '+await r.text());return r.json()}
async function load(){document.getElementById('t').value=tk();try{
 const o=await api('/admin/api/orders?status=pending');
 document.getElementById('orders').innerHTML='<tr><th>订单</th><th>SKU</th><th>Session</th><th>操作</th></tr>'+
  o.items.map(i=>`<tr><td>${i.order_no}</td><td>${i.sku}</td><td class=muted>${i.session_tail}</td>
  <td><button onclick="settle('${i.order_no}','success')">成功</button> <button onclick="settle('${i.order_no}','failed')">失败</button></td></tr>`).join('');
 const s=await api('/admin/api/stock');
 document.getElementById('stock').innerHTML='<tr><th>SKU</th><th>可用</th><th>已用</th></tr>'+
  s.items.map(i=>`<tr><td>${i.sku}</td><td>${i.available}</td><td class=muted>${i.used}</td></tr>`).join('');
}catch(e){msg(e.message)}}
async function settle(n,st){try{await api('/admin/api/orders/'+n+'/settle?status='+st,{method:'POST'});msg(n+' -> '+st);load()}catch(e){msg(e.message)}}
async function add(){const sku=document.getElementById('sku').value.trim(),codes=document.getElementById('codes').value;
 try{const r=await api('/admin/api/stock?sku='+encodeURIComponent(sku),{method:'POST',body:codes});msg('导入 '+r.added);load()}catch(e){msg(e.message)}}
load()
</script>"""


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"message": exc.detail}, status_code=exc.status_code)


@app.get("/", response_class=Response)
def root() -> Response:
    return Response(status_code=204)
