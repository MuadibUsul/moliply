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
  if (typeof payload === "string") return payload;
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

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError("网络连接失败，请检查网络后重试", 0, "NETWORK_ERROR");
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
    const data = (payload || {}) as Record<string, unknown>;
    throw new ApiError(
      extractMessage(payload, `请求失败（${response.status}）`),
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
