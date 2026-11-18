# 自动化「谷歌账号」供应链：可行性方案

更新日期：2026-11-07
适用范围：CCE 账号流水线（`recharge-gateway-backend/cce_console/`）的邮箱供给环节
结论一句话：**别去追「自动注册 @gmail.com」，把 Google 账号变成可 API 创建的 Workspace 身份；@gmail.com 只在产品必须要求时才保留。**

---

## 0. 先说结论

| 路线 | 手机号 | 官方 API | 单号成本/月 | 稳定性 | 适用量级 | 建议 |
| --- | --- | --- | --- | --- | --- | --- |
| **S. Workspace 自有域 + 别名** | 不需要 | 有（Directory API 建用户） | ≈ ¥1.7（$7/席位摊 30 个别名） | 高，确定性 | 任意，300 席位 ≈ 9000 地址 | **默认方案** |
| **A. 自建 @gmail.com 注册工厂** | 必需，一号 4~5 个 | 无，只能驱动网页 | 号码摊销 + 代理 ≈ ¥2~6 | 中，看号码池质量 | 每月 ≤200 | 产品必须 Gmail 时用 |
| **B. 采购成品 Gmail 号** | 卖家承担 | 无 | ¥1~5 | 低，转手多次 | 试水/补缺口 | 只做补充 |

三条路不是互斥的，但**不要把同一批账号混来源**：Anthropic 侧的风控是按身份簇聚合的，混源会让「异常簇」变模糊，出事后无法定位是哪一环。

关键判断点只有一个：**你卖的是「Claude 号」，还是「Gmail 号」？**

- 卖 Claude 号、客户只要能用 → 走 S，邮箱留在自己手里（`今年 188 元的 Pro 成品号，加一个月 $7 的席位就吃掉全部毛利`）。
- 客户明确要 Gmail 地址、要能自己找回 → 走 A 并准备号码池；或 S 的「独立席位」加价变体。

---

## 1. 事实基线（先破几个误区）

### 1.1 没有官方的个人 Gmail 建号 API

