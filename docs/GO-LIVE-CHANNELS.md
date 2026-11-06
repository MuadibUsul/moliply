# 双通道渐进式实盘切换 SOP

目标：在**不拿客户订单试错**的前提下，把自营 VCC 通道从关闭推到实盘，并随时可退回纯上游。

前提：代码已包含阶段位分流与自动熔断（`app/services/channel_dispatcher.py`、`app/services/fulfillment/self_vcc_circuit.py`）。
三段之间按顺序执行，不跳步。任何一段不通过就停在原地，不要「先上再说」。

> 术语：**上游** = `upstream-service` 或其背后的供应商；**自营** = 本地 VCC 卡池 + Playwright 结账。

---

## 阶段 0：先把钱和货备好（无客户流量）

- [ ] 备份数据库与 `.env`：`deploy/backup-db.sh`，并把备份拷离服务器
- [ ] 用 `python scripts/rotate_secrets.py --without=MASTER_AES_KEY,CCE_MASTER_KEY` 换掉 JWT/Token 类密钥（不动两个 AES key，避免旧密文失效）
- [ ] 上游服务能起、`/healthz` 通，余额充足
- [ ] `SEED_DEMO_INVENTORY=false`，演示卡密与演示账号已清理
- [ ] `preflight.sh` 全项 PASS 或已知 WARN

---

## 阶段 1：纯上游保底（Day 1–3）

验证「收款 → 发码 → 核销 → 交付」闭环，全程不碰浏览器。

1. 后台「VCC 卡池」把全局策略设为 `UPSTREAM_ONLY`
2. `SELF_VCC_ENABLED=false`
3. 支付切真实：`PAYMENT_MODE=live`、`MOCK_PAYMENT_ENABLED=false`，配好官方商户密钥与回调
4. 用最小金额自购 2–3 单，确认：回调验签、金额校验、自动发码、`/orders` 可见、激活码能核销、飞书通知到达
5. 观察 24–48 小时。出现失败先按 `/admin/tasks` 的错误码定位，**不要**在此期间开自营

通过标准：连续 20 单自动交付成功、0 人工复核、无金额/签名异常。

---

## 阶段 2：自营影子演练（Day 4–5，零扣费）

1. 录入 1 张测试卡（余额 ≥ $25），填美国账单地址；若测菲区再加菲律宾地址
2. 配好 `US_RESIDENTIAL_PROXY_URL`（菲区再加 `PH_RESIDENTIAL_PROXY_URL`）与 3DS IMAP
3. 「渠道状态」点「检测环境」，确认出口 IP / 时区 / 指纹一致
4. `SELF_VCC_ENABLED=true`、`SELF_VCC_DRY_RUN=true`
5. 策略切 `HYBRID_SELF_FIRST`
6. 用真实 Session 提交 3–5 单，预期每单都停在「演练模式」并**自动回退上游**成功
7. 检查任务详情：`fallback_reason` 应为 `SELF_VCC_DRY_RUN`，卡片绑定额回到 0

通过标准：上游兜底 100% 成功、无卡片被扣、无 `PAYMENT_OUTCOME_UNKNOWN`。

---

## 阶段 3：单卡灰度实盘（Day 6+）

1. `SELF_VCC_DRY_RUN=false`
2. 卡池只留 1 张卡、`max_bind_count=1`
3. 每单结束后看三处：任务 `executed_channel/executed_region`、卡片 `consecutive_failures`、后台「充值任务」是否有快照链接
4. 前 10 单人工盯盘；出现下列任一情况立即回退 `SELF_VCC_ENABLED=false` 并排查：
   - 出现 `PAYMENT_OUTCOME_UNKNOWN`
   - 「渠道状态」显示熔断打开且原因不是一次性代理抖动
   - 同一张卡 15 分钟内 2 次 `CARD_DECLINED`
5. 稳定 30 单后再补卡、放宽 `max_bind_count`

预期收益：菲区 ₱999 与美区直连的价差落进自己口袋；故障时客户侧无感，因为回退发生在任务内部。

---

## 熔断与回退速查

| 现象 | 含义 | 动作 |
| --- | --- | --- |
| 「渠道状态」熔断打开 | 15 分钟内 3 次环境故障（多半是选择器或代理） | 看最近的失败快照 → 修 → 「手动解除熔断」 |
| 任务 `PAYMENT_OUTCOME_UNKNOWN` | 已点付款但没读到结果 | 到发卡行/OpenAI 核对，再「核对成功/失败」；**不要**直接重试 |
| 卡片 `SUSPENDED` | 连续拒付或硬拒付 | 换卡；原卡销卡回血 |
| 全部任务走上游 | 熔断中 / `UPSTREAM_ONLY` / 自营关闭 | 确认是有意为之 |

## 回滚

任一阶段出问题：

```bash
# 立刻回到纯上游
SELF_VCC_ENABLED=false        # 后台「系统设置 → 履约通道」也可改
# 或直接全局收口
ROUTING_POLICY=UPSTREAM_ONLY  # 后台「VCC 卡池」也可切
```

已锁定的任务不会因为切换策略而改变，仍需人工按 `/admin/tasks` 收尾。
