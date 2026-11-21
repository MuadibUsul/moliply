import { ADMIN_TOKEN_KEY, USER_EMAIL_KEY, USER_TOKEN_KEY } from "@/config/brand";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  auth?: boolean;
};

function extractMessage(payload: unknown, fallback: string): string {
  if (!payload) return fallback;
  if (typeof payload === "string") {
    const trimmed = payload.trim();
    if (!trimmed || trimmed.startsWith("<") || /<html|<body|<!doctype/i.test(trimmed)) {
      return fallback;
    }
    return trimmed.slice(0, 240);
  }
  if (typeof payload !== "object") return fallback;
  const data = payload as Record<string, unknown>;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    const parts = data.detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return "";
      })
      .filter(Boolean);
    if (parts.length) return parts.join("；");
  }
  if (typeof data.message === "string") return data.message;
  return fallback;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (options.auth !== false && typeof window !== "undefined") {
    if (path.startsWith("/api/v1/admin")) {
      const adminToken = window.localStorage.getItem(ADMIN_TOKEN_KEY);
      if (adminToken) headers.set("Authorization", `Bearer ${adminToken}`);
    } else if (
      path.startsWith("/api/v1/store/orders") ||
      path.startsWith("/api/v1/store/my") ||
      path.startsWith("/api/v1/auth/me") ||
      path.startsWith("/api/v1/auth/change-password")
    ) {
      const userToken = window.localStorage.getItem(USER_TOKEN_KEY);
      if (userToken) headers.set("Authorization", `Bearer ${userToken}`);
    }
  }

  const controller = options.signal ? null : new AbortController();
  const timeoutId = controller ? setTimeout(() => controller.abort(), 20_000) : null;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      signal: options.signal ?? controller?.signal,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("请求超时，请稍后重试", 408, "REQUEST_TIMEOUT");
    }
    throw new ApiError("网络连接失败，请检查网络后重试", 0, "NETWORK_ERROR");
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const data = (payload && typeof payload === "object" ? payload : {}) as Record<string, unknown>;
    const statusHint =
      response.status === 502 || response.status === 503 || response.status === 504
        ? "服务暂时繁忙，请稍后重试"
        : `请求失败（${response.status}）`;
    throw new ApiError(
      extractMessage(payload, statusHint),
      response.status,
      typeof data.code === "string" ? data.code : undefined,
    );
  }
  return payload as T;
}

export function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  });
  const result = search.toString();
  return result ? `?${result}` : "";
}

export function getAdminToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ADMIN_TOKEN_KEY);
}

export function setAdminToken(token: string): void {
  window.localStorage.setItem(ADMIN_TOKEN_KEY, token);
}

export function clearAdminToken(): void {
  window.localStorage.removeItem(ADMIN_TOKEN_KEY);
}

export function getUserToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(USER_TOKEN_KEY);
}

export function setUserToken(token: string): void {
  window.localStorage.setItem(USER_TOKEN_KEY, token);
}

export function clearUserToken(): void {
  window.localStorage.removeItem(USER_TOKEN_KEY);
}

export function getUserEmail(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(USER_EMAIL_KEY);
}

export function setUserEmail(email: string): void {
  window.localStorage.setItem(USER_EMAIL_KEY, email);
}

export function clearUserEmail(): void {
  window.localStorage.removeItem(USER_EMAIL_KEY);
}
