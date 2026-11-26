"""Automated commercial delivery bridge between MOLIPLY storefront and CCE console.

When a storefront product is configured with ``FulfillmentStrategy.CCE_ACCOUNT_KEY``,
paying an order automatically claims a ready CCE Claude account profile (``IDLE``,
with sealed credentials and no active customer delivery key), mints a time-limited
``card_key``, refreshes the tunnel gateway authorization snapshot, and returns the
customer-facing handover payload for ``OrderDelivery``.

If CCE is not installed or no ready profile is available, ``claim_and_mint_key``
returns ``None`` so the order cleanly enters ``stock_pending`` and will be
auto-fulfilled by the backorder sweeper as soon as CCE finishes provisioning.

Two transports reach CCE, tried in this order:

* **HTTP** (``CCE_API_BASE`` + ``CCE_ADMIN_TOKEN``) — the console's own admin
  API. Preferred: it runs the same code path an operator does, so the gateway
  snapshot is refreshed by CCE itself and no file permissions are shared.
* **direct SQLite** (``CCE_DB_PATH``) — kept for a co-located CCE whose database
  this process can already open. Requires writing the gateway snapshot here.

Claiming over HTTP is not atomic the way a single ``BEGIN IMMEDIATE`` is, so a
``CCE_API_BASE`` deployment relies on the profile's own delivery-key check plus
a short Redis lock to keep two workers off the same account.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import secrets
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# How long an HTTP claim may hold the pick lock; a mint is two quick calls.
_CLAIM_LOCK_SECONDS = 20

CCE_OS_BINARIES: dict[str, str] = {
    "windows": "claude-box-windows-x64.exe",
    "linux": "claude-box",
    "darwin-arm64": "claude-box-darwin-arm64",
    "darwin-x64": "claude-box-darwin-x64",
}


class CceBundleError(RuntimeError):
    """Structured error raised when assembling a customer one-click CCE bundle."""

    def __init__(self, message: str, *, status_code: int = 409, code: str = "CCE_BUNDLE_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _cce_api_base() -> str:
    return (os.environ.get("CCE_API_BASE") or "").rstrip("/")


def _cce_admin_token() -> str:
    return os.environ.get("CCE_ADMIN_TOKEN", "")


def _http_ready() -> bool:
    return bool(_cce_api_base() and _cce_admin_token())


def _http_client(timeout: float = 15.0) -> httpx.Client:
    return httpx.Client(
        base_url=_cce_api_base(),
        headers={"x-admin-token": _cce_admin_token()},
        timeout=timeout,
    )


def _http_base_ready(profile: dict[str, Any]) -> bool:
    return (
        str(profile.get("status") or "") == "IDLE"
        and bool(profile.get("has_credentials"))
    )


def _http_mature_free(profile: dict[str, Any]) -> bool:
    """Return True if the profile is a mature free account in the warm-up pool ready for JIT Pro upgrade."""
    if str(profile.get("status") or "") != "IDLE":
        return False
    if str(profile.get("job_stage") or "") != "registered":
        return False
    w_days = max(1, int(profile.get("warmup_days") or 3))
    w_done = int(profile.get("warmup_completed") or 0)
    return w_done >= w_days


def _keys_have_active(keys: list[dict[str, Any]], now_iso: str) -> bool:
    for record in keys:
        if not isinstance(record, dict):
            continue
        if int(record.get("is_active", 1) or 0) != 1:
            continue
        if str(record.get("expires_at") or "") > now_iso:
            return True
    return False


def _http_profile_has_active_key(client: httpx.Client, profile: dict[str, Any], now_iso: str) -> bool:
    """Return True if the profile already has an active unexpired delivery key.

    Newer CCE builds include ``active_key`` directly on ``/admin/profiles`` rows.
    Older CCE builds omit the field; in that case we verify via
    ``GET /admin/keys/{profile_id}`` and fail closed on any error so an account
    can never be double-sold.
    """
    if "active_key" in profile:
        return bool(profile.get("active_key"))
    profile_id = str(profile.get("profile_id") or "").strip()
    if not profile_id:
        return True
    try:
        response = client.get(f"/admin/keys/{profile_id}")
        response.raise_for_status()
        keys = response.json().get("keys")
        if not isinstance(keys, list):
            return True
        return _keys_have_active(keys, now_iso)
    except Exception:  # noqa: BLE001 - fail closed to prevent double-selling
        logger.warning("CCE HTTP active-key check failed for %s; failing closed", profile_id, exc_info=False)
        return True


def _http_deliverable(profile: dict[str, Any]) -> bool:
    """Same conditions the SQLite query enforces, in Python.

    A profile is sellable when it is resting, holds sealed credentials, and has
    no live delivery key. ``job_stage`` is ignored deliberately: an operator can
    finish a stuck account by hand and the delivery is still valid.
    """
    return _http_base_ready(profile) and not profile.get("active_key")


def _http_available() -> int:
    try:
        now_iso = _now_iso()
        with _http_client() as client:
            response = client.get("/admin/profiles")
            response.raise_for_status()
            profiles = response.json().get("profiles") or []
            count = 0
            for profile in profiles:
                if _http_base_ready(profile) and not _http_profile_has_active_key(client, profile, now_iso):
                    count += 1
                elif _http_mature_free(profile):
                    # Mature free accounts in the warm-up pool are sellable via JIT promotion on payment.
                    count += 1
            return count
    except Exception:  # noqa: BLE001 - a bridge probe must never raise
        logger.warning("CCE HTTP probe failed", exc_info=False)
        return 0


def _http_claim(plan_tier: str, valid_days: int) -> dict | None:
    """Pick a deliverable profile and mint its key through the console API.

    If no pre-upgraded Pro account is on the hot-standby shelf, but mature free
    accounts exist in the warm-up pool, automatically triggers ``POST /admin/jobs/promote``
    so CCE upgrades one mature account to a full 30-day Pro subscription right now;
    the every-60s backorder sweeper then delivers it as soon as the upgrade finishes.
    """
    from app.services.locks import redis_lock

    with redis_lock("lock:cce:claim", ttl_seconds=_CLAIM_LOCK_SECONDS) as acquired:
        if not acquired:
            return None
        try:
            now_iso = _now_iso()
            with _http_client() as client:
                listed = client.get("/admin/profiles")
                listed.raise_for_status()
                all_profiles = listed.json().get("profiles") or []
                ready = [p for p in all_profiles if _http_base_ready(p)]
                # Oldest account first, matching the SQLite ordering.
                ready.sort(key=lambda p: str(p.get("created_at") or ""))
                chosen: dict[str, Any] | None = None
                for candidate in ready:
                    if not _http_profile_has_active_key(client, candidate, now_iso):
                        chosen = candidate
                        break
                if chosen is None:
                    if any(_http_mature_free(p) for p in all_profiles):
                        try:
                            client.post("/admin/jobs/promote", json={"count": 1, "wait": False})
                            logger.info("Triggered JIT Pro promotion for a mature CCE warm-up pool account")
                        except Exception:  # noqa: BLE001
                            logger.warning("Failed to trigger CCE JIT promotion", exc_info=False)
                    return None
                profile_id = str(chosen["profile_id"])

                minted = client.post(
                    "/admin/keys",
                    json={"profile_id": profile_id, "plan_tier": plan_tier, "valid_days": valid_days},
                )
                minted.raise_for_status()
                record = minted.json()
        except Exception:  # noqa: BLE001
            logger.exception("CCE HTTP claim failed")
            return None

    card_key = str(record["card_key"])
    tunnel_secret = str(record["tunnel_secret"])
    expires_iso = str(record["expires_at"])
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
    if _http_ready():
        return _http_available()
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
              AND length(p.claude_credentials_enc) > 1
              AND NOT EXISTS (
                  SELECT 1 FROM delivery_keys k
                  WHERE k.profile_id = p.profile_id
                      AND k.is_active = 1
                      AND k.expires_at > ?
              )
            """,
            (now_iso,),
        ).fetchone()
        ready_cnt = int(row["cnt"] if row else 0)
        mature_cnt = 0
        try:
            mrow = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM account_jobs j
                JOIN profiles p ON p.profile_id = j.profile_id
                WHERE j.stage = 'registered'
                  AND p.status = 'IDLE'
                  AND COALESCE(j.warmup_completed, 0) >= CASE WHEN COALESCE(j.warmup_days, 3) < 1 THEN 1 ELSE COALESCE(j.warmup_days, 3) END
                """
            ).fetchone()
            mature_cnt = int(mrow["cnt"] if mrow else 0)
        except sqlite3.Error:
            mature_cnt = 0
        return ready_cnt + mature_cnt
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
    """Claim one ready CCE profile and mint a delivery key for a paid order."""
    if _http_ready():
        return _http_claim(plan_tier, valid_days)
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
              AND length(p.claude_credentials_enc) > 1
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


def parse_delivery_payload(payload_text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines from an OrderDelivery payload of kind CCE_DELIVERY_KEY."""
    parsed: dict[str, str] = {}
    for raw_line in (payload_text or "").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        if key:
            parsed[key] = value.strip()
    return parsed