- 个人 `@gmail.com` 至今**没有**任何官方创建接口。市面上所有「Gmail 批量注册 API」都是网页自动化或设备农场的封装。
- 唯一官方可编程建号的是 **Google Workspace Directory API**：`POST /admin/directory/v1/users`，必须有 `password`（8~100 ASCII），超配额返回 503 要指数退避。见 [Manage users](https://developers.google.com/workspace/admin/directory/v1/guides/manage-users)。
- 因此「稳定」不等于「脚本写得好」，而是**换一条 Google 官方兜底的通道**。

### 1.2 官方说手机号可选，实测经常强制

[Create a Google Account](https://support.google.com/accounts/answer/27441) 把电话写成 "Optional: Add and verify a phone number"。实际批量注册时，一遇到"确认是你本人"就停在这一步，`signup.py` 里 `HUMAN_MARKERS` 含 `phone`/`sms`、命中即 `needs_human` 就是因为这个。

社区经验（**非官方数字，只作规划用**）：一个号码大约能验 4~5 个账号，之后报 "This phone number has been used too many times"，冷却以月计。所以 A 路线的真实产能上限 = 号码数 × 5 ÷ 冷却期，不是脚本速度。

### 1.3 Workspace 侧的关键限制（都会影响架构）

| 事实 | 影响 | 来源 |
| --- | --- | --- |
| Business Starter $7/人/月，上限 300 人 | 席位是主要成本项 | [Pricing](https://workspace.google.com/pricing.html) |
| **暂停用户照常计费**，只有删除才释放席位 | 不能靠暂停省钱 | [Suspend a user](https://knowledge.workspace.google.com/admin/users/suspend-a-user-temporarily) |
| 每个用户 **30 个免费别名** | 一个席位可承载 30 个 Claude 号 | [Email alias](https://knowledge.workspace.google.com/admin/users/add-or-delete-an-alternate-email-address-email-alias) |
| 次级域的用户仍按人头计费，域本身不另收费 | 加域不等于加容量 | [Multiple domains](https://knowledge.workspace.google.com/admin/domains/limits-with-multiple-domains) |
| Cloud Identity Free 上限 50 人、**不含 Gmail** | 可做免费身份池，但要另配收信 | [Editions](https://docs.cloud.google.com/identity/docs/editions) |
| 应用专用密码仍在，但需先开两步验证、改密码即吊销 | 沿用现有 IMAP 可行但脆 | [App passwords](https://support.google.com/accounts/answer/185833) |

### 1.4 应用专用密码不是唯一读信方式，而且正在退场

Workspace 可用**服务账号 + 全域委派**，以 `gmail.readonly` 冒充任意用户读信（管理员在 API Controls 里授权 Client ID，生效最长 24 小时）。见 [Domain-wide delegation](https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority)。

这比「每个账号发一个 16 位密码」强得多：没有逐号密钥、不受改密吊销影响、不依赖 2FA 页面。Gmail API 配额 6000 单位/分/用户，`messages.list` 只要 5 单位，够用。

### 1.5 Claude 不支持换绑邮箱

官方原文："It's not possible to change the email address associated with your Claude account at this time."（[帮助中心](https://support.claude.com/en/articles/8452276)）

推论：**邮箱是永久绑定物**。可以一个席位挂 30 个别名，但已用掉的别名不能再给别人，席位在别名全部退役前都不能删。

### 1.6 现状盘点：CCE 已经完成 80%

已有的、直接可复用的部分：

- `core/profile_manager.py` 按 `profile_id` 确定性生成指纹，一次生成终身复用；
- `core/proxy_inspector.py` 建号前校验出口 IP 与登记值一致，不一致直接熔断；
- `core/signup.py` 已是「身份先于账号存在」的写法，异常一律返回 reason 而不是抛穿；
- `core/mail_reader.py` 的 `IMAP_HOST` 写死 `imap.gmail.com`，Workspace 账号**同一主机**，几乎不用改。

要动的只有：邮箱从哪来、以及让读信支持「按收件人过滤」和「搜 All Mail」——见 §4。

---

## 2. 推荐架构：S 路线（Workspace + 别名 + Gmail API）

### 2.1 形态

```
域名（一次性）── MX/SPF/DKIM/DMARC
   │
Workspace Business Starter，1 个席位起步（每席位 30 个别名）
   │  Directory API 按需建用户 / 建别名，无需手机
   ▼
claude-0001@domain … claude-0030@domain       ← 一个 Claude 号一个别名，永久占用
   │
Claude 注册发码 ──► 同一席位邮箱
   │
服务账号全域委派（gmail.readonly）──► 按 to: 精确取码，不落地任何逐号密钥
```

### 2.2 为什么这样最稳

- **不需要手机号**：`Directory API` 建用户不经过任何验证码环节，这是全方案唯一真正消灭瓶颈的地方。
- **没有验证码**：Workspace 建号是 API 调用，不是网页点击，不存在 hCaptcha / Cloudflare。
- **没有逐号密钥**：服务账号一处委派，新增账号零配置。
- **地址可信度高于新 Gmail**：自有域邮箱从第一天起就是「域名邮件」，不是刚注册的空壳 Gmail。
- **成本可摊薄**：30 别名/席位 → ¥1.7/号/月，占 188 元售价不到 1%。
- **与现有流水线同构**：仍是「导入地址 → 自动注册 → 自动开 Pro」，只换邮箱来源。

### 2.3 一次性搭建

1. 注册一个只用于此业务的域名（不与主站混），**先放 7~14 天再量产**，让域名有年龄。
2. 开通 Workspace Business Starter（弹性月付），完成域所有权验证。
3. 配齐 SPF、DKIM、DMARC。收信也要配：否则 Anthropic 验证码会被判垃圾，整条链断在收件箱。
4. Admin console → Security → API Controls → Domain wide delegation，授权服务账号 Client ID，scope 只给：
   `https://www.googleapis.com/auth/gmail.readonly`
   另建一个只做建号的委派身份，scope 只给 `.../auth/admin.directory.user`。两个服务账号分开，最小权限。
5. 关闭 API 建号的「首次登录强制改密」（`changePasswordAtNextLogin=false`），否则每个号都要多走一屏。
6. 每个席位预留 `claude-<n>` 别名命名空间，别用随机串——排障时肉眼可读。

### 2.4 席位与别名的回收

- 别名一旦被某个 Claude 号占用，**直到该号退役都不可回收**；Claude 的改密/魔法链接都要它。
- 席位计数表：`used_aliases / 30`，到 25 就新建席位，别等打满。
- 删除席位前确认其上别名全部退役——删用户会连带删别名且席位退款按天折算。

### 2.5 成本对照（按现有 188 元售价，汇率 7.2）

| 项 | 金额/号/月 |
| --- | --- |
| Claude Pro 采购 | ≈ ¥144（$20） |
| Workspace 摊销（30 别名/席位） | ≈ ¥1.7 |
| 代理 + 卡费 + 质保 | 余下约 ¥40 内 |
| **合计** | ≈ ¥146 |

若「一席一号」直接交付：Workspace 变 ¥50/号，加 Claude ¥144 = ¥194 **超过 188 售价**。所以默认必须是共享席位模式（Mode A）；要交付独立邮箱就得出「独立邮箱」加价变体（建议 ≥ ¥248，或明确写不含邮箱所有权）。

---

## 3. A 路线：真要 @gmail.com 时的完整规程

只在「产品必须 Gmail 地址」时启用。这条路没有官方 API，全部靠伪装真实度，所以每一步都按「一个新的人、一台新机器」来配。

### 3.1 前置资产

- **号码池**：实体 SIM 优先于接码平台。平台号会被回收再售，三个月后连找回都做不到。一号上限按 4 个规划，用完进冷却（≥90 天），池表记 `verify_count`、`last_used_at`、`cooldown_until`。
- **代理**：必须是住宅/ISP，机房 IP 在 Google 这边比在 Anthropic 那边更早死。一号一 IP，不共用。
- **身份素材**：姓名、生日、性别从固定语料按 profile 确定性派生，避免同批次重名。

### 3.2 驱动顺序（`core/google_signup.py`）

入口 `https://accounts.google.com/signup/v2/createaccount?flowName=GlifWebSignIn&flowEntry=SignUp`。

1. 姓名、用户名、密码、生日、性别。
2. 用户名为 `名 + 姓 + 4~6 位数字`，先探测可用性再提交，被占用换候选而不是重跑整页。
3. 手机步骤：命中即从号码池取号。SMS 自动读取（Android 短信网关或接码平台回调），填码后**立刻把该号标 `used`**，无论后续成败。
4. 若出现「跳过」，一律跳过；后续再补 2FA。
5. 创建完成后立刻做**养号动作**：YouTube 看一段、搜索一次、进一次 Gmail 收件箱，制造真人痕迹。
6. 开两步验证：进 `myaccount.google.com/security`，选 Authenticator，**截图二维码并解出 `otpauth://` 里的 secret 存库**（后续换设备、重登都要它）。
7. 生成应用专用密码：`myaccount.google.com/apppasswords`，名称填 `cce`，抓取对话框里的 16 位。存库加密，与 `profile_id` 绑定。
8. 交给既有 `signup.py` 继续跑 Claude。

### 3.3 硬性纪律

- 同一出口 IP 每小时 ≤ 2 个，工作日白天做，夜里批量是重灾区。
- **新号 72 小时内不算成功**：批量停用多发生在建号后 1~3 天。入库先标 `quarantine`，满 7 天可用才转 `交易可用`。
- 同一指纹不跨号复用；`profile_manager` 现有确定性生成正好合适。
- 不用 VoIP/Google Voice 号做验证——被拒率高。
- 注册全程一个页面不刷新重进，中途刷新最容易触发「确认是你本人」。

### 3.4 现实成功率

配置到位时首轮 70~85%，一周存活 90% 左右（经验值，首批必须自测）。瓶颈永远是号码，不是脚本。

---

## 4. 对 CCE 的改造点（S 路线，最小改动）

只列必要项，按顺序做：

1. `core/mail_reader.py`
   - `IMAP_HOST` 从常量改为配置项（Workspace 仍可走 IMAP 兜底）。
   - `fetch_latest_code` 的 `client.select("INBOX")` 改为 `'"[Gmail]/All Mail"'`，否则转发/判定为垃圾的验证码取不到。
   - 新增 `recipient` 参数并在 `extract_code` 里比对 `To`/`Delivered-To`——**共享席位下这是必须的**，否则同一席位的 30 个号会互相抢码。
2. `core/pipeline.py`
   - `ImportRow` 增加 `alias`/`mailbox` 两字段：登录读信的账号与收件地址分离。
   - 新增 `google_signup.py` 阶段调用点，`needs_human` 的手机分支改为先查号码池。
3. `core/gmail_api.py`（新增）
   - 服务账号 JWT → impersonate 席位用户 → `users().messages().list(q="from:anthropic to:<alias> newer_than:1d")`。
   - 取 `raw` 复用现有 `extract_code`，选择器与 IMAP 共用一套。
4. `db/schema.sql`
   - `mail_seats(seat_email, used_aliases, status)`、`phone_pool(number, verify_count, cooldown_until, status)`、`mailboxes(address, seat_email, status)`。
5. `api/app.py` + `console.html`
   - 导入格式增加别名列；状态栏加「席位余量 / 号码冷却数」两格。

`signup.py` 的 Claude 流程本身**不用改**：它只认「email + 取码器」。这是当初写成注入式的好处。

---

## 5. 试点与放量

### 5.1 试点（10 个号，一周）

| 指标 | 门槛 |
| --- | --- |
| 建号成功（含自动过验证码） | ≥ 80% |
| 验证码到达 ≤ 60 s（含垃圾箱） | ≥ 90% |
| 7 天存活 | ≥ 90% |
| Claude 注册成功 | ≥ 70% |
| 开 Pro 成功 | 记录即可，先不设门槛 |

任一项不达标就停在该环节层修，不带着缺口放量。

### 5.2 放量节奏

10 → 30 → 100，每档跑满一周看存活曲线再进下一档。A 路线的产能受号码池约束，S 路线受席位约束，两者都不该一次拉满。

### 5.3 与 Claude 侧的耦合

`CCE_WORKER_INTERVAL` 现有 20 s 串行，对 Google 侧是安全的；S 路线建号完全在 Google API 侧，与 Claude 的节奏解耦，可以预先屯一批 `quarantine` 地址，跑 Claude 时只挑满 7 天的。

---

## 6. 风险登记册

| 风险 | 触发信号 | 处置 |
| --- | --- | --- |
| Workspace 扣款失败 → 整域停服 | 计费邮件、席位变灰 | 绑两张卡，余额告警阈值设高；这是 S 路线唯一的单点 |
| 域名被误判（新域、滥用投诉） | 退信率上升、验证码进垃圾 | 同业务专用域，预热期不量产，DMARC 策略先 `none` 再收紧 |
| Claude 侧识别出「同域批量」 | 同批注册集中失败 | 席位分散到 2~3 个域名，或掺 20% A 路线账号稀释 |
| 号码被回收（A 路线） | 二次登录要求验手机 | 建号后 7 天*内*补绑辅助邮箱，降低对号码的依赖 |
| 新号批量停用（A 路线） | 24~72 h 集中 `disabled` | 只把满 7 天的号投入 Claude；停用率 > 30% 换号码供应商 |
| 委派密钥泄露 | — | 服务账号密钥只挂读信 + 建号两个最小 scope，密钥不落地镜像 |

---

## 7. 明确不做的事

- **不把第三方「Gmail API」当官方接口**：它们是网页自动化的转售，故障边界不在你手里。
- **不用机房 IP 注册 Google**：比注册 Claude 死得更早。
- **不一个号跑到底**：一号 4~5 个是硬墙，撞墙后整批卡在验证页。
- **不开 2FA 就以为能出密码**：应用专用密码的前置条件就是 2FA。
- **不混来源做同一批**：出事后无法归因。
- **不把 Workspace 席位当无限容量**：30 别名是硬顶，且别名不可回收。

---

## 8. 备选：完全脱离 Google

Claude 接受任何邮箱。若某天客户对 Gmail 不再敏感：自有域 + Cloudflare Email Routing 全捕获 → 一个收件箱，`mail_reader` 加收件人过滤即可，成本降到一个域名钱。这条路的地址不是 Google 账号（不能 Google 登录、没有 Gmail 界面），但 Claude 侧行为完全等价。适合作为 S 路线的降级形态，也是 Google 域被封时的应急通道。
