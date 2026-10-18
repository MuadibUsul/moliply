from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Every value can be supplied through environment variables or the root
    ``.env`` file. Keep secrets out of source control.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_env: str = "development"
    debug: bool = True
    app_name: str = "MOLIPLY Auto-Recharge Gateway"
    api_base_url: str = "http://localhost:8000"
    public_store_url: str = "http://localhost:3000"
    public_redeem_url: str = "http://localhost:3000/redeem"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://up.moliply.test:3000"

    # ------------------------------------------------------------------
    # Brand tokens (spec section 1)
    # ------------------------------------------------------------------
    brand_name_en: str = "MOLIPLY"
    brand_name_cn: str = "模力补给"
    brand_subtitle: str = "AI 会员 · 软件订阅 · 数字商品"
    brand_hero_slogan: str = "让优质数字服务，触手可及。"
    brand_code_prefix: str = "MOLI"
    brand_theme_bg: str = "#0B0F19"
    brand_theme_primary: str = "#2563EB"
    brand_theme_accent: str = "#06B6D4"
    support_wechat: str = "MOLIPLY-SUPPORT"
    support_hours: str = "每日 09:00–24:00"

    # Feishu custom-bot management notifications
    feishu_enabled: bool = False
    feishu_webhook_url: str = ""
    feishu_secret: str = ""
    feishu_low_stock_threshold: int = 5
    feishu_mention_all: bool = False

    # ------------------------------------------------------------------
    # Persistence / queue
    # ------------------------------------------------------------------
    database_url: str = "sqlite:///./moliply.db"
    redis_url: str = "redis://localhost:6379/0"
    task_mode: str = "inline"  # inline | celery | manual (tests)
    seed_demo_data: bool = True
    seed_demo_inventory: bool = True
    redeem_lock_ttl_seconds: int = 300
    token_retention_minutes: int = 5

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    secret_key: str = "dev-secret-key-change-me"
    master_aes_key: str = ""  # base64 encoded 32-byte key; required in production
    admin_username: str = "admin"
    admin_password: str = "change-me"
    admin_jwt_secret: str = "dev-admin-jwt-secret-change-me"
    admin_token_ttl_minutes: int = 720
    # Google Authenticator / RFC 6238 base32 secret. Empty disables TOTP in dev.
    admin_totp_secret: str = ""
    admin_failed_login_limit: int = 5
    admin_login_lockout_minutes: int = 15
    # Customer order accounts. Falls back to SECRET_KEY when unset in dev.
    user_jwt_secret: str = ""
    user_token_ttl_days: int = 30
    login_rate_limit: str = "10/minute"
    max_session_bytes: int = 262_144
    redeem_rate_limit: str = "10/minute"
    admin_login_rate_limit: str = "10/minute"
    lookup_rate_limit: str = "30/minute"
    failed_code_limit: int = 5
    failed_code_window_minutes: int = 30

    # ------------------------------------------------------------------
    # Payments (Lanttu / Xunhupay personal merchant gateways)
    # ------------------------------------------------------------------
    payment_mode: str = "mock"  # mock | live
    mock_payment_enabled: bool = True
    # Hard safety gate: production never accepts mock settlement unless this
    # environment-only switch is explicitly enabled for a controlled test.
    allow_mock_payment_in_production: bool = False
    order_expire_minutes: int = 15
    enforce_stock: bool = True
    ltzf_mch_id: str = ""
    ltzf_app_id: str = ""
    ltzf_key: str = ""
    ltzf_api_base: str = "https://api.ltzf.cn"
    ltzf_notify_url: str = ""
    xunhupay_app_id: str = ""
    xunhupay_app_secret: str = ""
    xunhupay_api_base: str = "https://api.xunhupay.com"
    xunhupay_notify_url: str = ""
    xunhupay_return_url: str = ""

    # ------------------------------------------------------------------
    # Crypto checkout (NOWPayments: USDT / USDC / BTC / ETH)
    # ------------------------------------------------------------------
    crypto_enabled: bool = False
    crypto_provider: str = "nowpayments"  # nowpayments | mock
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""
    nowpayments_api_base: str = "https://api.nowpayments.io"
    nowpayments_ipn_url: str = ""
    crypto_cny_per_usd: float = 7.20
    crypto_supported_coins: str = "USDT,USDC,BTC,ETH"

    @property
    def crypto_coin_list(self) -> list[str]:
        return [item.strip().upper() for item in self.crypto_supported_coins.split(",") if item.strip()]

    @property
    def nowpayments_ipn(self) -> str:
        return self.nowpayments_ipn_url or f"{self.api_base_url}/api/v1/payments/nowpayments/ipn"

    # ------------------------------------------------------------------
    # Upstream recharge channel
    # ------------------------------------------------------------------
    upstream_mode: str = "mock"  # mock | http
    upstream_api_base: str = ""
    upstream_api_key: str = ""
    upstream_api_secret: str = ""
    # Optional comma-separated host allowlist; empty means any HTTPS host.
    upstream_allowed_hosts: str = ""
    upstream_mock_delay_seconds: float = 2.0
    upstream_mock_fail_rate: float = 0.0
    upstream_max_wait_seconds: int = 180
    upstream_poll_interval_seconds: float = 5.0

    # ------------------------------------------------------------------
    # OpenAI account pre-check (optional, requires a clean US proxy)
    # ------------------------------------------------------------------
    openai_account_check_enabled: bool = False
    openai_account_check_fail_open: bool = True
    openai_api_base: str = "https://chatgpt.com"
    openai_proxy_url: str = ""

    # ------------------------------------------------------------------
    # Self-owned VCC channel (RDVCC) + residential proxy
    # ------------------------------------------------------------------
    self_vcc_enabled: bool = False
    self_vcc_dry_run: bool = True
    self_vcc_headless: bool = True
    self_vcc_min_balance: float = 22.0
    self_vcc_timeout_seconds: int = 150
    # V2 hybrid routing: PH first, US fallback, upstream last.
    routing_policy: str = "HYBRID_SELF_FIRST"
    self_vcc_ph_min_balance: float = 18.50
    self_vcc_us_min_balance: float = 22.00
    self_vcc_cancel_autorenew: bool = True
    ph_plus_amount_php: float = 999.00
    ph_pro_20x_amount_php: float = 9990.00
    us_plus_amount_usd: float = 20.00
    us_pro_20x_amount_usd: float = 200.00
    ph_plus_cost_usd: float = 16.70
    us_plus_cost_usd: float = 20.90
    proxy_url: str = ""
    ph_residential_proxy_url: str = ""
    us_residential_proxy_url: str = ""
    proxy_health_url: str = "https://ipinfo.io/json"
    proxy_required_country: str = "US"
    # Optional comma-separated BIN allowlist (first 6 digits) for US cards.
    us_bin_allowlist: str = ""
    rdvcc_checkout_url_template: str = ""
    # Hosts allowed to receive card data in the self-owned checkout flow.
    rdvcc_checkout_allowed_hosts: str = "chatgpt.com,*.openai.com,stripe.com,*.stripe.com"

    # ------------------------------------------------------------------
    # RDVCC 3DS mailbox (IMAP)
    # ------------------------------------------------------------------
    rdvcc_imap_host: str = ""
    rdvcc_imap_port: int = 993
    rdvcc_imap_user: str = ""
    rdvcc_imap_password: str = ""
    rdvcc_3ds_sender: str = "rdvcc"
    rdvcc_3ds_code_regex: str = r"\b(\d{6})\b"
    rdvcc_3ds_code_window_seconds: int = 90

    # ------------------------------------------------------------------
    # Outbound delivery email (optional)
    # ------------------------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "MOLIPLY 模力补给 <no-reply@moliply.com>"
    smtp_use_ssl: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def ltzf_notify(self) -> str:
        return self.ltzf_notify_url or f"{self.api_base_url}/api/v1/payments/ltzf/notify"

    @property
    def xunhupay_notify(self) -> str:
        return self.xunhupay_notify_url or f"{self.api_base_url}/api/v1/payments/xunhupay/notify"

    @property
    def xunhupay_return(self) -> str:
        return self.xunhupay_return_url or self.public_store_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
