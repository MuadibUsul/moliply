# CCE · Claude 账号托管台

`/cce` 是 MOLIPLY 后台里的一个独立子系统，用来把「住宅 IP + Gmail 邮箱」变成可交付给客户的 Claude 账号：注册 → 开 Pro → 导出登录凭证 → 签发客户端交付 key。客户拿到 key 后用 `claude-box` 客户端经 TLS 网关连上这个账号专属的固定出口。

它和商城（Next.js / FastAPI 主站）只有两处耦合：由主站后台的侧栏 `/cce/ui` 进入，并复用主站的后台登录态（`moliply_admin` cookie / `Authorization: Bearer <JWT>`）。数据、浏览器、网关、数据库都是它自己的。

---

## 1. 组成

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| 控制面 API + 控制台 | `cce_console/api/app.py`、`api/console.html` | FastAPI；页面是单文件原生 HTML，无构建步骤 |
| 账号流水线 | `core/pipeline.py` | 导入、阶段机、重试、串行调度 |
| 注册驱动 | `core/signup.py` | 走 claude.ai 注册（含 Cloudflare / hCaptcha 处理） |
| 订阅驱动 | `core/subscribe.py` | 在 Stripe 结账页填卡开通 Pro（跨 iframe 填字段） |
| 邮箱取码 | `core/mail_reader.py` | Gmail IMAP，仅收 Anthropic 发件人、仅收新邮件 |
| 充值卡池 | `core/card_pool.py` | 卡号/CVV 用 `CCE_MASTER_KEY` 加密，状态机 available/in_use/used/dead |
| 指纹环境 | `core/profile_manager.py`、`core/browser_pool.py` | Camoufox 指纹按 profile_id 确定性生成、磁盘上限 10 MB、缓存擦除 |
| 出口校验（熔断） | `core/proxy_inspector.py` | 启动前验证代理可用且出口 IP 与登记值一致，不一致直接中止 |
| 端口映射 | `core/singbox_manager.py` | 一条 profile 一个本地端口，转发到该 profile 的上游住宅代理 |
| 凭证封存 | `core/crypto.py` | AES-GCM，关联数据绑定 profile_id，密文不可跨号重放 |
| TLS 网关 | 独立 Go 二进制 `gateway-linux-x64` | 认证式 CONNECT + 域名白名单，按 key 的 profile 绑定端口 |
| 客户端 | `claude-box` / `claude-box-windows-x64.exe` | 客户侧启动器，`cli` 与 `desktop` 两种模式 |

---

## 2. 目录与运行路径（线上）

```text
/home/deploy/cce/
├── cce.env                     # 600，全部运行参数（见 §6）
├── server/                     # 控制面源码，systemd 工作目录
│   ├── api/{app.py,console.html}
│   ├── core/{pipeline,signup,subscribe,mail_reader,card_pool,
│   │         browser_pool,profile_manager,proxy_inspector,
│   │         singbox_manager,crypto,lifecycle,login,secret_export}.py
│   ├── db/{database.py,schema.sql}
│   └── data/
│       ├── cce.sqlite3         # 主库：profiles / account_jobs / cards / keys
│       ├── profiles/<id>/      # 每个账号的 browser_data + claude_home
│       ├── singbox/config.json # 生成的端口映射
│       ├── clients/            # 对客户发布的启动器与 CA
│       └── cce.secrets         # 网关读取的 key→端口授权快照
├── bin/{gateway-linux-x64,sing-box}
└── venv/
```

进程：

| systemd **user** 单元 | 监听 | 说明 |
| --- | --- | --- |
| `cce-api.service` | `0.0.0.0:8010` | 控制面。Caddy 经 `172.18.0.1:8010` 反代 |
| `cce-gateway.service` | `:8444` | 客户流量入口，UFW 已有 `cce-gateway` 规则 |

Caddy 片段（在 `moliply.shop` 站点块内，注意是 `handle_path`）：

```caddyfile
redir /cce /cce/ 302
handle_path /cce/* {
    request_body { max_size 64MB }
    reverse_proxy 172.18.0.1:8010
}
```

`handle_path` 会剥掉 `/cce` 前缀；应用侧用 `CCE_PUBLIC_PREFIX=/cce` 把它注入页面（`window.CCE_BASE`）和交付命令，所以两处必须成对存在，不能只改其一。

