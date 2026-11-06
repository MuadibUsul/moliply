# CCE 子系统规格摘要

MOLIPLY 的 `/cce` 子系统把「住宅 IP + Gmail 邮箱」变成可交付的 Claude 账号。
完整实现见 [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §10 与 [`cce_console/README.md`](cce_console/README.md)。

## 不变量

1. **身份先于账号**：出口 IP、Camoufox 指纹、时区、语言在 `ProfileManager.create_profile()` 时一次定死，之后不重绘。
2. **出口每次复核**：`BrowserPool.acquire_context()` 先 `ProxyInspector.verify_and_inspect()`，出口 IP 与登记值不符即置 `PROXY_DEAD` 并中止。
3. **状态是许可**：迁移由 `core/lifecycle.py:ALLOWED` 白名单约束，越权迁移抛 `IllegalTransition`；只有人工接口可以 `force=True`。
4. **租约必须可回收**：`BUSY` 靠 `last_heartbeat_at` 续租，120 秒无心跳由 `sweep_reaperable()` 收回，防止 `kill -9` 永久占号。
5. **卡只在注册成功后租用**：注册失败不消耗卡；硬拒付直接 `dead`，软异常 `release_card()`。
6. **凭证密文绑定账号**：AES-GCM 的 AAD 是 `profile_id`，密文跨账号重放会解密失败。
7. **交付双因子**：`card_key` + `tunnel_secret` 缺一不可；`secret_export` 只发布 `IDLE/BUSY` 且未过期的 key，发布失败时删文件让网关 fail closed。
8. **人工闸口可恢复**：`needs_human` 把 job 停在原地、profile 回 `IDLE`；改代理/改邮箱接口会自动重排，不丢上下文。

## 阶段机

```text
queued → registering → registered → subscribing → subscribed → ready
              ↓              ↓            ↓
         retryable(≤3) → needs_human（停给人）
```

`MAX_AUTO_ATTEMPTS = 3`（`core/pipeline.py`）。

## 边界

- 不做短信读取，手机验证一律 `needs_human`。
- `plan_tier` 只是标签，不驱动实际档位。
- 网关域名白名单硬编码在 Go 二进制，加域名要重编译。
- 客户端启动器与网关二进制不在仓库，需单独发布到 `CCE_CLIENT_DIR` 与 `bin/`。
