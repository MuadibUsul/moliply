export const BRAND = {
  nameEn: "MOLIPLY",
  nameCn: "模力补给",
  subtitle: "AI 会员 · 软件订阅 · 数字商品",
  slogan: "让优质数字服务，触手可及。",
  codePrefix: "MOLI",
  description:
    "MOLIPLY 模力补给提供 AI 会员、软件订阅与数字商品的 24 小时自动交付服务。下单、支付、核销进度全程可查。",
  palette: {
    bg: "#0B0F19",
    primary: "#2563EB",
    accent: "#06B6D4",
  },
  contactWechat: process.env.NEXT_PUBLIC_CONTACT_WECHAT || "MOLIPLY-SUPPORT",
  storeUrl: process.env.NEXT_PUBLIC_STORE_URL || "http://localhost:3000",
  redeemUrl: process.env.NEXT_PUBLIC_REDEEM_URL || "http://localhost:3000/redeem",
} as const;

export const NAV_LINKS = [
  { href: "/", label: "首页" },
  { href: "/products", label: "全部商品" },
  { href: "/products?category=AI_MEMBERSHIP", label: "AI 会员" },
  { href: "/products?category=SOFTWARE", label: "软件订阅" },
  { href: "/products?category=DIGITAL", label: "数字商品" },
  { href: "/help", label: "帮助中心" },
] as const;

export const FOOTER_LINKS = [
  { href: "/help#terms", label: "服务条款" },
  { href: "/help#privacy", label: "隐私政策" },
  { href: "/orders", label: "订单查询" },
  { href: "/help#contact", label: "联系客服" },
] as const;

export const ASSURANCES = [
  { icon: "bolt", title: "24H 自动发货", desc: "支付完成后系统即时交付" },
  { icon: "shield", title: "安全便捷", desc: "免密核销，凭证加密保存" },
  { icon: "pulse", title: "交付进度可查", desc: "订单与充能状态实时更新" },
  { icon: "headset", title: "专业售后", desc: "质保期内按规则处理" },
] as const;

export const CATEGORY_CARDS = [
  {
    key: "AI_MEMBERSHIP",
    title: "AI 会员",
    subtitle: "ChatGPT · Claude · Gemini",
    href: "/products?category=AI_MEMBERSHIP",
    icon: "sparkles",
    tone: "blue",
  },
  {
    key: "SOFTWARE",
    title: "软件订阅",
    subtitle: "Cursor · Notion · Adobe",
    href: "/products?category=SOFTWARE",
    icon: "layers",
    tone: "violet",
  },
  {
    key: "DIGITAL",
    title: "数字商品",
    subtitle: "礼品卡 · 激活码 · 数字权益",
    href: "/products?category=DIGITAL",
    icon: "gem",
    tone: "amber",
  },
  {
    key: "REDEEM",
    title: "自助充能中心",
    subtitle: "MOLI 激活码 · 自动核销",
    href: "/redeem",
    icon: "zap",
    tone: "cyan",
  },
] as const;

export const DELIVERY_LABELS: Record<string, string> = {
  ACTIVATION_CODE: "MOLI 激活码",
  ACCOUNT_CREDENTIALS: "账密直发",
};

export const CATEGORY_LABELS: Record<string, string> = {
  AI_MEMBERSHIP: "AI 会员",
  SOFTWARE: "软件订阅",
  DEVELOPER: "开发工具",
  DESIGN: "设计创作",
  DIGITAL: "数字商品",
  API: "API 额度",
};

export const ADMIN_TOKEN_KEY = "moliply_admin_token";
export const CART_KEY = "moliply_cart_v1";
export const LAST_TASK_KEY = "moliply_last_redeem_task";