---

## 3. 控制台界面

单屏工作台，没有页签跳转：

- **顶部状态栏**：系统就绪、调度引擎（是否在跑、跑谁、排队几条）、充值卡余量。点后两格开抽屉补卡或手动推进。
- **漏斗过滤**：全部 / 待人工失败 / 队列与执行中 / 缺凭证 / 就绪，带实时计数。
- **账号卡**：一张卡一个账号，按阶段给不同的修复按钮（详见 §4.1）。
- **抽屉**：批量导入（实时表格预览 + 逐行校验）、补卡、客户端包、交付指令。

账号卡阶段胶囊：`排队中 / 注册中 / 已注册 / 开 Pro 中 / 已开通 Pro / 待人工 / 流程失败 / 就绪 / 缺凭证`。

---

## 4. 流水线

```
queued → registering → registered → subscribing → subscribed → ready
              ↓              ↓            ↓
         待自动重试（≤3 次）→ needs_human（停给人）
```

规则：

- **无人值守**：`_worker_loop` 每 `CCE_WORKER_INTERVAL`（默认 20 s，`0` 关闭）取一条跑，一次一个浏览器；导入接口会立刻踢一次，不等周期。
- **只租卡于注册成功之后**：注册失败不消耗卡。
- **卡心分离**：卡停在 `needs_human` 不算失败，`PATCH /admin/jobs/{profile_id}/proxy` 改代理会自动复位 profile 到 `IDLE` 并重排。
- **可自愈才重试**：Cloudflare 拦截、页面半渲染、取码超时、代理抖动 → 重排；Gmail 应用密码错、IP 漂移 → 直接停。
- 重试上限 `MAX_AUTO_ATTEMPTS = 3`（`core/pipeline.py`）。

### 4.1 卡片上的修复动线

`needs_human`（以及无 job 行的老账号处于 `PROXY_DEAD`）会按原因直接展开修复框，不用切页：

| 原因 | 卡片给的动作 |
| --- | --- |
| 代理不通 / IP 漂移 | 预填当前代理与出口 IP 的输入框 → `PATCH /admin/jobs/{profile_id}/proxy`（无 job 行走 `PATCH /admin/profiles/{profile_id}/proxy`），保存即复位 `IDLE` 并重排 |
| 邮箱验证码收不到 | 填应用专用密码 → `GET /admin/mail/check` 当场验，再 `PATCH /admin/jobs/{profile_id}/mail` 保存重排 |
| 手机验证 / 人机拦截 | 「检测环境」(`stealth-check`) + 「我已手动处理，标为就绪」(`/skip`) |

`needs_human` 的含义是「只有人能解决」：短信/手机校验、需要人工过的人机校验、卡缺失、密码错、代理彻底不通。

---

## 5. HTTP 接口

管理面全部需要 `x-admin-token: <CCE_ADMIN_TOKEN>`，或主站后台 cookie / Bearer JWT。启动器面用 `Authorization: Bearer <card_key>` + `x-tunnel-secret`。

### 5.1 运维 / 健康

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/ui` | 控制台页面 |
| GET | `/` | HTML 索取时返回控制台，否则返回索引 JSON |
| GET | `/healthz` | 存活 |
| GET | `/readyz` | 就绪：缺密钥/缺 sing-box/未运行都会 503 并列出原因 |
| GET | `/cce-docs` | OpenAPI 文档（FastAPI `docs_url`，非装饰器路由） |

### 5.2 账号流水线

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/admin/jobs` | 任务列表（无密码、令牌本体） |
| POST | `/admin/jobs/import` | 批量导入：`{text, proxy_template}`，每行 `邮箱,应用密码,IP[,代理[,套餐[,密码]]]` |
| POST | `/admin/jobs/run` | 手动跑队列：`{limit, concurrency}` |
| POST | `/admin/jobs/{profile_id}/run` | 推进单条 |
| PATCH | `/admin/jobs/{profile_id}/proxy` | 改代理并复位：`{upstream_proxy_url, expected_ip}` |
| POST | `/admin/jobs/{profile_id}/skip` | 人工已处理，直接标 `ready` |
| GET | `/admin/mail/check` | 校验 Gmail 应用密码：`?email=&app_password=` |
| GET | `/admin/mail/app-password-hint` | 邮箱配置提示（固定文案） |

