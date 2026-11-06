# MOLIPLY 全项目架构说明

> 扫描基线：`main` 分支，工作树干净（`a710fe9 feat(cce): rebuild the console as a single-screen workbench`）。
> 本文档只描述代码事实与调用关系，不代替 `README.md` 的运营说明；所有路径均相对仓库根目录。
> 行号来自本次扫描，代码变更后可能漂移，定位时以符号名为准。

---

## 0. 一句话概括

MOLIPLY 是一个「数字权益发卡 + AI 会员自动代充 + Claude 账号托管交付」的三合一系统：

1. **商城与订单**（Next.js 前台 + FastAPI 后端）负责商品、下单、收款与自动发码；
2. **充能引擎**（Celery / 线程）拿到客户粘贴的 Session 后，按「自营 VCC 卡池 → 上游代充 API」的顺序完成真实订阅升级；
3. **CCE 账号托管台**（`/cce` 子系统）用住宅 IP + Camoufox 指纹环境批量注册 Claude 账号、开 Pro、封存 OAuth 凭证，再签发带到期时间的客户端交付 key。

三者共用一个 FastAPI 进程（`/cce` 是 in-process mount），但数据、数据库、浏览器与队列彼此隔离。

---

## 1. 规模与技术栈

| 维度 | 事实 |
| --- | --- |
| 后端 Python | 19,172 行（`app/` 8,961、`cce_console/` 4,122、`tests/` 3,089、迁移/脚本等其余） |
| 前端 TypeScript | 10,248 行（其中 `src/app/` 7,784） |
| 独立服务 | `upstream-service`（FastAPI + SQLite，1,040 行） |
| 测试 | 18 个后端测试模块（+conftest）/ 167 个 `test_*` 函数；上游服务 26 个测试函数 |
| 数据库 | PostgreSQL 16（主站）、SQLite（上游服务、CCE 各自独立） |
| 队列 | Redis 7 + Celery 5（worker/beat），可降级为进程内线程 |
| 浏览器自动化 | Playwright + Chromium（主站自营结账）、Camoufox（CCE 注册/订阅） |
| 反向代理 | Nginx（仓库自带，双域名）、Caddy（共享机实际部署，`deploy/`） |
| 语言/框架版本 | Python 3.11、FastAPI 0.143.5、SQLAlchemy 2.0.36、Pydantic 2.14.1、Next 16.4.2、React 19.3.1、Node 24 |

> `README.md` 的测试数量已随本次加固同步为 167 + 26。

---

## 2. 仓库目录地图

```text
.
├── recharge-gateway-backend/          # 主后端（FastAPI）
│   ├── app/
│   │   ├── main.py                    # 应用装配、lifespan、/healthz、/api/v1/brand、/cce 挂载
│   │   ├── config.py                  # pydantic-settings，全部环境变量
│   │   ├── database.py                # engine/Session、Alembic 驱动、兼容补列、seed 入口
│   │   ├── models.py                  # 15 张表 + 10 个枚举
│   │   ├── schemas.py                 # 24 个请求模型（基类 extra=forbid）
│   │   ├── security.py                # 限流、TOTP、后台/客户 JWT、失败锁定
│   │   ├── seed.py                    # 演示商品/库存/优惠码 + GPT Pro 补种
│   │   ├── api/                       # 5 个路由模块（见 §5）
│   │   ├── services/                  # 业务核心（见 §6）
│   │   │   ├── payments/              # 7 种支付驱动 + 脱敏/闸门
│   │   │   ├── chains/                # TRC20 / EVM / BTC 链上监听
│   │   │   └── fulfillment/           # 自营卡池、Playwright、IMAP 3DS
│   │   └── tasks/                     # Celery app、worker、beat 计划
│   ├── cce_console/                   # /cce 子系统（见 §10）
│   ├── alembic/                       # 4 个迁移版本（baseline + 3 个增量）
│   ├── SPEC.md                        # CCE 不变量摘要（MCP 知识资源）
│   ├── scripts/rotate_secrets.py      # 生成正确格式的密钥轮换块
│   ├── docs/                          # CCE 文档转口页（MCP 知识资源）
│   ├── tests/                         # 18 个测试模块 + conftest（含 CCE 不变量）
│   ├── scripts/add_gpt_products.py    # 幂等补种 GPT Pro SKU
│   ├── data/cce.sqlite3               # CCE 开发库（被 *.sqlite3 忽略）
│   └── cce.secrets                    # CCE 网关授权快照（已跟踪）
├── recharge-gateway-frontend/         # Next.js 商城/充能中心/后台
│   └── src/{app,components,config,lib}
├── upstream-service/                  # 自建上游履约服务（HMAC 契约）
│   ├── app/main.py                    # 全部接口 + 上游库存/工单
│   ├── app/suppliers/yunshu.py        # 云枢适配器
│   └── deploy/upstream.sh             # 独立部署
├── nginx/                             # 双域名反代、限流区、安全头
├── deploy/                            # Caddy 共享机隔离部署、release、备份、预检
├── docker-compose.yml                 # 本地全栈（db/redis/api/worker/beat/web/nginx）
├── deploy/docker-compose.server.yml   # 生产隔离拓扑（Caddy 前置）
├── docs/self-hosted-upstream.md       # 上游契约逐字说明
├── docs/GO-LIVE-CHANNELS.md           # 双通道渐进式实盘切换 SOP
├── archive/storefront-theme-moliply/  # 独角数卡主题 + PHP 支付插件（可选）
├── design/*.png                       # 17 张设计稿（被 .gitignore 排除）
├── .env.example                       # 237 行环境变量模板
└── prod.env                           # 线上配置快照（已不跟踪，本地保留）
```

`./.deploy-staging/`（旧版部分拷贝）已删除；`prod.env` 与 `*.secrets` 已从 Git 索引移除并加入 `.gitignore`，`scripts/rotate_secrets.py` 生成新密钥块，`deploy/release.sh` 会在健康检查前先跑迁移。

---

## 3. 运行时拓扑

### 3.1 本地全栈（`docker-compose.yml`）

```text
浏览器 ──> nginx:80 ──┬── /api/, /healthz ──> api:8000 (FastAPI, TASK_MODE=celery)
                       └── /* ──────────────> web:3000  (Next.js standalone)
db: postgres:16 ──┐
redis:7 ──────────┼──> worker (Celery, Chromium, 2g/1g shm)
                  └──> beat  (Celery beat)
```

关键点：

- `api` 只绑定 `127.0.0.1:${API_PORT:-8000}`；`web` 不暴露宿主端口（`docker-compose.yml:118-121`）。
- `worker` 构建参数 `INSTALL_BROWSER=true`，运行 `celery worker --concurrency=2`（`:59-75`）。
- `beat` 只跑 `celery beat`，用于清理凭证、扫链、日报（`:83-99`）。
- Nginx 上游固定为 `web:3000` / `api:8000`（`nginx/nginx.conf:33-41`）。

### 3.2 生产共享机（`deploy/docker-compose.server.yml`）

- 项目名固定 `moliply`，容器名 `moliply-*`；db/redis/api/worker/beat 不映射宿主端口。
- `api` 装 CCE，并挂 `moliply_console_data:/data`，`CCE_DB_PATH=/data/cce.sqlite3`（`:78-102`）。
- 宿主 Caddy 反代：`/cce` → `172.18.0.1:18080`，`/*` → `moliply-web:3000`；`web` 只在 `127.0.0.1:18088` 供本机验证（`:91-96`、`:195-202`）。
- `deploy/release.sh`：clone 到 `releases/<ts>` → build → 切 `current` 软链 → 健康检查失败自动回滚 → 迁移到 Alembic head。
- `deploy/preflight.sh`：只读上线体检，优先读取数据库里的运行时设置而不是 `.env`。
- `deploy/backup-db.sh`：`pg_dump` + 校验 + 保留 14 天，连同 `.env` 一起备份（`.env` 内含 `MASTER_AES_KEY`）。

