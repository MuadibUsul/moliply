# 自建上游渠道完整教程

适用：MOLIPLY 自己当"货源方"，把上游代充从"买别人 API"变成"可控、可测、可换"。

> 先看第 0 节决定走哪条路。两条路的成本差一个数量级，别跳读。

---

## 0. 先搞清楚"上游渠道"到底是什么

MOLIPLY 有两层，别混：

| 层 | 是什么 | 谁负责 |
| --- | --- | --- |
| **收款层** | 客户在商城付钱（支付宝当面付 / 微信 V3） | 你已经做完，官方直连 |
| **履约层** | 钱收到后，把会员充到客户账号上 | 这层才是"上游" |

**"上游"不是必须买别人的 API。** 代码把履约抽象成"给一个 Session、一个 SKU，返回成功/失败"，渠道可插拔：

- **路线 A（自持卡池）**：用自己的 VCC 卡 + Playwright 直接去 OpenAI/Stripe 结账。`app/services/fulfillment/`，已实现。成本≈卡费，可控性最高，但要卡、要住宅代理、要 3DS IMAP。
- **路线 B（自建上游 API）**：自己写一个服务，替 MOLIPLY 完成"充会员"，MOLIPLY 只调它。**这是本教程的主题**，适合先跑通、或当你手上有人工渠道/成品号时。

**路线 B 的真正意义**：把供应商差异关在一个你自己写的 40 行服务里。以后换卡商、换 API、加人工兜底，只改这个服务，商城一行不动。

> 重要：MOLIPLY 的上游契约是**通用契约**，不是某家的。云枢、蓝兔这类"文档式"API 都要写适配层，不能直接填 URL。

---

## 1. 上游契约（必须逐字实现）

MOLIPLY 会调两个端点。**全部 POST/GET JSON，UTF-8，HMAC-SHA256 签名。**

### 1.1 鉴权头

每个请求带：

```
Content-Type: application/json
X-Merchant-Key: <你的上游 key>
X-Timestamp: <unix 秒>
X-Signature: <HMAC-SHA256(secret, f"{timestamp}.{body}") 的 hex>
```

- `body` 是请求体**原始字符串**；GET 轮询时 body 为空串 → 签名为 `HMAC(secret, f"{ts}.")`。
- 用**常量时间**比较（`hmac.compare_digest`）。
- **时间窗 ±300 秒**，防重放。`X-Merchant-Key` 用密文比。

### 1.2 下单 `POST /api/v1/recharge`

请求：

```json
{
  "session_raw": "<客户端原始 session 字符串>",
  "sku_type": "CHATGPT_PLUS_1M",
  "upstream_code": "<可选：你给这张卡密绑的上游编号>",
  "merchant": "MOLIPLY",
  "timestamp": 1767225600
}
```

`sku_type` 取值固定这几个（来自 `app/models.py: SkuType`）：

`CHATGPT_PLUS_1M` `CHATGPT_PLUS_3M` `CHATGPT_PLUS_12M` `CLAUDE_PRO_1M`
`CHATGPT_PRO_200` `CHATGPT_PRO_20X_1M` `API_CREDIT_100` `GENERIC_DIGITAL`

响应：

```json
{"order_no": "UP-abc123", "status": "pending", "message": "已受理"}
```

`status` 判定规则（`app/services/upstream_provider.py:132`）：

| status | MOLIPLY 行为 |
| --- | --- |
| `success` / `succeeded` / `completed` / `done` | 立即成功，核销卡密 |
| `failed` / `failure` / `declined` | 失败 |
| **其他任意值** | 进入轮询 |

**没有 `order_no` 会被判为 `UPSTREAM_INVALID_RESPONSE`。** 即使要立即失败，也建议给 `order_no`。

### 1.3 轮询 `GET /api/v1/recharge/{order_no}`

返回同上；`success` 即完成。轮询有指数退避、总时长上限 `upstream_max_wait_seconds`（默认 180 秒）。超时 → `UPSTREAM_TIMEOUT`，**不释放卡密**，转人工。

### 1.4 失败该如何映射

`RechargeError(code, message, release_code)`，`release_code=True` 表示"卡密退回、客户可重试"：

| 场景 | 抛什么 | release_code |
| --- | --- | --- |
| 参数非法 / 卡密无效 | `UPSTREAM_REJECTED` | True |
| 账号密码错、Session 过期 | `ACCOUNT_NOT_ELIGIBLE` | True |
| 已付款但结果不明 | `UPSTREAM_PENDING` | **False** |
| 已提交、等人工 | `AWAITING_MANUAL_REVIEW` | **False** |

`UPSTREAM_PENDING` / `AWAITING_MANUAL_REVIEW` 在 `MANUAL_REVIEW_ERRORS` 里，任务会停在"需人工复核"，卡密**保持锁定**——这就是防重复扣款的开关，别乱用。

---

## 2. 最小可跑实现（复制即用）

单文件 FastAPI，40 行，跑得通就能接。**它是骨架：真正要你填的只有 `do_recharge()`。**