### 5.3 充值卡

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/admin/cards` | 列表 + 计数摘要（返回 BIN 与后四位，不含全卡号） |
| POST | `/admin/cards` | 批量导入：`{items:[{pan, exp_month, exp_year, cvv, billing_zip}]}` |

### 5.4 环境 / profile

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/admin/profiles` | 新建（`profile_id`、代理、出口 IP、`os_type`、端口） |
| GET | `/admin/profiles` | 列表，已带 `job_stage` / `email` |
| GET | `/admin/profiles/{profile_id}` | 单条 |
| POST | `/admin/profiles/{profile_id}/preflight` | 校验代理并记录地理 |
| POST | `/admin/profiles/{profile_id}/stealth-check` | 真浏览器开 ipinfo 比对指纹/时区/IP |
| POST | `/admin/profiles/{profile_id}/sync-credentials` | `claude auth login` 走完 OAuth 并封存凭证 |
| POST | `/admin/profiles/{profile_id}/import-credentials` | 导入现成 `.claude` 目录 |
| POST | `/admin/profiles/{profile_id}/status` | 置状态 |
| POST | `/admin/profiles/{profile_id}/release` | 释放租约 |

### 5.5 交付

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/admin/keys` | 签发：`{profile_id, plan_tier, valid_days}` |
| GET | `/admin/keys/{profile_id}` | 列出该账号的 key |
| DELETE | `/admin/keys/{card_key}` | 吊销 |
| GET | `/admin/bundle/{profile_id}?os_name=` | 打 zip（mint 新 key + CA + 客户端） |
| GET | `/admin/client`、`/admin/client/{name}` | 已发布文件列表 / 下载 |

### 5.6 启动器（客户端调用）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/launcher/session` | 领取会话：指纹、地理、端口、隧道 |
| GET | `/launcher/credentials` | 取凭证 tar.gz |
| GET | `/launcher/credentials/b64` | 同上，base64 |
| POST | `/launcher/heartbeat` | 续租；`x-observed-ip` 与登记值不符即判 `PROXY_DEAD` |
| POST | `/launcher/status` | 上报结束状态：`IDLE/COOLDOWN/AUTH_EXPIRED/PROXY_DEAD` |

### 5.7 运维杂项

`POST /admin/batches`（批次）、`GET|POST /admin/singbox[/reload]`、`POST /admin/tunnel-secrets`（重发密钥快照）。

---

## 6. 环境变量（`/home/deploy/cce/cce.env`，600）

| 键 | 说明 |
| --- | --- |
| `CCE_ADMIN_TOKEN` | 64 位十六进制；脚本调用用 `x-admin-token` |
| `CCE_MASTER_KEY` | base64 32 字节；加密卡号/CVV/凭证。**丢失即全废** |
| `CCE_TUNNEL_HOST` / `PORT` / `SAN` / `CERT` / `KEY` | 网关对外地址与 TLS 材料 |
| `CCE_TUNNEL_SECRETS` | 网关读取的授权快照路径 |
| `CCE_DB_PATH` / `CCE_CLIENT_DIR` | 数据库与客户文件目录 |
| `CCE_PUBLIC_PREFIX` | 固定 `/cce`，与 Caddy `handle_path` 成对 |
| `CCE_WORKER_INTERVAL` | 自动调度间隔秒，`0` 关闭 |
| `CCE_REAPER_INTERVAL` | 回收过期租约/冷却的扫描间隔 |
| `MOLIPLY_ADMIN_JWT_SECRET` | 与主站 `ADMIN_JWT_SECRET` 同值，用于免二次登录 |

---

## 7. 数据模型（`db/schema.sql`）

- `payment_batches` — 风控隔离单位，一批一个 BIN，批间不混。
- `profiles` — 一条 = 一个可售身份。指纹/出口在创建时定死。`status` 六态：`IDLE/BUSY/COOLDOWN/AUTH_EXPIRED/PROXY_DEAD/BANNED`。
- `delivery_keys` — 客户凭据（`card_key` + `tunnel_secret` 双因子），带到期时间。
- `subscription_cards` — 加密卡，`available/in_use/used/dead`。
- `account_jobs` — 流水线阶段、尝试次数、错误与人工备注。

状态迁移由 `core/lifecycle.py` 的白名单约束，非法迁移是空操作而不是覆盖。