### 3.3 上游服务（`upstream-service/docker-compose.server.yml`）

独立 compose 项目 `moliply-upstream`，只暴露 `172.18.0.1:18099`，由 Caddy 以 `/upstream/*` 接入；SQLite 卷 `moliply_upstream_data`。

### 3.4 Nginx 双域名

| 文件 | 作用 |
| --- | --- |
| `nginx/nginx.conf` | 全局：`client_max_body_size 2m`、`redeem_zone=10r/m`、`general_zone=120r/m`、隐藏版本号 |
| `nginx/conf.d/shop.moliply.conf` | shop 域：静态缓存、`/api/` 限流反代、SPA 反代 |
| `nginx/conf.d/up.moliply.conf` | up 域：`/` 302 到 `/redeem`，其余同上 |
| `nginx/snippets/proxy.conf` | 统一 `X-Forwarded-*`、`X-Brand`、65s 超时 |
| `nginx/ssl.example.conf` | 443/HSTS/ACME 模板 |

---

## 4. 后端入口与装配

### 4.1 `app/main.py`

| 行 | 符号 | 说明 |
| --- | --- | --- |
| 28-59 | `lifespan()` | 启动：`init_db()` → 清理过期 Session 凭证 → 打印运行模式 → 调用 `app.state.cce_startup`；关闭：`cce_shutdown` |
| 62-69 | `app = FastAPI(...)` | 仅 `debug=true` 开 `/docs`；`/redoc` 始终关闭 |
| 71-72 | `limiter` / 429 handler | slowapi 限流 |
| 74-80 | `CORSMiddleware` | 白名单来自 `CORS_ORIGINS` |
| 83-91 | `security_headers()` | `nosniff`、`DENY`、`Referrer-Policy`、`Permissions-Policy`、`X-Brand` |
| 94-107 | `redeem_error_handler` / `order_error_handler` | 把业务异常转成 `{detail, code}` |
| 110-112 | `GET /healthz` | 存活探针（Docker healthcheck 使用） |
| 115-156 | `GET /api/v1/brand` | 品牌、支付模式、加密币种就绪度、客服 |
| 159-166 | `include_router` | 挂载 auth/store/redeem/payments/admin（admin 两套：公开登录 + 受保护） |
| 168-178 | `app.mount("/cce", console_app)` | 尝试挂载 CCE；失败只记日志，不阻塞商城启动 |

`app.state.cce_startup` / `cce_shutdown` 让 CCE 的启动钩子跟随主应用 lifespan 执行（`main.py:47-59`），因此 CCE 不需要独立进程。

### 4.2 `app/config.py`

`Settings(BaseSettings)`（`:7`）从 `.env`、`../.env`、`.env.local` 读取，`extra=ignore`。共分 12 组：应用、品牌、飞书、持久化/队列、安全、官方支付、加密收款、上游、OpenAI 预检、自营 VCC、3DS IMAP、SMTP。派生属性：

- `crypto_coin_list`（`:148`）
- `nowpayments_ipn`（`:152`）
- `cors_origin_list`（`:250`）
- `is_production`（`:253`）
- `alipay_notify` / `wechat_notify`（`:257-263`）

`settings` 是模块级单例（`:271`）。

### 4.3 `app/database.py`

| 行 | 符号 | 说明 |
| --- | --- | --- |
| 18-24 | `engine` / `SessionLocal` | SQLite 时加 `check_same_thread=False`；`expire_on_commit=False` |
| 27-28 | `class Base(DeclarativeBase)` | 所有 ORM 的基类 |
| 31-36 | `get_db()` | FastAPI 依赖，请求结束关闭会话 |
| 39-110 | `ensure_schema_compatibility()` | 对旧库补 `orders.user_id`、支付字段、VCC 卡池字段；PostgreSQL 上加宽数值列；回填卡指纹；把 JSON `null` 归一为 SQL NULL |
| 113-146 | `_alembic_config()` / `_run_migrations()` | 空库直接 `upgrade head`；无版本表的旧库先 `stamp 2a8f730946fe` 再升级 |
| 173-196 | `_strict_migrations()` / `init_db()` | 生产或 `STRICT_MIGRATIONS=1` 时迁移失败直接抛错，由 `release.sh` 回滚 |
| 149-170 | `_backfill_card_fingerprints()` | 用 `card_fingerprint()` 回填历史卡 |
| 173-191 | `init_db()` | 迁移失败降级 `create_all`，再跑兼容补列，必要时 seed |
| 194-196 | `session_scope()` | 后台线程取会话 |

> 生产环境迁移失败会中止启动（容器健康检查失败 → `deploy/release.sh` 回滚）；只有开发环境才降级 `create_all`。`ensure_schema_compatibility()` 保留为旧库兜底，同样的列集合已在 `alembic/versions/e9f4a5b6c7d8_consolidate_schema_compat.py` 里幂等表达。

---

## 5. HTTP 接口全表

所有业务接口前缀 `/api/v1`。限流装饰器来自 `app/security.py:limiter`。

### 5.1 公开商城 `app/api/store.py`（`prefix=/api/v1/store`）

| 行 | 方法/路径 | 处理函数 | 说明 |
| --- | --- | --- | --- |
| 40 | GET `/categories` | `categories()` | 分类 + 在售计数 |
| 45 | GET `/products` | `products()` | 分类/交付/搜索/排序/分页，`SORT_OPTIONS` 见 `:31-37` |
| 79 | GET `/products/{slug}` | `product_detail()` | slug 或数字 ID |
| 91 | POST `/coupons/validate` | `coupon_validate()` | `lookup_rate_limit` |
| 106 | POST `/orders` | `create_order()` | 建/验账户 → `create_order` → 签发客户 JWT |
| 132 | GET `/my/orders` | `my_orders()` | 需 `require_user`，最多 50 条 |
| 148 | GET `/orders/{order_no}` | `order_detail()` | 仅所属账户 |
| 158 | GET `/orders/{order_no}/status` | `order_status()` | 支付页轮询 |
| 177 | POST `/orders/{order_no}/pay` | `pay()` | 创建支付意图，超时订单 409 |
| 195 | POST `/orders/{order_no}/crypto-check` | `crypto_check()` | 立即触发扫链 |
| 215 | POST `/orders/{order_no}/mock-settle` | `mock_settle()` | 模拟支付成功 |
| 230 | GET `/payment-title-preview` | `payment_title_preview()` | 返回脱敏后的网关商品名 |

`_get_order()`（`:141`）统一做订单归属校验。

### 5.2 客户账户 `app/api/auth.py`（`prefix=/api/v1/auth`）

| 行 | 方法/路径 | 处理函数 |
| --- | --- | --- |
| 15 | POST `/login` | `login()` |
| 30 | GET `/me` | `me()` |
| 40 | POST `/change-password` | `change_password()`（改密后 `token_version+1` 使旧令牌失效） |

### 5.3 充能中心 `app/api/public_redeem.py`（`prefix=/api/v1/redeem`）

| 行 | 方法/路径 | 处理函数 |
| --- | --- | --- |
| 13 | POST `/verify-code` | `verify_code()` |
| 19 | POST `/parse-session` | `parse_session()` |
| 25 | POST `/submit` | `submit()` |
| 32 | GET `/status/{task_id}` | `status()` |

