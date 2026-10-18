# MOLIPLY 模力补给 · 24 小时自动交付平台

MOLIPLY（模力补给）数字商品发卡商城 + 免密自助充能中心 + 运营后台。

本仓库包含可运行的商城、充能中心、运营后台、自动化测试与通用部署配置。

> 商业计划、成本资料与设计源文件保留在私有环境，不纳入公开仓库；线上服务器地址与凭据同样不在仓库中。

当前状态：**前后端构建通过、63 项后端测试通过，Docker 全栈（Nginx/Postgres/Redis/Celery）端到端冒烟通过，桌面/移动端主流程已联调。** 线上环境由维护者通过 `deploy/` 与私有 `.env` 部署。

---

## 1. 已实现能力

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 品牌化商城 | ✅ | 首页、分类、搜索/排序、商品详情、规格、购物车、结算、扫码支付、支付成功交付 |
| 订单体系 | ✅ | 服务端计价、优惠码、模拟/真实支付回调、幂等发货、订单时间线 |
| 订单账户 | ✅ | 下单邮箱+密码自动建账户，登录后查看订单/卡密；密码 scrypt 加盐哈希，订单按账户隔离 |
| MOLI 激活码 | ✅ | `MOLI-XXXX-XXXX-XXXX` 生成（排除 0/O/1/I）、批量生成/导入、上游卡密加密映射、绑定账号、状态机 |
| 免密充能中心 | ✅ | 四步向导、自动验证、ChatGPT Session / Claude Session Key、账号状态预检、实时进度轮询、失败重试 |
| 加密货币收款 | ✅ | NOWPayments 收银台，支持 USDT / USDC / BTC / ETH；默认关闭，配置商户密钥后开启 |
| 双通道充值 | ✅ | 通道 1 上游 API（模拟/HTTP）；通道 2 自营 VCC（Playwright + 美国代理 + IMAP 3DS），默认关闭+演练 |
| 自营 VCC 卡池 | ✅ | AES-GCM 加密卡号/CVV、余额与绑定上限、拒付自动挂起、失败计数、`SKIP LOCKED` 选卡 |
| Redis/Celery | ✅ | 分布式锁、Celery Worker、Beat 清理 Session 凭证；本地可切换 inline 线程模式 |
| 运营后台 | ✅ | 登录、概览、订单、商品、卡密、充值任务、VCC 卡池、渠道状态、系统设置 |
| 系统设置 | ✅ | 支付/加密支付/上游/VCC/客服/SMTP/订单配置可在后台保存，密钥加密存储并即时生效 |
| 飞书管理通知 | ✅ | 可勾选新订单、支付、交付、充值失败、人工复核、库存预警、回调异常、系统异常、每日日报 |
| 安全 | ✅ | AES-256-GCM、JWT、限流、暴力破解 IP 限制、支付回调验签/金额校验、幂等事件、审计日志 |
| 独角数卡适配 | ✅（可选） | 品牌主题 Blade + 蓝兔/虎皮椒框架无关 PHP 插件 |
| 自动化测试 | ✅ | 63 项 pytest：加密、Session、卡密、商城、支付回调、核销、失败释放、卡池、IMAP |
| 容器部署 | ✅ | Docker Compose（Postgres/Redis/API/Worker/Beat/Web/Nginx）与双域名反代 |

---

## 2. 系统架构

```text
                    shop.moliply.com                 up.moliply.com
                           │                              │
                           └──────────── Nginx ───────────┘
                                  │              │
                          Next.js 商城/后台     FastAPI
                                  │              │
                                  └──── /api ────┤
                                                 ├── PostgreSQL（订单/卡密/任务/卡池）
                                                 ├── Redis（限流/锁/Celery）
                                                 ├── Celery Worker
                                                 │    ├─ 通道 1：上游代充 API
                                                 │    └─ 通道 2：Playwright + 美国代理
                                                 │         └─ IMAP 3DS 验证码
                                                 └── 支付回调：蓝兔 / 虎皮椒
```

商城与充能中心共用同一个 Next.js 应用（通过 Nginx 双域名暴露）；充能系统与订单/卡密共用一个 FastAPI 服务，避免两套数据源不一致。

核心数据流：