def _cce_client_dir() -> Path:
    return Path(os.environ.get("CCE_CLIENT_DIR") or "data/clients")


def _asset_http_client() -> httpx.Client:
    return httpx.Client(base_url=_cce_api_base(), timeout=60.0)


def _fetch_client_asset(name: str, *, card_key: str, tunnel_secret: str) -> bytes:
    """Fetch a launcher binary or gateway CA without mounting host directories.

    When ``CCE_API_BASE`` is configured, downloads over HTTP from the host CCE
    daemon: tries customer-authenticated ``/launcher/client/{name}`` first and
    falls back to admin-authenticated ``/admin/client/{name}`` when the daemon
    only exposes the admin endpoint. When HTTP is not configured, falls back to
    ``CCE_CLIENT_DIR`` for local/co-located deployments.
    """
    safe_name = Path(name).name
    if _cce_api_base():
        try:
            with _asset_http_client() as client:
                resp = client.get(
                    f"/launcher/client/{safe_name}",
                    headers={
                        "authorization": f"Bearer {card_key}",
                        "x-tunnel-secret": tunnel_secret,
                    },
                )
                if resp.status_code == 200:
                    return resp.content
                if resp.status_code in {401, 403}:
                    raise CceBundleError(
                        "该交付密钥已过期或已撤销，无法生成客户端整合包",
                        status_code=403,
                        code="KEY_REVOKED_OR_EXPIRED",
                    )
                if resp.status_code == 404 and _cce_admin_token():
                    admin_resp = client.get(
                        f"/admin/client/{safe_name}",
                        headers={"x-admin-token": _cce_admin_token()},
                    )
                    if admin_resp.status_code == 200:
                        return admin_resp.content
                    if admin_resp.status_code == 404:
                        raise CceBundleError(
                            f"服务端尚未发布客户端文件 {safe_name}，请联系客服或管理员上传",
                            status_code=409,
                            code="CLIENT_ASSET_MISSING",
                        )
                    raise CceBundleError(
                        f"获取客户端文件失败（CCE 返回 {admin_resp.status_code}）",
                        status_code=502,
                        code="CCE_ASSET_UPSTREAM_ERROR",
                    )
                if resp.status_code == 404:
                    raise CceBundleError(
                        f"服务端尚未发布客户端文件 {safe_name}，请联系客服或管理员上传",
                        status_code=409,
                        code="CLIENT_ASSET_MISSING",
                    )
                raise CceBundleError(
                    f"获取客户端文件失败（CCE 返回 {resp.status_code}）",
                    status_code=502,
                    code="CCE_ASSET_UPSTREAM_ERROR",
                )
        except CceBundleError:
            raise
        except httpx.HTTPError as exc:
            logger.warning("CCE HTTP asset fetch failed for %s: %s", safe_name, exc)
            raise CceBundleError(
                "连接 CCE 托管台超时或失败，请稍后重试",
                status_code=502,
                code="CCE_UNREACHABLE",
            ) from exc

    local_path = _cce_client_dir() / safe_name
    if not local_path.is_file():
        raise CceBundleError(
            f"服务端尚未发布客户端文件 {safe_name}，请联系客服或管理员上传",
            status_code=409,
            code="CLIENT_ASSET_MISSING",
        )
    return local_path.read_bytes()