### 5.4 支付回调 `app/api/payments.py`（`prefix=/api/v1/payments`）

| 行 | 方法/路径 | 处理函数 | 说明 |
| --- | --- | --- | --- |
| 48 | POST `/alipay/notify` | `alipay_notify()` | 表单/查询串 → `process_payment_notify("alipay")` |
| 67 | POST `/wechat/notify` | `wechat_notify()` | 原样 body + `wechatpay-*` 头 → 验签/AEAD |
| 97 | POST `/nowpayments/ipn` | `nowpayments_ipn()` | `x-nowpayments-sig` HMAC |

`_read_payload()`（`:21`）、`_report_callback_failure()`（`:37`）是共用辅助。

### 5.5 运营后台 `app/api/admin_manage.py`

两套路由：

- `router`（`:83`，公开）：`POST /login`、`POST /logout`、`GET /session`
- `protected`（`:84`，`dependencies=[Depends(require_admin)]`）：其余全部

| 行 | 方法/路径 | 处理函数 |
| --- | --- | --- |
| 104 | POST `/login` | `admin_login()`（用户名 + 密码 + TOTP + IP 锁定 + 审计 + cookie） |
| 188 | POST `/logout` | `admin_logout()` |
| 194 | GET `/session` | `admin_session()`（给 Caddy `forward_auth` 用） |
| 246 | GET `/overview` | `overview()`（统计、12 天趋势、通道状态） |
| 336 | GET `/orders` | `admin_orders()` |
| 368 | GET `/orders/{order_no}` | `admin_order_detail()` |
| 376 | POST `/orders/{order_no}/fulfill` | `admin_fulfill()`（缺货补发） |
| 397 | POST `/orders/{order_no}/mark-paid` | `admin_mark_paid()`（人工结算） |
| 418 | POST `/users/reset-password` | `admin_reset_user_password()` |
| 447 | GET `/settings` | `admin_get_settings()`（脱敏 + 回调地址 + 通知日志） |
| 460 | POST `/feishu/test` | `admin_test_feishu()` |
| 479 | PATCH `/settings` | `admin_update_settings()` |
| 514 | GET `/codes` | `admin_codes()` |
| 561 | POST `/codes/generate` | `admin_generate_codes()` |
| 602 | POST `/codes/import` | `admin_import_codes()`（`本地码,上游码`，非 MOLI 格式则生成本地码） |
| 654 | PATCH `/codes/{code_id}/status` | `admin_code_status()`（仅 UNUSED↔DISABLED） |
| 687 | GET `/tasks` | `admin_tasks()` |
| 714 | POST `/tasks/{task_id}/retry` | `admin_retry_task()` |
| 725 | POST `/tasks/{task_id}/resolve` | `admin_resolve_task()`（人工复核出口） |
| 739 | POST `/tasks/cleanup` | `admin_cleanup_tokens()` |
| 800 | GET `/cards` | `admin_cards()`（掩码卡号 + 池状态） |
| 856 | POST `/cards` | `admin_add_card()` |
| 867 | POST `/cards/batch-import` | `admin_batch_import_cards()`（结构化 JSON） |
| 900 | POST `/cards/paste-import` | `admin_paste_import_cards()`（解析器 `card_import.parse_batch_cards`，支持 dry-run） |
| 1008 | POST `/cards/{card_id}/calibrate` | `admin_calibrate_card()` |
| 1034 | GET `/cards/summary` | `admin_card_summary()` |
| 1050 | POST `/cards/{card_id}/mark-closed` | `admin_mark_card_closed()` |
| 1073 | PATCH `/routing-policy` | `admin_routing_policy()` |
| 1097 | PATCH `/cards/{card_id}` | `admin_update_card()` |
| 1127 | GET `/products` | `admin_products()` |
| 1147 | PATCH `/products/{product_id}` | `admin_update_product()` |
| 1177 | POST `/accounts/import` | `admin_import_accounts()` |
| 1214 | GET `/channels` | `admin_channels()` |
| 1241 | GET `/channels/supplier` | `admin_get_supplier()`（转发上游服务） |
| 1250 | POST `/channels/supplier` | `admin_save_supplier()` |
| 1289 | POST `/channels/supplier/probe` | `admin_probe_supplier()`（实拉报价 + 对表算毛利） |

卡号创建核心 `_create_vcc_card()`（`:745`）：Luhn → 国家校验 → BIN 白名单 → SHA-256 去重 → AES-GCM 落库。

### 5.6 CCE 路由

见 §10.4（全部在 `cce_console/api/app.py`）。

---

## 6. 业务核心模块

### 6.1 数据模型 `app/models.py`

枚举：

| 枚举 | 行 | 取值 |
| --- | --- | --- |
| `DeliveryType` | 39 | `ACTIVATION_CODE` / `ACCOUNT_CREDENTIALS` |
| `ProductCategory` | 44 | AI 会员 / 软件 / 开发 / 设计 / 数字 / API |
| `CodeStatus` | 68 | `UNUSED` / `LOCKED` / `USED` / `DISABLED` |
| `ChannelMode` | 75 | `UPSTREAM`、`SELF_VCC`（V1 兼容）+ `UPSTREAM_ONLY`、`SELF_VCC_ONLY`、`HYBRID_SELF_FIRST` |
| `RegionPreference` | 84 | `PH_FIRST` / `US_ONLY` |
| `SkuType` | 89 | 12 个 SKU（Plus 1/3/12 月、Claude Pro、Pro 各档、Codex 接码、API 额度、通用） |
| `RechargeState` | 120 | `PENDING` → `CHECKING_ACCOUNT` → `CREATING_CHECKOUT` → `PAYING` → `AWAITING_3DS` → `SUCCESS` / `FAILED` |
| `OrderStatus` | 130 | `PENDING` / `PAID` / `DELIVERED` / `FAILED` / `CANCELLED` / `REFUNDED` |
| `InventoryStatus` | 139 | `AVAILABLE` / `SOLD` / `DISABLED` |
| `VccStatus` | 145 | `ACTIVE` / `EXHAUSTED` / `SUSPENDED` / `CLOSED` |

实体（表名 / 行）：

| 类 | 表 | 用途 | 关键关系 |
| --- | --- | --- | --- |
| `Product`（152） | `products` | 商品 | 1→N `ProductVariant`（selectin） |
| `ProductVariant`（186） | `product_variants` | 规格、价格、SKU、通道 | → `Product` |
| `PlatformSetting`（203） | `platform_settings` | 运行时键值；`is_secret` 标记密文 | 主键 `key` |
| `User`（213） | `users` | 下单即建账户；scrypt 口令；`token_version` | 1→N `Order` |
| `Order`（226） | `orders` | 订单、支付地址/金额/网络、`stock_pending` | 1→N `OrderItem` |
| `OrderItem`（261） | `order_items` | 下单快照（名称/单价/SKU/通道） | 1→N `OrderDelivery` |
| `ActivationCode`（286） | `activation_codes` | MOLI 激活码/上游映射/绑定账号 | → `OrderItem` |
| `AccountInventory`（305） | `account_inventory` | 成品账号密文 | → `Product` |
| `OrderDelivery`（318） | `order_deliveries` | 已交付密文载荷 | → `OrderItem`、`ActivationCode`、`AccountInventory` |
| `Coupon`（332） | `coupons` | 满减码、次数/期限 | — |
| `RechargeTask`（346） | `recharge_tasks` | 充值任务、执行通道/区域/计费、错误、`error_detail`/`evidence_path` 诊断 | → `ActivationCode`、`VccCard` |
| `VccCard`（373） | `vcc_cards` | 加密卡、双区账单、预占/熔断字段 | — |
| `PaymentEvent`（411） | `payment_events` | 回调幂等键（`provider+event_id` 唯一） | — |
| `NotificationLog`（425） | `notification_logs` | 飞书发送结果 | — |
| `AuditLog`（437） | `audit_logs` | 全量操作审计 | — |