```python
# upstream/main.py
import hmac, hashlib, os, time, json
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

KEY = os.environ["UPSTREAM_KEY"]
SECRET = os.environ["UPSTREAM_SECRET"]
WINDOW = 300
orders: dict[str, dict] = {}

app = FastAPI()


def verify(request: Request, body: str) -> None:
    ts = request.headers.get("x-timestamp", "")
    got = request.headers.get("x-signature", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > WINDOW:
        raise HTTPException(401, "clock skew")
    if not hmac.compare_digest(request.headers.get("x-merchant-key", ""), KEY):
        raise HTTPException(401, "bad key")
    expect = hmac.new(SECRET.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(got, expect):
        raise HTTPException(401, "bad signature")


class RechargeIn(BaseModel):
    session_raw: str
    sku_type: str
    upstream_code: str | None = None
    merchant: str = "MOLIPLY"
    timestamp: int


def do_recharge(order_no: str, req: RechargeIn) -> None:
    """把订单推进到 success / failed / pending。这是唯一要你改的地方。"""
    try:
        # ---- 你的真实履约逻辑 ----
        # 1) 调你的上家 / 人工 / 跑 Playwright
        # 2) 成功 -> success；确定失败 -> failed；不确定 -> pending
        orders[order_no]["status"] = "pending"
    except Exception as exc:                       # noqa: BLE001
        orders[order_no].update(status="pending", message=str(exc))


@app.post("/api/v1/recharge")
async def recharge(request: Request):
    body = (await request.body()).decode()
    verify(request, body)
    req = RechargeIn(**json.loads(body))
    order_no = f"UP-{int(time.time()*1000)}"
    orders[order_no] = {"status": "pending", "sku": req.sku_type, "code": req.upstream_code}
    do_recharge(order_no, req)
    return {"order_no": order_no, "status": orders[order_no]["status"], "message": "已受理"}


@app.get("/api/v1/recharge/{order_no}")
async def status(order_no: str, request: Request):
    verify(request, "")
    row = orders.get(order_no)
    if not row:
        raise HTTPException(404, "unknown order")
    return {"order_no": order_no, "status": row["status"], "message": row.get("message", "")}
```

跑：

```bash
export UPSTREAM_KEY=$(openssl rand -hex 16)
export UPSTREAM_SECRET=$(openssl rand -hex 32)uvicorn upstream.main:app --host 0.0.0.0 --port 9000
```

**只允许 HTTPS**（`net_guard.ensure_https`），所以生产要放在 Caddy/Nginx 后面，或直接用云函数（天然 HTTPS）。

### 2.1 上生产前的三处替换

上面是骨架，**内存 dict 重启即丢**，直接上生产会丢单。三处必须换：

1. **持久化**：SQLite 表 `orders(order_no PK, status, sku, upstream_code, session_fp, created_at)`
2. **幂等**：`session_fp = sha256(session_raw)`，24 小时内同指纹已有非 failed 订单 → 直接返回旧 `order_no`
3. **终态不可翻转**：更新时 `UPDATE ... WHERE status NOT IN ('success','failed')`

```python
def claim(order_no: str, fp: str, req) -> str:
    """返回订单号；同 session 尚有在途单就复用它，防重复扣款。"""
    row = con.execute(
        "SELECT order_no FROM orders WHERE session_fp=? AND status='pending' "
        "AND created_at > ? ORDER BY id DESC LIMIT 1",
        (fp, time.time() - 86400),
    ).fetchone()
    if row:
        return row[0]
    con.execute(
        "INSERT INTO orders(order_no,status,sku,upstream_code,session_fp,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (order_no, "pending", req.sku_type, req.upstream_code, fp, time.time()),
    )
    con.commit()
    return order_no


def settle(order_no: str, status: str, message: str = "") -> None:
    con.execute(
        "UPDATE orders SET status=?, message=? WHERE order_no=? "
        "AND status NOT IN ('success','failed')",
        (status, message, order_no),
    )
    con.commit()
```

`do_recharge` 开头用 `claim()`，结尾用 `settle()`，把示例里的 `orders[...] =` 全替换掉。

---

## 3. 在 MOLIPLY 里接上

后台「系统设置 → 上游代充通道」：

| 字段 | 填什么 |
| --- | --- |
| 通道模式 | `http` |
| API Base | `https://upstream.你的域名` |
| 允许的上游域名 | 你的域名，如 `upstream.你的域名`（**建议必填**，防 SSRF） |
| API Key | 上面 `UPSTREAM_KEY` |
| API Secret | 上面 `UPSTREAM_SECRET` |
| 最长等待秒数 | 先 `120`，稳定后再放宽 |
| 轮询间隔秒数 | `3` |

然后**必做**：卡密里绑上游编号。导入卡密时第二列就是：

```
MOLI-ABCD-EFGH-IJKL,<上游那条记录/账号编号>
```

不绑也能跑，只是 `upstream_code` 为 `null`，你的 `do_recharge` 得自己选货。