---

## 8. 日常操作

```bash
# 状态
systemctl --user is-active cce-api cce-gateway
curl -s localhost:8010/readyz

# 重启（改代码后必须）
systemctl --user restart cce-api.service

# 看日志
journalctl --user -u cce-api.service -n 100 --no-pager

# 校验 Caddy 片段（改后）
sudo docker exec caddy-caddy-1 caddy validate --config /etc/caddy/Caddyfile
sudo docker exec caddy-caddy-1 caddy reload  --config /etc/caddy/Caddyfile
```

改代码的固定顺序：本地改 → `scp` 到 `/tmp` → `python3 -c "import ast; ast.parse(...)"` 语法门 → 备份同名文件 → 覆盖 → 重启 → 打 `/ui` 与 `/readyz`。控制台是原生 JS，改完必须用 `new vm.Script(<script>)` 验语法，它一旦语法错就静默全哑。

---

## 9. 前置依赖与已知缺口

宿主必须先满足（换机时逐条装）：

- `claude` CLI **必须是不依赖 Bun 的纯 JS 版**，当前锁 `@anthropic-ai/claude-code@2.1.100`，`/usr/local/bin/claude` 指向其 `cli.js`。本机是 QEMU CPU 且无 SSE4.2，Bun 版会直接 `error: this CPU is missing SSE4.2`（旧版留档 `claude-code.bak-bun`）。
- Camoufox 依赖的系统库：`libgtk-3-0 libasound2t64 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libxkbcommon0 libpango-1.0-0 libcairo2 libdbus-glib-1-2 libxt6`。缺 `libgtk-3.so.0` 时浏览器起不来。
- `sing-box`、`gateway-linux-x64` 二进制在 `bin/`，`clients/` 里有已发布的启动器与 CA。
- Gmail 必须开两步验证并生成 **应用专用密码**（普通密码不能用于 IMAP）。

明确还没做到的：

- 没有短信读取，手机验证一律停 `needs_human`。
- `plan_tier` 目前只存标签，不驱动实际档位。
- 没有养号、没有按账号轮换卡、没有实测成功率；退出码/限额只有启动器能看见，靠它上报。
- 网关域名白名单硬编码在 Go 里（`*.anthropic.com`、`*.claude.ai`、`claude.com`、`platform.claude.com`、`*.stripe.com`、`*.cloudflare.com`、`ipinfo.io`、`ip-api.com`），加新域名要重新编译。
- `data/` 与 `cce.env` 需自行备份，含 `CCE_MASTER_KEY`；备份目前不加密。

---

## 10. 风控要点

自动化本身不触发判定，**身份与资金**才触发。这套的强项是：出口 IP 先于账号存在并每次复核、指纹一次生成终身复用、语言时区随出口推导、注册与付费同环境同 IP。

真实风险排序：**卡 BIN 复用**（故有 `payment_batches` 一批一 BIN）> **IP 质量**（机房 IP 必挂，须真住宅/ISP）> hCaptcha / 手机验证 > Gmail 账号年龄 > 突发节奏。当前串行 + 20 s 间隔既是内存所限（宿主仅余约 1.5 G），也顺带降低了突发感。

---

## 11. 故障速查

| 现象 | 先看 |
| --- | --- |
| 页面空白、按钮全无反应 | 控制台 JS 语法（`new vm.Script`），而不是网络 |
| `/cce/ui` 404 | Caddy 用了 `handle` 而非 `handle_path`，或缺 `CCE_PUBLIC_PREFIX` |
| 403/401 但已登录后台 | 进程没读到 `MOLIPLY_ADMIN_JWT_SECRET`，或跨源缺 cookie |
| 建任务 500，日志 `PermissionError ... fpgen/data` | 进程用户无权写 `site-packages/fpgen/data` |
| `CamoufoxNotInstalled` | 缺 `libgtk-3.so.0` 等系统库，或浏览器未 fetch |
| `claude: missing SSE4.2` | 装成 Bun 版了，换纯 JS 版 |
| 任务全停「代理不可用」 | 上游代理死；`PATCH /admin/jobs/{profile_id}/proxy` 改对后会自动重排 |
| `profile is AUTH_EXPIRED` 抢不到 | 页面残留态；`PATCH .../proxy` 会复位到 `IDLE` |