### 6.2 订单与交付 `app/services/orders.py`

| 行 | 符号 | 说明 |
| --- | --- | --- |
| 74 | `OrderError` | `code/message/status_code` |
| 82 | `generate_order_no()` | `MOL<YYYYMMDD><6位随机>`，12 次重试 |
| 91 | `validate_coupon()` | 有效期/次数/门槛，折扣不超过小计 |
| 106 | `create_order()` | 服务端计价、库存校验、审计、飞书 `order.created` |
| 207 | `_direct_payment_response()` | 复用未过期直收意图 |
| 228 | `_direct_provider_for()` | 币种 → provider |
| 238 | `create_payment()` | 直收或多币种/官方网关；写回地址与金额 |
| 342 | `serialize_order()` | 交付载荷仅在可揭示时明文，含时间线 |
| 446 | `find_user_orders()` | 账户订单（≤50） |
| 456 | `mark_order_paid()` | `SELECT … FOR UPDATE`，幂等，优惠码计数，`sold_count`，审计，发货，飞书 |
| 518 | `_send_delivery_notification_async()` | SMTP 存在时后台线程发信 |
| 538 | `fulfill_order()` | 行锁 + `skip_locked` 领码/领号；不足置 `stock_pending`；成功后低库存告警 |
| 681 | `settle_mock_order()` | 模拟结算（受 `mock_payments_allowed()` 管控） |
| 693 | `_apply_success_event()` | 把 `waiting` 事件升级为成功，IntegrityError 重试 3 次 |
| 746 | `process_payment_notify()` | 验签 → 订单存在 → 币种/金额容差 → 幂等 → 结算 |

金额容差：USD 0.02，其他 0.01（`:768-770`）。

### 6.3 核销与任务 `app/services/redeem.py`

| 行 | 符号 | 说明 |
| --- | --- | --- |
| 43-69 | 常量 | `TERMINAL_STATES`、`RISKY_STATES`、`MANUAL_REVIEW_ERRORS`、`REDEEMABLE_SKUS`、`SMS_SKUS`、进度/步骤映射 |
| 117 | `RedeemError` | — |
| 147 | `_load_code()` | 归一化、IP 封禁、格式/存在性、成功清零失败计数 |
| 166 | `verify_code()` | 返回 SKU、绑定掩码、可核销性 |
| 184 | `_prepare_session()` | Session 类型匹配、绑定账号一致、账号预检 |
| 203 | `parse_and_precheck()` | 状态检查 + 解析 + 预检，不落库 |
| 246 | `submit_recharge()` | Redis 锁 + 行锁 + 绑定 + 置 `LOCKED` + 建任务 + 审计 + 派发 |
| 334 | `dispatch_recharge()` | `celery` 投递 / `inline` 起线程 / `manual` 留队 |
| 352 | `_finalize_success()` | 码置 `USED`、清空凭证、审计、飞书 |
| 392 | `_finalize_failure()` | 按 `release_code` 决定释放或保持锁定 |
| 440 | `_reconstruct_session()` | 从密文还原 Session |
| 465 | `run_recharge_task()` | 任务锁 900s；风险态直接转人工；预检 → 调度 → 终结 |
| 563 | `serialize_task()` | 步骤/进度/错误/可重试性 |
| 609 | `retry_task()` | 人工复核类错误禁止自动重试 |
| 666 | `resolve_manually()` | 人工确认成功/失败 |
| 721 | `cleanup_sensitive_tokens()` | 终态 5 分钟后清空凭证 |

### 6.4 渠道路由 `app/services/channel_dispatcher.py`

| 行 | 符号 | 说明 |
| --- | --- | --- |
| 44 | `DispatchOutcome` | 通道/区域/币种/金额/上游单号 |
| 54 | `ChannelFallback` | `can_fallback` / `notify` |
| 70 | `effective_mode()` | 全局策略压过单码；V1 值映射到 V2 |
| 91 | `upstream_configured()` | mock 视为已配置 |
| 105 | `_attempt_upstream()` | 解密上游码、取 `sessionToken`、轮询、写回单号 |
| 137 | `_candidate_regions()` | PH→US 或仅 US |
| 145 | `_attempt_self()` | 逐区域：验代理 → 算价 → 预占卡 → 建结账 → Playwright 付款 → 结算/回滚 |
| 263 | `dispatch()` | 三种策略执行；混合模式按阶段决定回退或锁定 |

失败按「是否点过付款按钮」分三档（`_attempt_self` 的 `SelfVccFailure` 分支）：
未提交 → 释放预占 + 计熔断 + 换区域；明确拒付 → 卡片挂起 + 换区域；已提交且结果不明 → `PAYMENT_OUTCOME_UNKNOWN`、锁码转人工。只有前两档能被外层回退到上游。

熔断器在 `app/services/fulfillment/self_vcc_circuit.py`：15 分钟内 3 次环境故障 → `circuit:self_vcc:open` 30 分钟，期间混合策略直接走上游；成功一次即清零，也可由 `POST /api/v1/admin/channels/self-vcc/circuit/reset` 手动解除。Redis 不可用时退化为进程内窗口。

### 6.5 自营卡池 `app/services/fulfillment/vcc_pool_scheduler.py`

| 行 | 符号 | 说明 |
| --- | --- | --- |
| 43-52 | 阈值读取 | 连续失败上限、冷却分钟、低水位 |
| 55 | `_alert()` | 飞书 + SMTP 双通道 |
| 69 | `acquire_card()` | `SKIP LOCKED`，要求 `可花余额 ≥ 所需`、绑定额未满、未冷却；PH 必须有 PH 账单；预占金额 |
| 117 | `peek_card()` | 只读预览 |
| 142 | `release_reservation()` | 回滚预占与绑定额 |
| 153 | `mark_success()` | 扣真实成本、清熔断、按余额/绑定判断 `EXHAUSTED` |
| 177 | `suspend()` | 硬拒付立即挂起；软拒付累计到阈值 |
| 214 | `is_hard_decline()` | `declined/stolen/expired/fraud` 等 |
| 219 | `record_failure()` | 释放预占 + 熔断 |
| 225 | `check_watermark()` | 低于水位告警 |
| 255/265/274 | `mark_closed()` / `calibrate_balance()` / `summary()` | 销卡、余额校准、池统计 |

### 6.6 区域与结账页 `app/services/region_router.py`

- `REGIONS`（`:26`）：PH/PHP/₱ 与 US/USD/$ 的代理键、账单键、最低余额键、成本键。
- `determine_region()`（`:53`）：`US_ONLY` 或 Session 币种为 USD → US，否则 PH。
- `proxy_for()`（`:61`）：区域代理，回退 `PROXY_URL`。
- `cost_usd_for()` / `amount_for()`（`:85`、`:105`）：按 SKU 分档计价。
- `create_checkout_url()`（`:149`）：模板优先，否则调 `POST /backend-api/payments/checkout`。
- `_validated_checkout_url()`（`:134`）：HTTPS + 主机白名单（`net_guard`）。

### 6.7 浏览器结账 `app/services/fulfillment/self_vcc_worker.py`

