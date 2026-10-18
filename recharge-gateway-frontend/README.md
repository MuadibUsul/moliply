# recharge-gateway-frontend

MOLIPLY 商城、免密充能中心与运营后台（Next.js 14 App Router + Tailwind CSS）。

完整部署说明见根目录 [README](../README.md)。

## 开发

```bash
npm install
npm run dev
# 端口冲突时：npm run dev -- -p 3100
```

默认把 `/api` 代理到 `http://127.0.0.1:8000`，可用环境变量覆盖：

```bash
API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

## 构建

```bash
npx tsc --noEmit
npm run build
npm start
```

Docker 使用 `output: standalone` 与多阶段构建，见 `Dockerfile`。

## 路由

| 路径 | 页面 |
| --- | --- |
| `/` | 商城首页 |
| `/products` | 商品列表/筛选/搜索 |
| `/products/[slug]` | 商品详情 |
| `/checkout` | 结算（购物车或直接购买） |
| `/pay/[orderNo]` | 扫码支付 / 支付成功交付 |
| `/orders` | 订单查询 |
| `/redeem` | 免密自助充能中心 4 步向导 |
| `/help` | 帮助中心 |
| `/admin/login` | 运营后台登录 |
| `/admin` | 运营概览 |
| `/admin/orders` `/admin/codes` `/admin/tasks` `/admin/cards` `/admin/products` `/admin/channels` | 后台管理页 |

## 设计系统

- 品牌：深空黑 `#0B0F19` + 电光蓝 `#2563EB` + 极光青 `#06B6D4`；
- 深色玻璃拟态 + 细网格/径向光晕；
- 组件：`BrandLogo`、`BrandTile`、`SiteHeader`、`ProductCard`、`RedeemWizard`、`AdminShell` 等；
- 响应式：桌面导航 + 移动端底部 Tab；后台侧边栏抽屉；
- 所有页面通过 `/api/v1/*` 读取后端，后台使用 localStorage JWT。

## 环境变量

仅以下变量会进入浏览器包：

```dotenv
NEXT_PUBLIC_STORE_URL=https://shop.moliply.com
NEXT_PUBLIC_REDEEM_URL=https://up.moliply.com/redeem
NEXT_PUBLIC_CONTACT_WECHAT=...
NEXT_PUBLIC_API_BASE_URL=
```

`NEXT_PUBLIC_API_BASE_URL` 留空表示同源 `/api`。不要在 `NEXT_PUBLIC_*` 中放任何密钥。
