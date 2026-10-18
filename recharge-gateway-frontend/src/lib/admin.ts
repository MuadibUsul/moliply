import { ApiError, clearAdminToken } from "@/lib/api";

export function handleAdminError(error: unknown): string {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    clearAdminToken();
    if (typeof window !== "undefined") {
      window.location.href = "/admin/login";
    }
    return "登录状态已过期，请重新登录";
  }
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}