| 行 | 符号 | 说明 |
| --- | --- | --- |
| 46 | `SelfVccFailure` | 带 `release_code` / `card_id` |
| 58 | `check_proxy_health()` | 经代理查 ipinfo，校验国家 |
| 87 | `select_vcc_card()` | 走统一调度器 |
| 154 | `PlaywrightCheckout` | Chromium（`--disable-blink-features=AutomationControlled`），逐 frame 填卡 |
| 160/172/196/208/224 | `_fill_first` / `_select_or_fill_first` / `_click_first` / `_visible_code_input` / `_cancel_autorenew` | 选择器容错 |
| 243 | `PlaywrightCheckout.run()` | 白名单 → 启动 → 填表 → 提交 → 3DS(IMAP) → 判定成功/拒付/转人工 → 取消续订 |
| 397 | `run_self_vcc_recharge()` | V1 单卡入口（保留） |

`PlaywrightCheckout.run()` 用局部变量 `submitted` 记录是否点过付款按钮，所有异常在单一 `except` 里补上 `payment_submitted`、页面提示原文与截图路径（`_capture_failure_snapshot`，卡号框先模糊，保留最近 50 张）。页面提示只作运营诊断，不覆盖给客户看的 `error_message`。

`app/services/self_vcc_worker.py`、`vcc_pool_scheduler.py`、`stripe_playwright.py`、`imap_3ds_reader.py` 都只是 `fulfillment` 包的 `import *` 兼容壳。

### 6.8 上游适配 `app/services/upstream_provider.py`

- `_headers()`（`:70`）：`X-Merchant-Key` + `X-Timestamp` + `HMAC-SHA256("ts.body")`。
- `_http_recharge()`（`:84`）：HTTPS/白名单校验 → `POST /api/v1/recharge` → 状态映射。
- `_poll()`（`:139`）：1.5 倍退避、上限 30s、总时长 `upstream_max_wait_seconds`；超时不释放码。
- `channel_status()`（`:171`）、`supplier_config/save_supplier/probe_supplier`（`:213-220`）：通道管理与报价转发。

### 6.9 支付驱动 `app/services/payments/`

| 文件 | 类/函数 | 要点 |
| --- | --- | --- |
| `base.py` | `PaymentProvider`(44)、`PaymentIntent`(16)、`ParsedNotify`(32)、`PaymentProviderError`(8) | 契约 |
| `__init__.py` | `get_provider`(84)、`get_callback_provider`(67)、`masked_order_title`(38)、`official_payment_ready`(47) | 回调始终用真实验签实现，即使商城是 mock |
| `desensitize.py` | `DesensitizedBillingSubject`(29)、`OfficialPaymentGatewayMixin`(44) | 固定前缀 + 订单号，127 字节上限 |
| `wechat_native.py` | `WechatNativeProvider`(50) | RSA2048 签名、AEAD 解密、mchid 比对、可选平台公钥验签 |
| `alipay_f2f.py` | `AlipayF2FProvider`(56) | RSA2 签名/验签、app_id 比对 |
| `nowpayments.py` | `NowPaymentsProvider`(38)、`ipn_signature`(32) | HMAC-SHA512（键排序紧凑 JSON）、`finished` 才成功、跳过金额校验 |
| `trc20.py` | `DirectTrc20Provider`(27) | 唯一金额选取、TRON 地址正则 |
| `direct_multi.py` | `DirectEthereumProvider`(85)、`DirectBitcoinProvider`(97) | 多币种直收 create/parse |
| `mock.py` | `MockPaymentProvider`(12) | 演示二维码 |
| `guards.py` | `mock_payments_allowed`(9) | 生产必须显式允许 |

**重要行为**：`get_provider()`（`__init__.py:97-106`）里 `wechat` 与 `alipay` 只是「顾客扫哪个 App」的标签，真正走哪个商户号由后台 `payment_gateway` 决定，所以两个按钮可能打到同一个商户。

### 6.10 链上监听 `app/services/chains/`

- `crypto_watcher.scan_pending_orders()`（`:14`）串行调用三个扫描器，单个失败不影响其他。
- `trc20_watcher`：TronGrid `transactions/trc20`，`only_to`、合约/收款地址双重比对、金额精确到 1e-6、时间窗、`PaymentEvent` 去重。
- `evm_watcher`：Etherscan `txlist`/`tokentx`，确认数、合约、金额、时间窗。
- `btc_watcher`：Blockstream `address/{addr}/txs`，vout 求和、确认数、金额。

三者都只匹配 `PENDING` 且未过期（含宽限期）的订单。

### 6.11 安全与凭证

| 文件 | 符号 | 说明 |
| --- | --- | --- |
| `app/services/crypto.py` | `encrypt_text`(49)、`decrypt_text`(57)、`mask_email`(75)、`luhn_valid`(93)、`card_fingerprint`(107) | AES-256-GCM；生产缺失 `MASTER_AES_KEY` 直接报错（`:40-41`） |
| `app/security.py` | `FailureTracker`(58)、`AdminLoginGuard`(94)、`verify_admin_totp`(144)、`require_admin`(190)、`create_user_token`(205) | 5 次错误卡密封 30 分钟；后台 5 次失败锁 15 分钟 |
| `app/services/net_guard.py` | `ensure_https`(12)、`host_allowed`(26)、`ensure_checkout_url`(43) | 阻止明文与未授权主机 |
| `app/services/locks.py` | `redis_lock`(56) | Redis 缺失时退化为数据库行锁 |
| `app/services/runtime_settings.py` | `SECRET_KEYS`(25)、`_load`(243)、`update_settings`(347)、`public_settings`(436) | 密钥落库加密；2 秒缓存；未知键拒绝 |

### 6.12 任务与定时 `app/tasks/`

- `celery_app.py`：`task_acks_late`、`prefetch=1`、软/硬超时 570/600s、时区 `Asia/Shanghai`；beat 每分钟清凭证 + 扫链，每日 09:00 日报（`:26-37`）。
- `celery_worker.py`：`execute_recharge_task(13)`、`cleanup_tokens_task(19)`、`scan_direct_crypto_payments_task(31)`、`feishu_daily_summary_task(42)`。

### 6.13 通知与邮件

- `app/services/feishu.py`：`send_feishu(109)`（HTTPS 校验、签名、卡片、日志）、`dispatch_event(168)`（线程火并忘）、`dispatch_security_alert(187)`、`send_test_message(200)`、`recent_logs(216)`。
- 事件目录 `app/services/notification_events.py:5-102`，共 16 个事件，勾选存 `feishu_events`。
- `app/services/mailer.py`：SMTP 发客户交付邮件与运营告警。
- `Imap3dsReader`（`fulfillment/imap_3ds_reader.py:127`）只有 `fetch_latest_code`(144) 与 `wait_for_code`(190) 两个入口，后者是结账流程使用的轮询封装。

---

## 7. 前端结构 `recharge-gateway-frontend/`

### 7.1 路由

