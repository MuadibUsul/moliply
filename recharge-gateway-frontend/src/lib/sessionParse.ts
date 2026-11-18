/**
 * Local, zero-network sanity check of pasted session material.
 *
 * The point is instant feedback while the customer is still in the paste step:
 * once both an ``accessToken`` and a mailbox are readable the wizard can say
 * "correct" before any request leaves the browser. The backend repeats the full
 * check, so this only has to be permissive and fast — never authoritative.
 */

const CLAUDE_KEY = /sk-ant-[A-Za-z0-9_\-.]{8,}/;

export function maskEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (!domain) return email;
  const head = local.slice(0, 1);
  return `${head}${"*".repeat(Math.max(1, Math.min(3, local.length - 1)))}@${domain}`;
}

export type LocalSessionParse =
  | { ok: true; email: string; maskedEmail: string }
  | { ok: false; reason: string };

export function parseSessionLocally(raw: string, kind: "chatgpt" | "claude"): LocalSessionParse {
  const text = raw.trim();
  if (!text) return { ok: false, reason: "" };

  if (kind === "claude") {
    const match = text.match(CLAUDE_KEY);
    if (match) return { ok: true, email: "", maskedEmail: "" };
    if (text.includes("sessionKey=")) return { ok: false, reason: "已识别到 Cookie，但还没看到完整的 sessionKey 值" };
    return { ok: false, reason: "尚未识别到以 sk-ant- 开头的 sessionKey" };
  }

  if (!text.startsWith("{")) {
    return { ok: false, reason: "请粘贴以 { 开头的完整 JSON（可直接打开凭证接口页复制）" };
  }
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return { ok: false, reason: "JSON 还没解析通过，请确认已全选复制完整内容" };
  }
  if (typeof data !== "object" || data === null) {
    return { ok: false, reason: "粘贴的内容不是 JSON 对象" };
  }
  const record = data as Record<string, unknown>;
  // Tolerate the `{"session": {...}}` wrapper the API sometimes returns.
  const inner =
    typeof record.session === "object" && record.session !== null
      ? (record.session as Record<string, unknown>)
      : record;
  const token = inner.accessToken ?? inner.access_token;
  if (typeof token !== "string" || !token.trim()) {
    return { ok: false, reason: "JSON 里没有 accessToken，请确认复制的是 /api/auth/session 页面" };
  }
  const user = typeof inner.user === "object" && inner.user !== null ? (inner.user as Record<string, unknown>) : {};
  const email = [user.email, inner.email].find(
    (value): value is string => typeof value === "string" && value.includes("@"),
  );
  if (!email) {
    return { ok: false, reason: "JSON 里没有账号邮箱，可能复制不完整" };
  }
  return { ok: true, email, maskedEmail: maskEmail(email) };
}