1. 用户下单 → 支付网关异步回调 → 验签、金额校验、幂等事件 → 自动分配激活码/成品号 → 订单变为“已交付”。
2. 用户持 `MOLI-` 激活码进入充能中心 → 本地预检 + OpenAI 账号状态预检（可选）→ 行锁 + Redis 锁锁定卡密 → 创建任务 → 双通道充值 → 成功核销 / 失败释放或转人工。
3. 任务结束 5 分钟内清空 Session 凭证；VCC 拒付一次立即挂起卡片。

---

## 3. 目录结构

```text
.
├── recharge-gateway-backend/     FastAPI + SQLAlchemy + Celery + Playwright
│   ├── app/
│   │   ├── api/                  store / redeem / payments / admin
│   │   ├── services/             加密、卡密、Session、上游、自营 VCC、IMAP、订单、支付
│   │   ├── tasks/                Celery 任务与 Beat
│   │   └── seed.py               演示商品/库存/优惠码
│   └── tests/                    63 项 pytest
├── recharge-gateway-frontend/    Next.js 14 + Tailwind 商城 + 充能中心 + 后台
├── storefront-theme-moliply/     独角数卡主题 + 蓝兔/虎皮椒脱敏插件（可选）
├── nginx/                        双域名反代、限流、安全头
├── deploy/                       共享服务器隔离部署与 Caddy 接入说明
├── docker-compose.yml
└── .env.example
```

---

## 4. 快速开始

### 4.1 Docker Compose（推荐）

前置：Docker Desktop / Docker Engine + Compose v2。

```bash
cp .env.example .env
```

编辑 `.env`，至少完成：

```dotenv
POSTGRES_PASSWORD=<强密码>
DATABASE_URL=postgresql+psycopg2://moliply:<同一个强密码>@db:5432/moliply
SECRET_KEY=<随机长字符串>
ADMIN_PASSWORD=<强密码>
ADMIN_JWT_SECRET=<随机长字符串>
MASTER_AES_KEY=<base64 编码的 32 字节随机值>
PUBLIC_STORE_URL=http://localhost
PUBLIC_REDEEM_URL=http://localhost/redeem
API_BASE_URL=http://localhost
```

生成密钥：

```bash
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

启动：

```bash
docker compose up -d --build
docker compose ps
```

访问：

- 商城：<http://localhost>（Nginx 的 `shop` 服务块包含 `localhost`）
- 充能中心：`http://up.moliply.test/redeem`（需在 hosts 添加 `127.0.0.1 up.moliply.test`；或直接访问 `http://localhost/redeem`）
- 运营后台：<http://localhost/admin/login>
- API 文档（开发环境）：<http://localhost:8000/docs>

默认管理员用户名是 `admin`，密码为 `.env` 中的 `ADMIN_PASSWORD`。**首次启动前务必修改。**

本地演示数据：

- `SEED_DEMO_DATA=true`：首次建库时创建 10 个商品与演示优惠码；
- `SEED_DEMO_INVENTORY=true`：额外生成 252 个演示激活码、36 个演示成品号；
- 演示库存全部是假数据（`@moliply.test`），**正式上线前必须设为 `false` 并导入真实库存**。

停止 / 重置：

```bash
docker compose down
docker compose down -v   # 同时删除数据库与 Redis 数据
```

> 如果本机 80/8000 端口被占用，可在 `.env` 设置 `HTTP_PORT=8080` / `API_PORT=8001`。

### 4.2 本地开发（不用 Docker）

后端：

```bash
cd recharge-gateway-backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt      # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt  # macOS/Linux

# 使用 SQLite 快速启动（无需 Postgres/Redis）
DATABASE_URL=sqlite:///./moliply.db TASK_MODE=inline \
PUBLIC_STORE_URL=http://localhost:3000 PUBLIC_REDEEM_URL=http://localhost:3000/redeem \
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端（另一个终端）：

```bash
cd recharge-gateway-frontend
npm install
npm run dev
# 若 3000 被占用：npm run dev -- -p 3100
```

前端开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`（可用 `API_PROXY_TARGET` 覆盖）。本地使用 `TASK_MODE=inline` 时，充值任务在 API 进程的线程中执行，适合演示；生产请使用 Celery。

测试与构建：