| 路径 | 文件（行） | 说明 |
| --- | --- | --- |
| `/` | `src/app/(store)/page.tsx:44` | 首页 |
| `/products` | `src/app/(store)/products/page.tsx:357` | 列表/筛选 |
| `/products/[slug]` | `src/app/(store)/products/[slug]/page.tsx:41` | 详情/规格 |
| `/checkout` | `src/app/(store)/checkout/page.tsx:499` | 结算（购物车或 `?product=` 直购） |
| `/pay/[orderNo]` | `src/app/(store)/pay/[orderNo]/page.tsx:665` | 二维码/加密地址/倒计时/轮询 |
| `/orders` | `src/app/(store)/orders/page.tsx:428` | 邮箱+密码登录查单 |
| `/redeem` | `src/app/(store)/redeem/page.tsx:61` | 充能中心（`RedeemWizard`） |
| `/help` | `src/app/(store)/help/page.tsx:173` | 条款/隐私/FAQ |
| `/admin/login` | `src/app/admin/login/page.tsx:22` | 后台登录（三项凭据） |
| `/admin` | `src/app/admin/page.tsx:87` | 概览 |
| `/admin/orders` | `src/app/admin/orders/page.tsx:360` | 订单 |
| `/admin/products` | `src/app/admin/products/page.tsx:13` | 商品 |
| `/admin/codes` | `src/app/admin/codes/page.tsx:51` | 卡密 |
| `/admin/tasks` | `src/app/admin/tasks/page.tsx:20` | 任务 |
| `/admin/cards` | `src/app/admin/cards/page.tsx:68` | VCC 卡池 |
| `/admin/channels` | `src/app/admin/channels/page.tsx:26` | 通道状态 |
| `/admin/channels/supplier` | `src/app/admin/channels/supplier/page.tsx:62` | 上游凭据 + 实时报价/毛利 |
| `/admin/settings` | `src/app/admin/settings/page.tsx:139` | 5 个设置页签 |

布局：`src/app/layout.tsx:41`（根）、`src/app/(store)/layout.tsx:7`（购物车 + 头尾）、`src/app/admin/layout.tsx:8`（本地令牌门禁）。

### 7.2 组件与状态

- `src/components/admin/AdminShell.tsx:68`：侧栏/搜索/退出；`NAV_GROUPS`(`:27`) 把 `/cce/ui` 以新窗口外链。
- `src/components/RedeemWizard.tsx:43`：四步向导，`sessionStorage` 之外的 `localStorage` 续跑（30 分钟），2.2s 轮询。
- `src/lib/cart.tsx:63`：`CartProvider`，`localStorage` 键 `moliply_cart_v1`，单项上限 5。
- `src/lib/api.ts:43`：统一 `fetch`，按路径自动附后台或客户 Bearer；`ApiError` 带 `status/code`。
- `src/config/brand.ts:91-95`：`moliply_admin_token`、`moliply_user_token`、`moliply_user_email`、`moliply_cart_v1`、`moliply_last_redeem_task`。

### 7.3 前端 → 后端调用映射（节选）

| 前端 | 后端 |
| --- | --- |
| `checkout/page.tsx` → `/api/v1/store/orders` | `store.create_order` |
| `pay/[orderNo]` → `/pay`、`/status`、`/mock-settle`、`/crypto-check` | 同名接口 |
| `RedeemWizard` → `/verify-code`、`/parse-session`、`/submit`、`/status/{id}` | `public_redeem` |
| `admin/cards` → `/admin/cards*`、`/admin/routing-policy` | `admin_manage` |
| `admin/settings` → `GET/PATCH /admin/settings`、`POST /admin/feishu/test` | `admin_manage` |
| `admin/channels/supplier` → `/admin/channels/supplier*` + `PATCH /admin/settings` | 上游转发 + 运行时设置 |

构建期通过 `next.config.mjs:2-17` 把 `/api`、`/healthz` 重写到 `API_PROXY_TARGET`；浏览器侧 API 基址由 `NEXT_PUBLIC_API_BASE_URL` 覆盖（`src/lib/api.ts:3`）。

---

## 8. 上游履约服务 `upstream-service/`

### 8.1 契约与鉴权

- `authenticate()`（`app/main.py:113`）：`X-Merchant-Key` 常量时间比对 + `X-Timestamp` ±300s + `HMAC-SHA256(secret, "ts.body")`。
- `sign()`（`:109`）与商城 `UpstreamProvider._headers()` 对称。
- 接口：`POST /api/v1/recharge`、`GET /api/v1/recharge/{order_no}`、`POST/GET /api/v1/supplier`、`POST /api/v1/supplier/probe`（`:328-478`）。

### 8.2 履约模式 `FULFILL_MODE`

| 值 | 行为（`run_fulfillment`，`:251`） |
| --- | --- |
| `manual` | 一律 `pending`，等后台人工结单 |
| `stock` | `claim_stock()` 认领预导入码 → `success`；无码则 `pending` |
| `supplier` | `build_supplier()` → `YunshuSupplier.deliver()`；`Rejected/Uncertain/Unavailable` 分别落 `failed/pending/pending` |

`repoll()`（`:304`）只在查询接口对 `pending + supplier` 单子补查一次。

### 8.3 云枢适配 `app/suppliers/yunshu.py`

`_post()`（`:43`）表单编码 + Bearer；`deliver()`（`:97`）`/order` → `/check` → `/redeem` → `/result` 轮询；`quote()`（`:90`）报价。异常类型定义在 `app/suppliers/base.py:17-30`，用于「可释放 / 不确定 / 暂时不可用」分类。

### 8.4 管理面

`/admin` 单页、`/admin/api/orders`、`/admin/api/orders/{no}/settle`、`/admin/api/stock`，鉴权只认 `x-admin-token` 头（不再接受 `?token=`），同一 IP 15 分钟内 5 次失败封禁 15 分钟。

---

## 9. 完整业务流

### 9.1 购买 → 支付 → 交付

```text
checkout.submit()
  └─ POST /api/v1/store/orders
       ├─ accounts.get_or_create_user()         # scrypt；同邮箱错密码 401
       ├─ orders.create_order()                 # 服务端价、库存、优惠码、审计
       └─ create_user_token()                   # 客户 JWT
  └─ 跳转 /pay/{order_no}?provider=
       └─ POST /orders/{no}/pay
            └─ orders.create_payment()
                 ├─ 官方网关 → wechat_native / alipay_f2f
                 ├─ NOWPayments → invoice_url
                 └─ 直收 → 唯一金额 + 收款地址
  └─ 网关异步回调 / 链上扫描 / mock-settle
       └─ orders.process_payment_notify()
            ├─ 验签 + 金额/币种比对 + PaymentEvent 幂等
            └─ orders.mark_order_paid()
                 ├─ 行锁、优惠码计数、销量累加、审计、飞书
                 └─ orders.fulfill_order()
                      ├─ 激活码：SKIP LOCKED 领码，加密写入 OrderDelivery
                      └─ 成品号：领账号，标记 SOLD，加密载荷
```

结果：订单 `DELIVERED` 或 `PAID + stock_pending`；客户在 `/orders` 用下单账号查看明文，或以 `?code=` 进入充能中心。

### 9.2 核销 → 充能

```text
verify-code → parse-session → submit
  └─ redeem.submit_recharge()
       ├─ Redis 锁 lock:redeem:{code} + 行锁
       ├─ 绑定账号、码置 LOCKED
       ├─ 建 RechargeTask（账号口令/原始 Session 加密）
       └─ dispatch_recharge()
            └─ Celery / 线程 → run_recharge_task()
                 ├─ 任务锁 900s
                 ├─ 风险态 → PAYMENT_OUTCOME_UNKNOWN（保持锁定）
                 ├─ 重建 Session → check_account()
                 └─ channel_dispatcher.dispatch()
                      ├─ UPSTREAM_ONLY → _attempt_upstream()
                      ├─ SELF_VCC_ONLY → _attempt_self()
                      └─ HYBRID_SELF_FIRST → 自营 PH→US → 失败回上游
                 └─ 成功核销 / 可恢复失败释放 / 不确定保持锁定转人工
```

### 9.3 自营单区域尝试

