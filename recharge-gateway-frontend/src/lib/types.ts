export type ProductVariant = {
  id: number;
  label: string;
  months: number;
  price: number;
  original_price: number | null;
  sku_type: string | null;
  sku_label: string;
  channel_mode: string;
  active: boolean;
};

export type ProductFeature = {
  title: string;
  desc: string;
  icon?: string;
};

export type ProductFaq = { q: string; a: string };

export type Product = {
  id: number;
  slug: string;
  name: string;
  subtitle: string;
  category: string;
  category_label: string;
  brand: string;
  logo_key: string;
  delivery_type: "ACTIVATION_CODE" | "ACCOUNT_CREDENTIALS";
  delivery_label: string;
  badge: string | null;
  tags: string[];
  price: number;
  original_price: number | null;
  sold_count: number;
  warranty: string;
  delivery_note: string;
  description: string;
  usage_steps: string[];
  features: ProductFeature[];
  faq: ProductFaq[];
  active: boolean;
  variants: ProductVariant[];
  stock: number | null;
};

export type CategoryOption = {
  key: string;
  label: string;
  count: number;
};

export type ProductsResponse = {
  items: Product[];
  total: number;
  page: number;
  page_size: number;
  categories: CategoryOption[];
  delivery_options: { key: string; label: string }[];
};

export type Delivery = {
  id: number;
  kind: "ACTIVATION_CODE" | "ACCOUNT_CREDENTIALS";
  payload: string | null;
  payload_hint: string | null;
  code_status: string | null;
  redeemed_at: string | null;
  created_at: string;
};

export type OrderItem = {
  id: number;
  product_id: number;
  variant_id: number | null;
  product_name: string;
  spec_label: string;
  logo_key: string;
  brand: string;
  unit_price: number;
  quantity: number;
  delivery_type: string;
  sku_type: string | null;
  deliveries: Delivery[];
  delivered: boolean;
};

export type TimelineStep = {
  key: string;
  label: string;
  at: string | null;
  done: boolean;
};

export type Order = {
  order_no: string;
  email: string;
  amount: number;
  discount_amount: number;
  coupon_code: string | null;
  status: string;
  status_label: string;
  payment_provider: string | null;
  payment_label: string;
  stock_pending: boolean;
  created_at: string;
  expires_at: string | null;
  paid_at: string | null;
  delivered_at: string | null;
  items: OrderItem[];
  timeline: TimelineStep[];
};

export type BrandInfo = {
  crypto_enabled: boolean;
  crypto_coins: string[];
  payment_mode: string;
  mock_payment_enabled: boolean;
  support_wechat: string;
  support_hours: string;
};

export type OrderCreateResult = {
  order: Order;
  payment_method: string;
  user_token: string;
  user_email: string;
  account_created: boolean;
};

export type UserLoginResult = {
  token: string;
  expires_in: number;
  email: string;
};

export type UserProfile = {
  id: number;
  email: string;
  created_at: string;
  last_login_at: string | null;
};

export type PaymentIntent = {
  provider: string;
  qr_content: string;
  pay_url: string;
  trade_no: string;
  mock: boolean;
  expires_in: number;
  order_no: string;
  amount: number;
};

export type RedeemVerify = {
  valid: boolean;
  code: string;
  sku_type: string;
  sku_label: string;
  product_name: string;
  status: string;
  status_label: string;
  bound_email_masked: string | null;
  session_kind: "chatgpt" | "claude";
  redeemable: boolean;
  channel_mode: string;
};

export type RedeemPrecheck = {
  eligible: boolean;
  account_email: string;
  current_plan: string;
  message: string;
  warning: string | null;
  session_kind: "chatgpt" | "claude";
  expires_at: string;
};

export type RedeemStep = {
  key: string;
  label: string;
  status: "done" | "active" | "waiting" | "failed";
};

export type RedeemTask = {
  task_id: string;
  state: string;
  progress_percent: number;
  step_message: string;
  steps: RedeemStep[];
  error_code: string | null;
  error_message: string | null;
  target_email: string;
  session_kind: string;
  sku_label: string;
  code_status: string;
  can_retry: boolean;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type AdminStats = {
  orders_total: number;
  revenue_today: number;
  pending_orders: number;
  delivery_success_rate: number;
  codes_total: number;
  codes_unused: number;
  codes_locked: number;
  codes_used: number;
  tasks_total: number;
  tasks_success: number;
  tasks_failed: number;
  manual_review: number;
};

export type AdminOverview = {
  stats: AdminStats;
  trend: { date: string; key: string; count: number }[];
  channels: {
    name: string;
    status: string;
    label: string;
    mode?: string;
    detail?: string;
  }[];
  latest_orders: Order[];
};

export type AdminOrderList = {
  items: Order[];
  total: number;
  page: number;
  page_size: number;
};

export type AdminCode = {
  id: number;
  code: string;
  sku_type: string;
  sku_label: string;
  channel_mode: string;
  status: string;
  status_label: string;
  bound_email_masked: string | null;
  product_name: string;
  note: string;
  created_at: string;
  used_at: string | null;
};

export type AdminCodeList = {
  items: AdminCode[];
  total: number;
  page: number;
  page_size: number;
  stats: {
    total: number;
    unused: number;
    locked: number;
    used: number;
    disabled: number;
  };
  sku_options: { key: string; label: string }[];
};

export type AdminTask = RedeemTask & {
  target_email_full: string;
  attempts: number;
  upstream_order_no: string | null;
  vcc_card_id: number | null;
  state_label: string;
};

export type AdminTaskList = {
  items: AdminTask[];
  total: number;
  page: number;
  page_size: number;
};

export type AdminCard = {
  id: number;
  provider: string;
  card_bin: string;
  card_masked: string;
  exp_month: string;
  exp_year: string;
  holder_name: string;
  available_balance: number;
  max_bind_count: number;
  current_bind_count: number;
  failed_count: number;
  status: string;
  status_label: string;
  created_at: string;
};

export type AdminCardList = {
  items: AdminCard[];
  summary: {
    total: number;
    active: number;
    suspended: number;
    available: number;
  };
};

export type AdminProduct = Product & {
  account_stock: number;
};

export type AdminProductList = {
  items: AdminProduct[];
};

export type FeishuEventOption = {
  key: string;
  label: string;
  description: string;
  severity: "info" | "success" | "warning" | "error";
};

export type NotificationLogItem = {
  id: number;
  event: string;
  title: string;
  status: string;
  error: string;
  created_at: string;
};

export type AdminSettings = {
  values: Record<string, string | number | boolean>;
  secret_configured: Record<string, boolean>;
  payment_ready: boolean;
  crypto_ready: boolean;
  upstream_ready: boolean;
  support_ready: boolean;
  feishu_ready: boolean;
  callbacks: { ltzf: string; xunhupay: string; nowpayments: string };
  feishu_event_options: FeishuEventOption[];
  recent_notifications: NotificationLogItem[];
};