```bash
cd recharge-gateway-backend && .venv/Scripts/python -m pytest -q
cd recharge-gateway-frontend && npm run build
```

---

## 5. 演示流程

1. 打开商城 → 选择「ChatGPT Plus 1个月代充」→ 立即购买。
2. 填写接收邮箱并设置订单密码（至少 8 位）→ 勾选协议 → 立即支付。
3. 支付页会显示二维码；演示环境点击「模拟扫码支付成功」。
4. 支付成功页出现 `MOLI-XXXX-XXXX-XXXX` 激活码，点击「立即前往自助充能中心」。
5. 激活码自动验证 → 粘贴 ChatGPT Session JSON：
   ```json
   {
     "user": { "id": "demo-user", "email": "user@example.com" },
     "expires": "2026-12-31T00:00:00Z",
     "accessToken": "demo-token",
     "account": { "planType": "free" }
   }
   ```
   （`expires` 必须是未来时间；真实场景请按页面指引从 `https://chatgpt.com/api/auth/session` 复制。）
6. 提交后任务经历 `PENDING → CHECKING_ACCOUNT → PAYING → SUCCESS`，进度实时更新。
7. 打开 `/orders`，用下单邮箱和密码登录，可看到卡密与核销完成时间。
8. 忘记订单密码时，由后台在订单详情中「重置客户密码」。
9. 打开 `/admin/login` 登录后台，查看概览、订单、卡密、任务、卡池与渠道状态。

演示优惠码：`MOLI10`（满 100 减 10）、`WELCOME5`（满 50 减 5）。

---

## 6. 关键配置

完整变量见 [`.env.example`](.env.example)。以下为上线必须关注的项：

| 变量 | 作用 | 上线建议 |
| --- | --- | --- |
| `BRAND_*` | 品牌名、副标题、主标语、卡密前缀 | 按品牌规范填写 |
| `PUBLIC_STORE_URL` / `PUBLIC_REDEEM_URL` | 订单页、邮件、二维码回调使用 | 改为真实 HTTPS 域名 |
| `DATABASE_URL` | Postgres/SQLite | 生产用 Postgres |
| `REDIS_URL` | 限流、锁、Celery | 生产用独立 Redis |
| `TASK_MODE` | `celery` / `inline` / `manual` | 生产必须 `celery` |
| `MASTER_AES_KEY` | 卡号/CVV/Session 加密 | 32 字节 base64，妥善备份 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `ADMIN_JWT_SECRET` | 后台登录 | 全部更换 |
| `USER_JWT_SECRET` / `USER_TOKEN_TTL_DAYS` | 客户订单账户令牌 | 独立随机密钥，默认 30 天 |
| `PAYMENT_MODE` | `mock` / `live` | 配好商户密钥后切 `live` |
| `LTZF_*` / `XUNHUPAY_*` | 蓝兔/虎皮椒 | 按签约文档填写 |
| `UPSTREAM_MODE` | `mock` / `http` | 有供应商 API 后切 `http` |
| `SELF_VCC_ENABLED` | 自营通道开关 | 默认 `false` |
| `SELF_VCC_DRY_RUN` | 自营通道演练 | 首次必须 `true`，验证后再关闭 |
| `PROXY_URL` | 美国住宅代理 | 自营通道必需 |
| `US_BIN_ALLOWLIST` | 可选：强制校验卡 BIN 前 6 位 | 建议配置为 RDVCC 美国卡段 |
| `RDVCC_IMAP_*` | 3DS 验证码邮箱 | 自营通道必需 |
| `OPENAI_ACCOUNT_CHECK_ENABLED` | OpenAI 账号状态预检 | 配好美国代理后开启 |
| `SMTP_*` | 发货邮件 | 可选；本系统不依赖邮件，订单通过邮箱+密码登录查看 |
| `SEED_DEMO_DATA` / `SEED_DEMO_INVENTORY` | 演示数据 | 上线必须关闭演示库存 |

---

## 7. 支付通道

### 7.1 当前行为

