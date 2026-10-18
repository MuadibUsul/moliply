# MOLIPLY 开业上线清单

目标：从“演示站”切换为可接真实客户的站点。

> 当前线上仍是演示支付 + 演示库存。完成本清单中的“必须由站主完成”项后，再运行预检脚本，全部 PASS 才对外收款。

## 一、已经由工程侧完成

- [x] `https://moliply.shop` 部署、HTTPS 证书、Caddy 接入
- [x] 独立 Compose 项目、Postgres/Redis/Celery/Worker/Beat/Web 全部运行
- [x] 商城、商品、支付页、订单、充能中心、帮助中心、运营后台
- [x] 自动发码、充值任务状态机、失败释放、后台重试
- [x] 数据库每日备份脚本与 cron（本地保留 14 天）
- [x] 上线前预检脚本 `preflight.sh`
- [x] 演示数据清理脚本 `purge-demo-data.sh`
- [x] 真实全链路演示（模拟支付/模拟上游）已跑通
- [x] 现有服务器其他站点隔离，未改动现有项目

## 二、必须由站主完成（阻塞收款）

### 1. 支付商户

- [ ] 蓝兔支付或虎皮椒完成个人商户签约
- [ ] 拿到商户号 / AppID / 密钥 / 官方 API 文档
- [ ] 在支付后台配置异步回调：
  - `https://moliply.shop/api/v1/payments/ltzf/notify`
  - `https://moliply.shop/api/v1/payments/xunhupay/notify`
- [ ] 把密钥和文档交给工程侧配置；签名字段可能因版本不同需要核对
- [ ] 完成一笔最小金额真实支付 + 回调验证

### 1.1 加密货币（可选但已接入）

- [ ] 注册 NOWPayments 商户，创建 API Key 与 IPN Secret
- [ ] 开启 USDT / USDC / BTC / ETH
- [ ] 配置 IPN：`https://moliply.shop/api/v1/payments/nowpayments/ipn`
- [ ] 在 `.env` 设置 `CRYPTO_ENABLED=true`、`NOWPAYMENTS_API_KEY`、`NOWPAYMENTS_IPN_SECRET`
- [ ] 更新 `CRYPTO_CNY_PER_USD` 为当前汇率
- [ ] 用最小金额真实支付一次，确认 IPN → 自动交付

### 2. 真实货源

二选一：

**A. 成品号模式（最快）**

- [ ] 准备真实的 ChatGPT Plus / Claude Pro 成品号
- [ ] 在后台「商品管理 → 导入成品号」导入
- [ ] 只保留能真实交付的 SKU，下架其余商品
- [ ] 交付格式：`邮箱----邮箱密码----辅助邮箱----质保到期日`

**B. 代充模式**

- [ ] 供应商真实自动代充 API（地址、密钥、字段契约、签名文档）
- [ ] 或确认可在供应商后台人工核销
- [ ] 用工程侧的上游适配器联调并真实充值一个自有账号
- [ ] 没有真实上游 API 时，**不要**用 `UPSTREAM_MODE=mock` 接客户

### 2.1 系统设置与飞书通知

登录后台 →「系统设置」，填写支付/加密货币/上游/VCC/客服等配置并保存；密钥加密存储，页面不回显。

飞书通知：在群聊添加自定义机器人，复制 Webhook（如有签名校验则复制 Secret），在「飞书管理通知」中粘贴并勾选事件，保存后点「保存并发送测试消息」。测试失败会显示在最近通知中。

### 2.2 自营 VCC 通道（可选，增量能力）

自营通道让订单先走自己的虚拟卡池（菲律宾 ₱999 → 美区 $20），失败或无卡时按策略回退上游 API。需要：

- [ ] 准备可用的虚拟卡台账户与卡片（RDVCC 或其他 US BIN 卡），确认可订阅 ChatGPT Plus
- [ ] 在后台「VCC 卡池 → 录入卡片」填写美国账单地址；如要参与 ₱999，再补菲律宾账单地址
- [ ] 准备 3DS 收码邮箱，在「系统设置 → VCC」填写 IMAP 主机/账号/授权码
- [ ] 准备菲律宾与美国住宅代理，填入「VCC 卡池 → 菲律宾/美国住宅代理」
- [ ] 设置路由策略：混合（推荐）/ 仅自营 / 仅上游；新任务的激活码也可单独指定通道
- [ ] 先用「演练模式」验证：混合策略会跳过自营并回退上游、不扣卡；然后关闭演练，用单卡上限 1 跑小额实盘
- [ ] 真实小额跑通一单自营扣款，确认 3DS 自动取码与续订取消
- [ ] 首批卡片建议单卡上限 1 个账号，确认稳定后再放宽到 2 个
- [ ] 卡池余额低于阈值会自动停止调度；后台「待销卡」提示出现时及时销卡回血
- [ ] Playwright 结账页面可能随 OpenAI 改版失效，需持续维护；失败会转人工并按策略回退上游