```text
_candidate_regions()
  └─ region_router.proxy_for(region)
  └─ region_router.amount_for(region, sku) / cost_usd_for()
  └─ vcc_pool_scheduler.acquire_card(region, amount)
       ├─ 无卡 → ChannelFallback(NO_ACTIVE_VCC_IN_POOL) → 下一个区域
       └─ 有卡 → 预占金额 + 绑定额
  └─ region_router.create_checkout_url() → 白名单
  └─ stripe_playwright.checkout_and_cancel_autorenew()
       └─ PlaywrightCheckout.run() → 3DS → IMAP 取码
  └─ mark_success() / release_reservation() / record_failure()
```

### 9.4 CCE 账号流水线

```text
导入 email,app_password,ip,proxy,plan
  └─ pipeline.parse_import() → pipeline.create_job()
       ├─ profiles.create_profile()             # 指纹/端口/出口 IP 一次定死
       └─ account_jobs(stage=queued)             # 口令与应用密码 AES-GCM
  └─ _worker_loop / 手动 run
       └─ pipeline.run_job()
            └─ BrowserPool.acquire_context()
                 ├─ ProxyInspector.verify_and_inspect()  # 出口 IP 漂移即 PROXY_DEAD
                 └─ Camoufox(指纹/时区/语言/代理)
            └─ signup.run_signup()              # Cloudflare/hCaptcha/邮件码
            └─ card_pool.lease_card()
            └─ subscribe.run_subscribe()        # 跨 iframe 填卡开 Pro
            └─ BrowserPool.sync_claude_cli_credentials()
            └─ stage=ready
```

---

## 10. CCE 子系统 `cce_console/`

### 10.1 装配

`cce_console/__init__.py:16-22`：把 `cce_console/` 插入 `sys.path`（因为内部用裸 `core`/`db`/`api` 导入），设 `CCE_BASE_PATH=/cce`，再 `create_app()`。主应用在 `app/main.py:168-178` 用 try/except 挂载，失败只降级 404。

### 10.2 模块职责

| 路径 | 主要符号 |
| --- | --- |
| `api/app.py` | `create_app`(242)、`require_admin`(156)、`_moliply_admin`(139)、`_launcher_key`(181)、`_reaper_loop`(202)、`_worker_loop`(220)、全部路由 |
| `api/console.html` | 789 行单文件控制台（`apiFetch`(227)、`refreshAll`(293)、`renderList`(376)、`onAction`(471)） |
| `core/pipeline.py` | `ImportRow`(40)、`parse_import`(55)、`create_job`(112)、`run_job`(205)、`_drive`(250)、`run_pending`(320)、`MAX_AUTO_ATTEMPTS=3`(184) |
| `core/signup.py` | `SignupOutcome`(52)、`run_signup`(134)、`_solve_captcha`(101) |
| `core/subscribe.py` | `SubscribeOutcome`(70)、`run_subscribe`(127)、`_fill_in_any_frame`(89) |
| `core/mail_reader.py` | `GmailReader`(130)、`extract_code`(79)、`check_credentials`(199) |
| `core/card_pool.py` | `issue_card`(46)、`lease_card`(119)、`settle_card`(144)、`release_card`(159) |
| `core/profile_manager.py` | `ProfileManager`(231)、`FingerprintSpec`(86)、`create_profile`(250)、`scrub_profile_cache`(302)、`enforce_disk_ceiling`(338) |
| `core/browser_pool.py` | `BrowserPool`(34)、`acquire_context`(56)、`verify_stealth`(109)、`sync_claude_cli_credentials`(160) |
| `core/proxy_inspector.py` | `ProxyInspector`(97)、`inspect`(102)、`verify_and_inspect`(145)、`ProxyKillSwitchError`(67) |
| `core/singbox_manager.py` | `SingBoxManager`(61)、`register`(86)、`render`(96)、`reload`(118) |
| `core/lifecycle.py` | `ALLOWED`(22)、`allows`(52)、`mark`(72)、`sweep_reaperable`(166)、`LEASE_TIMEOUT=120s`(39) |
| `core/crypto.py` | `encrypt_blob`(44)、`decrypt_blob`(51)，AAD 绑定 `profile_id` |
| `core/secret_export.py` | `export_secrets`(14)：原子写 0600 授权快照 |
| `db/database.py` | `Database`(38)、`migrate`(72)、`claim_session`(199)、`active_delivery_key`(313) |
| `db/schema.sql` | 5 张表 + 状态 CHECK + 索引 |
| `mcp_server.py` | FastMCP 9 个工具 + 3 个知识资源，复用同一服务图 |
| `util/ids.py` | `new_card_key`(18)、`new_token`(24) |

### 10.3 数据模型 `db/schema.sql`

| 表 | 用途 |
| --- | --- |
| `payment_batches` | 风控隔离批次（共享 BIN/账单国/上限） |
| `profiles` | 一条 = 一个可售身份；指纹/出口 IP 创建时定死；状态六态 |
| `delivery_keys` | `card_key` + `tunnel_secret` 双因子，带到期 |
| `subscription_cards` | 加密卡，`available/in_use/used/dead` |
| `account_jobs` | 流水线阶段、尝试次数、错误、人工备注 |

### 10.4 CCE 路由（`api/app.py`）

**健康/页面**：`GET /ui`(320)、`GET /`(334)、`GET /healthz`(350)、`GET /readyz`(354)。

**管理面**（全部 `dependencies=[admin]`）：

| 行 | 路由 |
| --- | --- |
| 375 | GET `/admin/status` |
| 423/427/499 | GET `/admin/client`、`/admin/bundle/{id}`、`/admin/client/{name}` |
| 507 | POST `/admin/batches` |
| 512/526/547 | POST/GET `/admin/profiles`、GET `/admin/profiles/{id}` |
| 554/574/587 | PATCH `.../proxy`、POST `.../status`、POST `.../release` |
| 596/601/605/610 | POST `.../preflight`、`.../stealth-check`、`.../sync-credentials`、`.../import-credentials` |
| 620/624/653/660 | GET `/admin/jobs`、POST `/admin/jobs/import`、POST `/admin/jobs/run`、POST `/admin/jobs/{id}/run` |
| 668/693/715 | PATCH `.../proxy`、PATCH `.../mail`、POST `.../skip` |
| 728/732 | GET `/admin/mail/check`、`/admin/mail/app-password-hint` |
| 741/745 | GET/POST `/admin/cards` |
| 766/770 | GET `/admin/singbox`、POST `/admin/singbox/reload` |
| 774/794/798/804 | POST `/admin/keys`、GET `/admin/keys/{id}`、DELETE `/admin/keys/{key}`、POST `/admin/tunnel-secrets` |

**启动器面**（Bearer card_key + `x-tunnel-secret`）：`POST /launcher/session`(811)、`GET /launcher/credentials`(834)、`POST /launcher/heartbeat`(850)、`POST /launcher/status`(863)、`GET /launcher/credentials/b64`(891)。

### 10.5 CCE 鉴权与状态机

- 管理面：`require_admin()` 优先验主站 JWT（cookie `moliply_admin` 或 Bearer，密钥 `MOLIPLY_ADMIN_JWT_SECRET`），否则比对 `x-admin-token`。
- 启动器：`_launcher_key()` 校验 key 有效、secret 匹配；非 `/launcher/session` 还要求 `BUSY` 且 `x-session-id` 匹配。
- 状态迁移白名单 `lifecycle.ALLOWED`；`BANNED` 只能回 `IDLE`；冷却上限 24h；120 秒无心跳回收 `BUSY`。
- `secret_export` 只导出 `IDLE/BUSY` 且未过期的 key → `CCE_TUNNEL_SECRETS`，网关据此放行端口。

### 10.6 CCE 部署形态