- `PAYMENT_MODE=mock`：返回指向支付页的演示二维码，支持「模拟扫码支付成功」按钮；
- `PAYMENT_MODE=live`：按微信/支付宝选择调用蓝兔或虎皮椒（当前 `get_provider` 将微信/支付宝映射到蓝兔，`xunhupay` 作为独立 provider 可用）；
- 所有支付请求的商品名都会经过 `masked_order_title()`，从合规名称池生成：
  - `模力补给-数字商品订单-####`
  - `MOLIPLY-软件订阅服务-####`
  - `模力补给-数字权益卡券-####`
- 回调必须通过签名校验、金额一致校验与幂等事件检查，才会触发自动交付；
- 调试接口：`GET /api/v1/store/payment-title-preview?order_no=...`

### 7.2 上线前必须做

1. 向蓝兔/虎皮椒签约并拿到商户号、AppID、密钥；
2. 对照你签约版本的官方文档，核对 `ltzf.py` / `xunhupay.py` 中的**签名参数与成功状态字段**。签名函数已隔离，通常只需改 `_sign()`；
3. 在支付平台配置异步回调地址：
   - `https://shop.moliply.com/api/v1/payments/ltzf/notify`
   - `https://shop.moliply.com/api/v1/payments/xunhupay/notify`
4. 设置 `PAYMENT_MODE=live`，关闭 `MOCK_PAYMENT_ENABLED`；
5. 用最小金额真实下单/退款验证一次，确认回调、发码、金额校验全部正常。

> 商品名脱敏仅用于支付通道展示，**不得用于向用户隐瞒真实收费金额或商品性质**。请自行确认支付平台条款与当地监管要求。

---

### 7.3 加密货币（NOWPayments）

代码已接入 NOWPayments 托管收银台，支持 USDT / USDC / BTC / ETH：

1. 在 NOWPayments 商户后台创建 API Key 与 IPN Secret；
2. 开启上述四个币种；
3. 配置 IPN 回调：`https://moliply.shop/api/v1/payments/nowpayments/ipn`；
4. 在 `.env` 设置：

```dotenv
CRYPTO_ENABLED=true
CRYPTO_PROVIDER=nowpayments
NOWPAYMENTS_API_KEY=...
NOWPAYMENTS_IPN_SECRET=...
CRYPTO_CNY_PER_USD=7.20
CRYPTO_SUPPORTED_COINS=USDT,USDC,BTC,ETH
```

5. 重启 API，前台结算页会自动出现「加密货币支付」；首笔请用最小金额真实支付验证 IPN。
6. 结算金额按 `CRYPTO_CNY_PER_USD` 从人民币折算为美元，请在汇率变化时更新该值。

> 未配置密钥时 `CRYPTO_ENABLED=false`，前台不展示加密货币选项。

### 7.4 飞书管理通知

后台「系统设置 → 飞书管理通知」填写飞书群自定义机器人 Webhook（可选签名 Secret），勾选需要通知的事件即可。支持：

- 新订单、支付成功、自动交付完成、缺货待补
- 新充能任务、充值成功、充值失败、人工复核
- 库存不足预警、支付回调异常、系统任务异常
- 每日 09:00 经营日报

通知失败会记录在设置页的「最近通知」，不会阻断支付或交付。

## 8. 上游代充通道

`app/services/upstream_provider.py` 实现：

- 创建充值订单 → 携带用户 Session、SKU、上游卡密；
- 指数退避轮询（默认 5 秒起，总上限 180 秒）；
- 成功 → 卡密核销；可恢复失败 → 自动释放激活码；超时未确认 → 保持锁定并转人工。

默认 `UPSTREAM_MODE=mock`，用于本地演示。接入真实供应商时：

```dotenv
UPSTREAM_MODE=http
UPSTREAM_API_BASE=https://supplier.example.com
UPSTREAM_API_KEY=...
UPSTREAM_API_SECRET=...
```

然后根据供应商文档调整：

- 创建/查询路径（当前为 `/api/v1/recharge`、`/api/v1/recharge/{order_no}`）；
- 请求字段与签名头（当前为 HMAC-SHA256 + `X-Timestamp` / `X-Signature`）；
- 状态字符串映射。

**不要在没有核对供应商契约的情况下直接切换 `http` 模式。**

---

## 9. 自营 VCC 通道（默认关闭）

启用顺序：

