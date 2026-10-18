# MOLIPLY 独角数卡主题与支付脱敏插件（可选适配层）

主交付商城由 `recharge-gateway-frontend`（Next.js）提供，本目录用于以下场景：

- 已有独角数卡（Dujiaoka / Dujiaoka NEXT）部署，希望复用其商品、订单、卡密管理；
- 需要把 MOLIPLY 品牌首屏注入 Dujiaoka 主题；
- 需要蓝兔支付 / 虎皮椒支付的商品名称脱敏与回调强校验参考实现。

## 目录

```text
storefront-theme-moliply/
├── assets/theme.css              # 首屏品牌样式（暗色玻璃拟态）
├── views/layouts/app.blade.php   # 品牌布局（标题/metadata/顶栏/页脚）
├── views/home/index.blade.php    # 商城首屏（Hero + 分类 + 商品网格）
├── ltzf_gateway.php              # 蓝兔支付适配器（含商品名脱敏 + 回调验签）
└── xunhupay_gateway.php          # 虎皮椒 V3 适配器（含商品名脱敏 + 回调验签）
```

## 安装到 Dujiaoka

1. 将 `views/` 下的文件复制到 Dujiaoka 主题目录（不同版本通常位于
   `resources/views/theme/<theme>/`，请以你安装的版本为准）。
2. 将 `assets/theme.css` 复制到 `public/assets/theme/moliply.css`，并在布局中引用。
3. 站点名称设置为：
   `MOLIPLY 模力补给 | AI 会员 · 软件订阅 · 数字商品`
4. `.env` 中配置品牌：

```dotenv
BRAND_NAME_EN=MOLIPLY
BRAND_NAME_CN=模力补给
BRAND_SUBTITLE=AI 会员 · 软件订阅 · 数字商品
BRAND_HERO_SLOGAN=让优质数字服务，触手可及。
BRAND_CODE_PREFIX=MOLI
```

## 支付插件接入

Dujiaoka 各版本的支付网关基类/注册方式不同，`ltzf_gateway.php` 与
`xunhupay_gateway.php` 以**框架无关类**实现核心逻辑，便于映射：

- `maskedTitle($orderNo)`：发送给微信/支付宝的商品名固定从合规名称池生成，
  永不包含 ChatGPT / Claude / 代充 / 虚拟卡 / 外币等敏感词；
- `createNative(...)`：发起统一下单，返回二维码内容；
- `verifyNotify(array $payload)`：严格校验签名、订单号、金额与成功状态，
  只有返回 `verified=true` 且 `success=true` 时才允许发卡；
- `successResponse()`：返回对应通道要求的确认字符串。

映射到你的 Dujiaoka 版本时，只需在该版本的网关类中调用上述方法，并把
`createNative()` 的返回二维码交给现有支付页渲染。

### 环境变量

```dotenv
LTZF_MCH_ID=
LTZF_APP_ID=
LTZF_KEY=
LTZF_NOTIFY_URL=https://shop.example.com/pay/ltzf/notify

XUNHUPAY_APP_ID=
XUNHUPAY_APP_SECRET=
XUNHUPAY_NOTIFY_URL=https://shop.example.com/pay/xunhupay/notify
XUNHUPAY_RETURN_URL=https://shop.example.com/orders
```

> 签名算法以你签约时拿到的官方文档为准。本项目已将签名函数隔离，
> 如字段或拼接顺序有差异，只需修改对应网关文件中的 `sign()`。
> 切勿在代码或仓库中提交真实密钥。

## 合规提示

- 商品名脱敏只用于支付通道展示，不得用于向用户隐瞒收费金额或真实商品；
- 网站页脚必须保留合规声明；
- 不要售卖或赠送任何网络代理工具；
- 商品权益与售后范围以商品页展示为准。