> 自营通道仍然依赖 OpenAI 结账页与发卡行，属于技术上可维护但非官方授权的路径；请自行评估账号、资金与合规风险。没有真实卡池和代理时请保持 `SELF_VCC_ENABLED=false`。

### 3. 真实库存与商品

- [ ] 导入真实库存
- [ ] 运行演示数据清理（会先自动备份）：
  ```bash
  MOLIPLY_BASE=/home/deploy/moliply /home/deploy/moliply/purge-demo-data.sh --yes
  ```
- [ ] 后台确认：真实卡密/成品号数量 > 0，演示 `@moliply.test` 数据为 0
- [ ] 确定最终售价、质保期、退款规则
- [ ] 下架暂时无法交付的商品

### 4. 管理员与客服

- [ ] 从服务器读取当前随机密码并登录后台：
  ```bash
  grep '^ADMIN_PASSWORD=' /home/deploy/moliply/.env
  ```
- [ ] 改成你长期掌握的安全密码（编辑 `.env` 后重启 api）
- [ ] 把真实客服微信填入 `NEXT_PUBLIC_CONTACT_WECHAT`，重新构建 web
- [ ] 客户下单时会设置订单密码；后台订单详情可「重置客户密码」，用于客户忘记密码时售后
- [ ] 准备客服话术、售后处理时间和退款流程

### 5. 数据库备份离站

- [ ] 本地每日备份已配置在 `/home/deploy/moliply/backups/`
- [ ] 将备份目录定期复制到你自己的电脑/对象存储（服务器坏了本地备份也会丢）
- [ ] 单独离线保存 `MASTER_AES_KEY`（丢失后卡号/CVV/Session 无法解密）

### 6. 真实上线测试

- [ ] 真实支付最小金额
- [ ] 回调 → 自动交付
- [ ] 用真实账号完成核销/登录
- [ ] 客户邮箱+密码登录「我的订单」，确认只能看到自己的订单
- [ ] 后台记录、退款/售后流程、后台重置客户密码
- [ ] 确认没有模拟支付入口残留

## 三、强烈建议在首单前完成

- [ ] SMTP 发货邮件（可选，不再必需；客户通过邮箱+密码登录查看订单）
- [ ] 外部 uptime 监控：`https://moliply.shop/healthz`
- [ ] `www.moliply.shop`（如需，DNS 加 `CNAME www → moliply.shop`，再让工程侧加 Caddy 站点）
- [ ] 订单密码找回目前依赖后台重置；如需要自助找回，再配置 SMTP 并增加邮件验证码流程
- [ ] 合规审查：支付平台类目、网站文案、OpenAI/Claude 服务条款、退款与消费者权益
- [ ] 小规模试运营，控制初始库存与广告投放

## 四、开关与命令

支付与货源配置在服务器 `.env`：

```dotenv
PAYMENT_MODE=live
MOCK_PAYMENT_ENABLED=false
LTZF_MCH_ID=...
LTZF_APP_ID=...
LTZF_KEY=...
# 或 XUNHUPAY_APP_ID / XUNHUPAY_APP_SECRET

UPSTREAM_MODE=http
UPSTREAM_API_BASE=...
UPSTREAM_API_KEY=...
UPSTREAM_API_SECRET=...
```

修改后：

```bash
cd /home/deploy/moliply
docker compose -f docker-compose.server.yml --env-file .env up -d api worker beat
```

客服微信变更需要重新构建前端：

```bash
docker compose -f docker-compose.server.yml --env-file .env build web
docker compose -f docker-compose.server.yml --env-file .env up -d web
```

预检：

```bash
MOLIPLY_BASE=/home/deploy/moliply /home/deploy/moliply/preflight.sh
```

全部 PASS 后才可以对外推广。

## 五、安全提醒

- 当前 `PAYMENT_MODE=mock` 时，任何访问者都可以点“模拟支付成功”领取演示激活码；**切换真实支付前不要公开推广网站**。
- 不要在客服对话中索要用户密码、短信验证码、银行卡 CVV。
- 退款、质保、封号风险需要在商品页与客服话术中写清楚。
- 代充/成品号转售可能违反对应服务商条款，请自行评估合规与资金风险。