1. 在后台「VCC 卡池」录入 RDVCC 卡片（美国 BIN + 美国账单地址 + 余额 ≥ $25）；
2. 配置美国静态住宅代理 `PROXY_URL`，并验证 `PROXY_HEALTH_URL` 返回 `country=US`；
3. 配置 RDVCC 注册邮箱的 IMAP 参数（`RDVCC_IMAP_*`），确认能收到 3DS 邮件；
4. 保持 `SELF_VCC_ENABLED=true` + `SELF_VCC_DRY_RUN=true`，用测试卡跑一次，确认流程停在提交支付前；
5. 确认无误后设置 `SELF_VCC_DRY_RUN=false`。

安全约束：

- 单卡最多绑定 2 个账号（默认 1）；
- 余额低于 `$22` 不调度；
- 任一拒付立即 `SUSPENDED`，不允许盲目重试；
- 代理国家不是 `US` 时直接中止任务；
- 支付中途异常不会自动释放卡密，任务进入人工复核。

已知限制：

- OpenAI/Stripe 的结账页是外部 UI，`self_vcc_worker.py` 中的选择器与结账创建方式是**尽力而为**实现，可能需要随官方页面更新；
- 没有真实卡与真实账号时无法完整验证实盘路径，因此默认关闭并演练；
- 如供应商/官方提供正式 API，优先替换浏览器自动化，稳定性会高很多。

---

## 10. 安全设计

- **加密**：卡号、CVV、上游卡密、成品号账密、用户 Session 均使用 AES-256-GCM；`MASTER_AES_KEY` 丢失将无法解密历史数据，务必离线备份；
- **凭证生命周期**：任务成功立即清除 Session；失败任务在 `TOKEN_RETENTION_MINUTES`（默认 5）后由 Celery Beat 清除；
- **一码一充**：`SELECT ... FOR UPDATE` + Redis 锁 + 状态机；重复提交幂等返回同一任务；
- **限流**：Nginx 10 次/分钟 + FastAPI slowapi 10 次/分钟；连续 5 次错误卡密封禁 IP 30 分钟；
- **支付安全**：回调验签、金额精确比对、`PaymentEvent` 唯一约束、订单状态幂等；
- **管理安全**：JWT + 限流；后台所有敏感展示均为掩码；
- **审计**：制卡、导入、支付、核销、重试、卡池操作写入 `audit_logs`；
- **网络**：Nginx 安全头、HTTPS（Certbot）、`server_tokens off`；生产建议 Cloudflare/CDN 与 WAF。

### 已知取舍

- 订单中心使用下单邮箱+密码登录，交付内容只对订单账户可见；忘记密码需要后台重置（无 SMTP 自助找回）。
- 没有 Alembic 迁移，当前使用 `Base.metadata.create_all`。长期迭代建议引入 Alembic；
- 独角数卡主题/插件是参考实现，具体落盘路径与网关基类需按你安装的 Dujiaoka 版本映射；
- 自营通道的浏览器自动化需要持续维护。

---

## 11. 运营后台

路径 `/admin/login`。页面：

- **概览**：订单总数、今日收入、待处理、交付成功率、12 天趋势、通道状态、最新订单；
- **订单管理**：筛选/搜索、详情、交付内容、标记已支付、缺货重发；
- **商品管理**：库存（卡密/账密）、上下架、批量导入成品号；
- **卡密管理**：统计、筛选、批量生成、批量导入上游卡密、作废/恢复；
- **充值任务**：状态筛选、错误码、重试（Session 已清除时会拒绝）；
- **VCC 卡池**：录入、掩码展示、恢复并重置失败计数、手动挂起；
- **渠道状态**：上游/自营开关与配置检查清单。

---

## 12. 测试

```bash
cd recharge-gateway-backend
.venv/Scripts/python -m pytest -q
```

覆盖：

- AES-GCM 往返/篡改、邮箱/卡号掩码、Luhn；
- 卡密格式/归一化；
- Session 解析（ChatGPT/Claude/过期/会员/类型不匹配）；
- 订单创建、优惠码、模拟支付、自动交付、幂等结算、订单查询；
- 核销提交、绑定账号、重复提交、成功、临时失败释放、需人工复核保持锁定、暴力破解封禁；
- 支付商品名脱敏、蓝兔/虎皮椒签名与回调结算；
- VCC 选卡约束、拒付挂起、绑定次数耗尽；
- IMAP 3DS 验证码解析（当前邮件/过期邮件/其他发件人/HTML）。