**走通验收**：后台生成 1 个卡密 → 商城下单 → 模拟支付 → 用真的 Session 去 `/redeem` 核销 → 任务变成功、卡密变已用。全程盯「充值任务」页。

---

## 4. 三种真实履约后端

`do_recharge` 里换成哪种，决定这条路的性质。

### 4.1 转给你的现货供应商（最快）

拿一个 Yahoo/QQ 号 FIFO 队列、一张 spreadsheet、一个现成 CSV，按顺序取一条，取完标 `used`。抢占用文件锁或数据库行锁。

- 优点：今天就能跑通全链路
- 缺点：还是人工味道，没自动化
- 关键：**取货前先加锁，成功后立刻标记**，否则并发会发重

### 4.2 人工 relay（最稳但最贵）

下单后 `status=pending`，抛到飞书群；你人工充完在后台点确认，服务再把对应对单改成 `success`。

- 适合：高客单价、要保口碑的初期
- 缺点：不能睡觉跑；**客服时长 10:00–24:00 之外的订单会积压**
- 关键：把 `UPSTREAM_PENDING` 的 `release_code` 设为 `False`

### 4.3 自己的卡池（终极）

`do_recharge` 里起 Playwright，用你的 VCC 卡去结账，3DS 码从 IMAP 读。

- 就是 MOLIPLY 自带的路线 A。**好处**是拆出来后由你的法律主体扣卡，MOLIPLY 侧埋不进卡号/CVV，也不需要给它代理
- 代价：卡、住宅代理、3DS 邮箱、维护选择器
- 关键：卡失败一定要 `failed` 而不是 `pending` 无限挂

> 很多人直接上 4.3。**建议顺序 4.1 → 4.2 → 4.3**，因为前两条能让你在一天内得到"能收钱"的闭环，再逐层替换。

---

## 5. 必做加固清单

上线前逐条打勾。这些是真实会咬人的：

- [ ] **幂等**：MOLIPLY 超时会重投，同一 `(session, sku)` 不能扣两次。按 session 指纹留 24 小时去重记录
- [ ] **时间窗**：已给 ±300 秒，别照抄成不校验
- [ ] **持久化订单**：示例用内存 dict，重启就丢。改 SQLite，字段至少 `order_no / status / sku / created_at`
- [ ] **并发上限**：同时充太多会被上家风控。加信号量或队列，宁可 `pending` 慢一点
- [ ] **超时兜底**：你在 `pending` 卡住时，MOLIPLY 会在 180 秒后放弃并转人工；别在这之后又悄悄成功（人工复核时会撞一次）
- [ ] **不记明文 session**：日志里 session 要么不打要么打前 8 位
- [ ] **key/secret 分离**：上游 key 泄漏 = 别人能免费让你充
- [ ] **限流**：给 `/api/v1/recharge` 加 IP 或 key 维度限流
- [ ] **状态只增不改**：`success`/`failed` 为终态，重复查询不能翻转
- [ ] **观测**：至少记 `order_no / sku / 耗时 / 结果`，出事能对账

---

## 6. 验证：不连商城先自测

```bash
TS=$(date +%s); BODY='{"session_raw":"x","sku_type":"CHATGPT_PLUS_1M","merchant":"test","timestamp":'$TS'}'
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$UPSTREAM_SECRET" -hex | awk '{print $NF}')
curl -s -X POST https://upstream.你的域名/api/v1/recharge \
  -H "Content-Type: application/json" -H "X-Merchant-Key: $UPSTREAM_KEY" \
  -H "X-Timestamp: $TS" -H "X-Signature: $SIG" -d "$BODY"
```

改一个字节的 body 不带新签名 → 必须 401。

---

## 7. 和自持卡池怎么分工

`routing_policy`：

- `HYBRID_SELF_FIRST`：卡池优先，失败/无卡才走你写的上游
- `SELF_VCC_ONLY`：只用卡池
- `UPSTREAM_ONLY`：只走上游（**卡池没卡时用这个**）

**自建上游的最大价值不是省钱，是让 `HYBRID` 真的有意义**：上游只在卡池撑不住时兜底，平时不花冤枉钱。

---

## 8. 常见坑

| 现象 | 原因 |
| --- | --- |
| `UPSTREAM_INSECURE_URL` | API Base 用了 http |
| `UPSTREAM_HOST_NOT_ALLOWED` | 白名单没包含该域名 |
| 全部 `UPSTREAM_TIMEOUT` | 你返回了非 success/failed 的 status 又永远不落终态 |
| 卡密被反复扣 | 失败返回了 `pending`；或没做幂等 |
| 明明成功却转人工 | 返回了 `success` 之外的值且超时——改用 `success` 并给 `order_no` |
| 一手好货被风控 | 没做并发上限 |

---

## 9. 安全边界

- 上游只管"充会员"，**不解密卡号/CVV**，也不碰客户密码
- 上游知道客户 Session → 泄漏等于能登客户账号；**Session 用完即弃**
- 上游挂了不能让商城挂：超时要分类成 `release_code`，别 500 冒到前端
