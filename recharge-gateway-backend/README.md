# recharge-gateway-backend

FastAPI + SQLAlchemy + Celery + Playwright 的 MOLIPLY 后端。

完整部署与业务说明见根目录 [README](../README.md)。

## 本地运行

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
DATABASE_URL=sqlite:///./moliply.db TASK_MODE=inline \
PUBLIC_STORE_URL=http://localhost:3000 PUBLIC_REDEEM_URL=http://localhost:3000/redeem \
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`TASK_MODE`：

| 值 | 用途 |
| --- | --- |
| `inline` | 本地演示：FastAPI 进程内线程执行充值 |
| `celery` | 生产：投递到 Redis，由 Celery Worker 执行 |
| `manual` | 测试：仅创建任务，由测试代码显式执行 |

## 测试

```bash
.venv/Scripts/python -m pytest -q
```

## 主要接口

公开：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/store/products` | 商品列表（分类/交付/搜索/排序/分页） |
| GET | `/api/v1/store/products/{slug-or-id}` | 商品详情 |
| POST | `/api/v1/store/coupons/validate` | 优惠码校验 |
| POST | `/api/v1/store/orders` | 创建订单（服务端计价 + 邮箱/密码建账户） |
| POST | `/api/v1/auth/login` | 客户邮箱+密码登录 |
| GET | `/api/v1/auth/me` | 当前客户账户 |
| POST | `/api/v1/auth/change-password` | 修改客户密码（旧令牌失效） |
| GET | `/api/v1/store/my/orders` | 当前账户的订单列表 |
| GET | `/api/v1/store/orders/{order_no}` | 订单详情（仅订单所属账户） |
| GET | `/api/v1/store/orders/{order_no}/status` | 支付/交付状态轮询（仅所属账户） |
| POST | `/api/v1/store/orders/{order_no}/pay` | 创建支付二维码 |
| POST | `/api/v1/store/orders/{order_no}/mock-settle` | 仅演示：模拟支付成功 |
| POST | `/api/v1/redeem/verify-code` | 验证 MOLI 激活码 |
| POST | `/api/v1/redeem/parse-session` | Session 解析 + 账号预检 |
| POST | `/api/v1/redeem/submit` | 锁定卡密并创建充值任务 |
| GET | `/api/v1/redeem/status/{task_id}` | 任务进度 |
| POST | `/api/v1/payments/ltzf/notify` | 蓝兔支付异步回调 |
| POST | `/api/v1/payments/xunhupay/notify` | 虎皮椒异步回调 |
| POST | `/api/v1/payments/nowpayments/ipn` | NOWPayments IPN（USDT/USDC/BTC/ETH） |

运营后台（Bearer JWT）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/admin/login` | 管理员登录 |
| GET | `/api/v1/admin/overview` | 概览统计/趋势/通道 |
| GET | `/api/v1/admin/orders` | 订单列表 |
| GET | `/api/v1/admin/orders/{order_no}` | 订单详情 |
| POST | `/api/v1/admin/orders/{order_no}/mark-paid` | 人工标记已支付 |
| POST | `/api/v1/admin/orders/{order_no}/fulfill` | 人工重发 |
| GET | `/api/v1/admin/codes` | 卡密列表/统计 |
| POST | `/api/v1/admin/codes/generate` | 批量生成 |
| POST | `/api/v1/admin/codes/import` | 批量导入（可带上游卡密） |
| PATCH | `/api/v1/admin/codes/{id}/status` | 作废/恢复 |
| GET | `/api/v1/admin/tasks` | 充值任务列表 |
| POST | `/api/v1/admin/tasks/{task_id}/retry` | 失败任务重试 |
| POST | `/api/v1/admin/tasks/cleanup` | 清理过期 Session 凭证 |
| GET | `/api/v1/admin/cards` | VCC 卡池 |
| POST | `/api/v1/admin/cards` | 录入卡片（AES-GCM） |
| PATCH | `/api/v1/admin/cards/{id}` | 状态/余额/失败计数 |
| GET | `/api/v1/admin/products` | 商品与库存 |
| PATCH | `/api/v1/admin/products/{id}` | 上下架/价格/角标 |
| POST | `/api/v1/admin/accounts/import` | 导入成品号账密 |
| GET | `/api/v1/admin/channels` | 双通道状态 |
| POST | `/api/v1/admin/users/reset-password` | 后台重置客户密码 |

## 充值状态机

```text
PENDING
  → CHECKING_ACCOUNT
  → CREATING_CHECKOUT (SELF_VCC)
  → PAYING
  → AWAITING_3DS (SELF_VCC)
  → SUCCESS
  → FAILED
```

失败策略：

- `release_code=True`：可恢复失败，激活码回到 `UNUSED`（保留绑定邮箱）；
- `release_code=False`：支付确认中或需人工复核，保持 `LOCKED`，后台可重试；
- 成功：`activation_codes.status=USED`，立即清空 Session 凭证。

## 目录

- `app/api`：路由；
- `app/services`：加密、卡密、Session、账户、上游、自营 VCC、IMAP、订单、支付；
- `app/tasks`：Celery Worker/Beat；
- `app/seed.py`：演示目录与库存；
- `tests/`：59 项 pytest。