def _resolve_gateway_addr(parsed: dict[str, str], host_url: str) -> str:
    configured = (os.environ.get("CCE_GATEWAY_ADDR") or "").strip()
    if configured:
        return configured
    if parsed.get("GATEWAY"):
        return parsed["GATEWAY"]
    hostname = urlparse(host_url).hostname or "127.0.0.1"
    port = (os.environ.get("CCE_TUNNEL_PORT") or "8444").strip() or "8444"
    return f"{hostname}:{port}"


def build_customer_bundle(payload_text: str, *, os_name: str = "windows") -> tuple[str, bytes]:
    """Assemble a ready-to-run customer zip bundle reusing the delivered key.

    Never mints a new delivery key and never touches host filesystem paths when
    ``CCE_API_BASE`` is configured.
    """
    os_key = (os_name or "windows").strip().lower()
    binary_name = CCE_OS_BINARIES.get(os_key)
    if not binary_name:
        raise CceBundleError(
            f"不支持的系统类型：{os_name}（可选：windows / darwin-arm64 / darwin-x64 / linux）",
            status_code=400,
            code="UNSUPPORTED_OS",
        )

    parsed = parse_delivery_payload(payload_text)
    card_key = parsed.get("CCE_KEY", "")
    tunnel_secret = parsed.get("TUNNEL_SECRET", "")
    if not card_key or not tunnel_secret:
        raise CceBundleError("交付凭证不完整，缺少密钥或隧道口令", status_code=422, code="INVALID_PAYLOAD")

    profile_id = parsed.get("PROFILE") or "claude"
    expires_at = parsed.get("EXPIRES_AT") or ""
    default_host = f"{settings.public_store_url.rstrip('/')}/cce"
    host_url = (parsed.get("HOST") or default_host).rstrip("/")
    gateway = _resolve_gateway_addr(parsed, host_url)

    binary_bytes = _fetch_client_asset(binary_name, card_key=card_key, tunnel_secret=tunnel_secret)
    ca_bytes = _fetch_client_asset("tls.crt", card_key=card_key, tunnel_secret=tunnel_secret)

    manifest = {
        "key": card_key,
        "secret": tunnel_secret,
        "host": host_url,
        "gateway": gateway,
        "ca": "gateway-ca.pem",
    }
    if os_key == "windows":
        launch_hint = f"双击 {binary_name} 就能用，不用输任何命令。\r\n"
    else:
        launch_hint = f"在终端进入本文件夹并执行：chmod +x ./{binary_name} && ./{binary_name}\n"

    expiry_line = (
        f"有效期到 {expires_at[:10]}，过期请联系卖家续期。\r\n"
        if expires_at
        else "请妥善保管本文件夹，切勿泄露给他人。\r\n"
    )
    readme = (
        f"{launch_hint}"
        "\r\n"
        f"{expiry_line}"
        "\r\n"
        "如果提示找不到 claude：\r\n"
        "  1. 先装 Claude Code 官方客户端；或\r\n"
        "  2. 把 claude 可执行文件复制到本文件夹，再运行一次。\r\n"
        "\r\n"
        "跑完窗口会停住，按回车关闭。\r\n"
    )

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cce.json", json.dumps(manifest, indent=2))
        archive.writestr("gateway-ca.pem", ca_bytes)
        info = zipfile.ZipInfo(binary_name)
        info.compress_type = zipfile.ZIP_DEFLATED
        if os_key != "windows":
            info.external_attr = 0o100755 << 16
        archive.writestr(info, binary_bytes)
        archive.writestr("请先看我.txt", readme.encode("utf-8"))

    safe_profile = re.sub(r"[^A-Za-z0-9._-]", "-", profile_id).strip("-") or "claude"
    filename = f"moliply-claude-{safe_profile}-{os_key}.zip"
    return filename, out.getvalue()