前端：

```bash
cd recharge-gateway-frontend
npx tsc --noEmit
npm run build
```

---

## 13. 上线部署

1. 购买香港/新加坡轻量服务器（2C2G 起步），安装 Docker；
2. 注册域名，添加 A 记录：
   - `shop.moliply.com` → 服务器 IP
   - `up.moliply.com` → 服务器 IP
3. 复制并填写 `.env`，把 `PUBLIC_*` 改为 HTTPS 地址；
4. `docker compose up -d --build`；
5. 申请证书（Certbot），按 `nginx/ssl.example.conf` 配置 443，并保留 80 端口的 ACME 校验；
6. 首次启动后登录后台，导入真实卡密/成品号，关闭演示库存并重建数据库或清理演示数据；
7. 配置支付回调、上游 API、SMTP、监控与数据库备份；
8. 用小额真实订单跑通支付 → 发码 → 核销 → 售后闭环。

备份重点：Postgres 数据 + `.env`（尤其 `MASTER_AES_KEY`）+ Nginx 证书。

---

## 14. 需要你提供 / 决策的事项

代码侧能做的已全部完成；以下必须由你提供真实资源后才能上线：

### 必须（阻塞上线）

- [ ] **服务器与域名**：香港/新加坡服务器、`shop` 与 `up` 两个域名解析；
- [ ] **支付商户**：蓝兔支付或虎皮椒的商户号/AppID/密钥，并核对你签约版本的签名文档；
- [ ] **上游货源**：供应商自动代充 API（地址、密钥、字段契约），或至少一批可导入的上游卡密；
- [ ] **管理员密码与加密密钥**：`ADMIN_PASSWORD`、`ADMIN_JWT_SECRET`、`MASTER_AES_KEY`（生成后离线备份）；
- [ ] **正式商品与库存**：确认售价、质保文案、真实卡密/成品号；
- [ ] **客服渠道**：企业微信/客服微信，填入 `NEXT_PUBLIC_CONTACT_WECHAT`；
- [ ] **合规审查**：支付平台条款、平台类目、当地监管要求，以及 OpenAI/Claude 服务条款风险。

### 自营 VCC 通道（如启用）

- [ ] RDVCC 账户与至少 1 张测试卡（余额 ≥ $25）；
- [ ] 美国静态住宅代理（需验证出口国家为 US）；
- [ ] RDVCC 注册邮箱的 IMAP 账号/授权码；
- [ ] 先用演练模式跑通，再关闭 `SELF_VCC_DRY_RUN`；
- [ ] 根据官方结账页更新选择器（如页面改版）。

### 建议

- [ ] SMTP 发件邮箱（提高交付通知到达率）；
- [ ] 订单查询增加邮箱验证码/用户账号（当前仅订单号或邮箱）；
- [ ] 引入 Alembic 迁移与监控告警（Uptime、Sentry、数据库备份）；
- [ ] 为上游/支付增加沙箱回归测试；
- [ ] 评估是否用上游正式 API 替代 Playwright 自营通道。

---

## 15. 合规声明

本项目是电商/数字商品交付的技术实现，不构成法律、税务或支付合规意见。请特别注意：

- 转售 AI 订阅、代充或成品号可能违反对应服务商的用户协议，存在封号、拒付与资金损失风险；
- 支付商品名脱敏不得用于欺骗消费者或规避监管；应确保收费金额、商品性质与售后政策真实、透明；
- 不得售卖、赠送或提供任何网络代理工具与节点；
- 用户 Session、账号、卡号属于高敏感数据，应遵守个人信息保护相关法规，最小化收集、限期删除、限制访问；
- 上线前请咨询专业人士，确认业务模式、支付通道与税务安排合规。

---

## 16. 文档索引

- [环境变量模板](.env.example)
- [共享服务器隔离部署](deploy/README.md)
- [开业上线清单](deploy/GO-LIVE.md)
- [独角数卡主题与支付插件](storefront-theme-moliply/README.md)
- [后端说明](recharge-gateway-backend/README.md)
- [前端说明](recharge-gateway-frontend/README.md)