- 开发：进程内 `/cce`，数据 `recharge-gateway-backend/data/cce.sqlite3`。
- 生产：同进程挂载（`deploy/docker-compose.server.yml:78-102`），Caddy `handle_path /cce/*` → `172.18.0.1:18080`。
- `cce_console/README.md` 另描述了独立 systemd/8010/8444 网关形态，与当前 compose 并存，以 compose 为准。

---

## 11. 测试地图

| 文件 | 覆盖 |
| --- | --- |
| `tests/conftest.py` | 临时 SQLite、限流清零、admin fixture |
| `test_accounts.py` | scrypt 往返、改密失效、后台重置 |
| `test_admin.py` | 登录/概览/制码/导卡/鉴权/cookie |
| `test_card_import.py` | JSON/CSV/TSV、Luhn、去重、dry-run、BIN 白名单 |
| `test_card_selection.py` | 余额/绑定/熔断、预占消耗 |
| `test_crypto.py` | AES-GCM 篡改、掩码、Luhn、码格式 |
| `test_fulfillment.py` | 发货幂等、缺货等待 |
| `test_imap_3ds.py` | 验证码解析、过期/他发件人/HTML |
| `test_multi_crypto.py` | 多币种启用与 EVM/BTC 匹配 |
| `test_payments.py` | 脱敏长度、RSA 往返、微信 AEAD/签名/mchid、金额校验 |
| `test_redeem_flow.py` | 正常核销、幂等、已订阅、绑定、失败释放/保持、暴破、Claude |
| `test_security_hardening.py` | mock 闸门、TOTP、锁定、事件升级、并发预占、人工复核 |
| `test_session_parser.py` | ChatGPT/Claude/过期/包裹/会员 |
| `test_settings.py` | 设置往返、密钥掩码、飞书签名 |
| `test_stock.py` | 按变体 SKU 限库存 |
| `test_store_flow.py` | 目录、下单支付交付、账户隔离、优惠码、成品号、GPT 商品 |
| `test_trc20.py` | 唯一金额、扫链匹配/忽略、直收端点 |
| `test_v2_channel.py` | 策略/区域/回退/拒付熔断/演练/PH 地址约束 |
| `upstream-service/tests/test_service.py` | 签名、重放、幂等、结单、库存、供应商（23 个） |

运行：`cd recharge-gateway-backend && .venv/Scripts/python -m pytest -q`；上游同理。

---

## 12. 安全边界与密钥

| 面 | 措施 |
| --- | --- |
| 静态数据 | 卡号、CVV、Session、成品号、上游码、供应商 token 均 AES-256-GCM；CCE 用独立 `CCE_MASTER_KEY` 且 AAD 绑 `profile_id` |
| 传输 | 所有可配置外部 URL 强制 HTTPS（`net_guard`）；结账地址主机白名单 |
| 后台 | 密码 + TOTP + JWT（12h），cookie `HttpOnly/Secure/SameSite=Lax`；5 次失败锁 15 分钟并飞书告警 |
| 客户 | 下单即建账户，scrypt；订单按 `user_id` 隔离；改密递增 `token_version` |
| 核销 | IP 暴破 5 次/30 分钟封禁；Redis + 行锁 + 状态机；风险态禁止自动重试 |
| 支付 | 回调用真实驱动验签，金额/币种比对，`PaymentEvent` 唯一约束，生产默认拒绝 mock |
| 前端 | 后台/客户 JWT 存 `localStorage`（XSS 面），同时后台也下发 HttpOnly cookie 供 `/cce` 免登录 |
| 审计 | `audit_logs` 覆盖登录、制码、导卡、改价、路由、支付、核销、重试 |
| 容器 | 生产 compose 给 api/beat/web 加 `no-new-privileges`、内存/进程上限；镜像以 uid 10001 非 root 运行（`Dockerfile:31-33`） |

敏感密钥：`MASTER_AES_KEY`、`ADMIN_JWT_SECRET`、`USER_JWT_SECRET`、`CCE_MASTER_KEY`、商户私钥、`UPSTREAM_SECRET`、`CCE_ADMIN_TOKEN`。`prod.env` 与 `recharge-gateway-backend/cce.secrets` 已随仓库提交。

---

## 13. 扩展点

| 目标 | 位置 |
| --- | --- |
| 新支付方式 | 继承 `payments/base.py:PaymentProvider`，在 `payments/__init__.py:get_provider/get_callback_provider` 注册，加回调路由 |
| 新供应商 | `upstream-service/app/suppliers/` 实现 `Supplier` 协议，在 `build_supplier()` 分支 |
| 新 SKU | `models.py:SkuType` + `SKU_LABELS` + `schemas.py` 两处 Literal + `region_router.amount_for`/`cost_usd_for` + `redeem.REDEEMABLE_SKUS` |
| 新履约通道 | `channel_dispatcher.py` 增加分支与策略值，`vcc_pool_scheduler` 式的池模块 |
| 新 CCE 阶段 | `account_jobs.stage` CHECK、`pipeline._drive`、`console.html` 的 `STAGE` 映射 |

---

## 14. 已知缺口与风险

1. **浏览器自动化仍然易碎**：选择器靠猜，官方改版必失败。现在用「阶段位 + 熔断 + 现场快照」兜底（未扣款即自动回上游，连续故障 30 分钟内不再尝试），但没有消除脆弱性本身。
2. **生产密钥需人工轮换**：`prod.env` 已不跟踪，但旧历史里仍有一份；`scripts/rotate_secrets.py` 只生成新值，改库后旧的 AES 密文会不可解，必须配备份。
3. **迁移仍保留开发降级路径**：`ensure_schema_compatibility()` 只在开发/`create_all` 时使用，但两处列定义可能再次漂移，新增列应同时进 Alembic。
4. **CCE 网关/客户端二进制不在仓库**：`gateway-linux-x64`、`claude-box*` 需另行发布，`/admin/bundle` 找不到文件时 409。
5. **CCE 依赖宿主**：纯 JS 版 `claude` CLI（2.1.100）、Camoufox 系统库、sing-box；README §9 有清单。
6. **CCE 测试只覆盖确定性部分**：状态机、加密、卡池、租约、授权快照有覆盖（`tests/test_cce_invariants.py`），注册/订阅的真实页面交互仍需人工烟测。
7. **上游管理面限流是进程内的**：单容器足够，多副本时各自计数；`/admin` 页面仍用 `localStorage` 存 token。
8. **`_attempt_self` 死代码已清理**，但注释与常量仍可能漂移，阅读时以实际分支为准。
9. **CCE MCP 知识资源是转口页**：`mcp_server.py:193-195` 读 `recharge-gateway-backend/SPEC.md` 与 `recharge-gateway-backend/docs/*.md`，这些文件已补为指向根文档的入口页；改架构时两个位置都要照顾到。

---

## 15. 建议阅读顺序

1. `README.md` → 业务边界与运营口径
2. `app/main.py` → `app/config.py` → `app/models.py` → `app/schemas.py`
3. `app/api/store.py` → `app/services/orders.py` → `app/services/payments/`
4. `app/api/public_redeem.py` → `app/services/redeem.py` → `app/services/session_parser.py`
5. `app/services/channel_dispatcher.py` → `region_router.py` → `fulfillment/*`
6. `cce_console/README.md` → `cce_console/api/app.py` → `core/pipeline.py` → `core/browser_pool.py`
7. `upstream-service/README.md` → `app/main.py` → `suppliers/yunshu.py`
8. `deploy/` 与两份 compose → 上线拓扑
9. `docs/GO-LIVE-CHANNELS.md` → 实盘切换顺序与回滚
