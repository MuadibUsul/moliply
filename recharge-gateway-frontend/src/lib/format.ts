export function formatMoney(value: number | string | null | undefined, digits = 2): string {
  const numeric = Number(value ?? 0);
  return `¥${Number.isFinite(numeric) ? numeric.toFixed(digits) : "0.00"}`;
}

export function formatNumber(value: number | string | null | undefined): string {
  const numeric = Number(value ?? 0);
  return new Intl.NumberFormat("zh-CN").format(Number.isFinite(numeric) ? numeric : 0);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function secondsUntil(value: string | null | undefined): number {
  if (!value) return 0;
  const date = new Date(value).getTime();
  if (Number.isNaN(date)) return 0;
  return Math.max(0, Math.floor((date - Date.now()) / 1000));
}

export function formatCountdown(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

export function maskEmail(email: string | null | undefined): string {
  if (!email || !email.includes("@")) return "***";
  const [local, domain] = email.split("@");
  if (!local) return `***@${domain}`;
  return `${local[0]}***@${domain}`;
}

export function statusTone(status: string): "success" | "warning" | "danger" | "muted" | "brand" {
  switch (status.toUpperCase()) {
    case "DELIVERED":
    case "SUCCESS":
    case "USED":
    case "ACTIVE":
      return "success";
    case "PENDING":
    case "PAID":
    case "LOCKED":
    case "AWAITING_3DS":
      return "warning";
    case "FAILED":
    case "CANCELLED":
    case "SUSPENDED":
    case "DISABLED":
      return "danger";
    case "EXHAUSTED":
      return "muted";
    default:
      return "brand";
  }
}

export function paymentLabel(provider: string | null | undefined): string {
  if (!provider) return "—";
  const map: Record<string, string> = {
    wechat: "微信支付",
    alipay: "支付宝",
    ltzf_wechat: "微信支付",
    ltzf_alipay: "支付宝",
    xunhupay: "虎皮椒",
    mock: "模拟支付",
    mock_wechat: "微信支付",
    mock_alipay: "支付宝",
    manual: "人工确认",
  };
  return map[provider] || provider;
}

export function splitDescription(description: string): string[] {
  return (description || "")
    .split(/\n+/)
    .map((part) => part.trim())
    .filter(Boolean);
}