def pool_overview() -> dict[str, Any]:
    """Return a unified CCE pool status snapshot for the MOLIPLY admin dashboard."""
    if _http_ready():
        try:
            with _http_client(timeout=8.0) as client:
                resp = client.get("/admin/status")
                if resp.status_code == 200:
                    data = resp.json()
                    pool = data.get("pool") or {}
                    return {
                        "reachable": True,
                        "transport": "http",
                        "ready": bool(data.get("ready", True)),
                        "problems": data.get("problems") or [],
                        "ready_pro": int(pool.get("ready_pro", 0)),
                        "mature_free": int(pool.get("mature_free", 0)),
                        "warming_free": int(pool.get("warming_free", 0)),
                        "hot_standby_target": int(pool.get("hot_standby_target", 0)),
                        "cards_available": int((data.get("cards") or {}).get("available", 0)),
                        "vcc_issuer": data.get("vcc_issuer") or {},
                        "clients": data.get("clients") or [],
                        "worker": data.get("worker") or {},
                    }
        except Exception:  # noqa: BLE001
            pass

    conn = _connect()
    if conn is None:
        return {
            "reachable": False,
            "transport": "http" if _http_ready() else "sqlite",
            "ready": False,
            "problems": ["CCE 服务未连接或数据库未初始化"],
            "ready_pro": 0,
            "mature_free": 0,
            "warming_free": 0,
            "hot_standby_target": 0,
            "cards_available": 0,
            "vcc_issuer": {},
            "clients": [],
            "worker": {},
        }
    try:
        now_iso = _now_iso()
        ready_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM profiles p
            WHERE p.status = 'IDLE'
              AND p.claude_credentials_enc IS NOT NULL
              AND length(p.claude_credentials_enc) > 1
              AND NOT EXISTS (
                  SELECT 1 FROM delivery_keys k
                  WHERE k.profile_id = p.profile_id AND k.is_active = 1 AND k.expires_at > ?
              )
            """,
            (now_iso,),
        ).fetchone()
        ready_pro = int(ready_row["cnt"] if ready_row else 0)
        mature_free = 0
        warming_free = 0
        cards_avail = 0
        try:
            m_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM account_jobs j
                JOIN profiles p ON p.profile_id = j.profile_id
                WHERE j.stage = 'registered' AND p.status = 'IDLE'
                  AND COALESCE(j.warmup_completed, 0) >= CASE WHEN COALESCE(j.warmup_days, 3) < 1 THEN 1 ELSE COALESCE(j.warmup_days, 3) END
                """
            ).fetchone()
            mature_free = int(m_row["cnt"] if m_row else 0)
            w_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM account_jobs j
                WHERE j.stage = 'registered'
                  AND COALESCE(j.warmup_completed, 0) < CASE WHEN COALESCE(j.warmup_days, 3) < 1 THEN 1 ELSE COALESCE(j.warmup_days, 3) END
                """
            ).fetchone()
            warming_free = int(w_row["cnt"] if w_row else 0)
            c_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM subscription_cards WHERE status = 'available'"
            ).fetchone()
            cards_avail = int(c_row["cnt"] if c_row else 0)
        except sqlite3.Error:
            pass
        return {
            "reachable": True,
            "transport": "sqlite",
            "ready": True,
            "problems": [],
            "ready_pro": ready_pro,
            "mature_free": mature_free,
            "warming_free": warming_free,
            "hot_standby_target": int(os.environ.get("CCE_HOT_STANDBY_TARGET") or "0"),
            "cards_available": cards_avail,
            "vcc_issuer": {},
            "clients": [],
            "worker": {},
        }
    finally:
        conn.close()
