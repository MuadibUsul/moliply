# 上游履约服务

MOLIPLY 的货源端：实现商城的上游契约，负责真正把会员充上。

**先读** [docs/self-hosted-upstream.md](../docs/self-hosted-upstream.md)。

## 两种模式

| FULFILL_MODE | 行为 |
| --- | --- |
| `manual` | 接单后挂起，你在 `/admin` 点成功或失败 |
| `stock` | 按 SKU 认领一条预导入的码，认到即成功，没货则挂起等补货 |

## 本地起

```bash
pip install -r requirements.txt
export UPSTREAM_KEY=$(openssl rand -hex 16)
export UPSTREAM_SECRET=$(openssl rand -hex 32)
export UPSTREAM_ADMIN_TOKEN=$(openssl rand -hex 12)
export DB_PATH=./up.db
uvicorn app.main:app --port 9000
```

控制台 http://127.0.0.1:9000/admin（粘贴 admin token）。

## 部署

见 `deploy/upstream.sh`；装好后在商城后台「系统设置 → 上游代充通道」填 Base `https://moliply.shop/upstream`。

## 加第三种履约方式

改 `app/main.py` 的 `run_fulfillment()`：换成调你的上家、转人工群、或起 Playwright 用卡片结账。契约不变，商城不用动。
