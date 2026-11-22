"use client";

import { Download, Loader2 } from "lucide-react";
import { useState } from "react";
import { getUserToken } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

const OS_OPTIONS: { key: string; label: string; primary?: boolean }[] = [
  { key: "windows", label: "一键下载 Windows 整合包", primary: true },
  { key: "darwin-arm64", label: "macOS (Apple Silicon)" },
  { key: "darwin-x64", label: "macOS (Intel)" },
  { key: "linux", label: "Linux" },
];

function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const match = /filename="?([^";\n]+)"?/i.exec(header);
  return match?.[1]?.trim() || fallback;
}

export function CceBundleDownload({ orderNo, deliveryId }: { orderNo: string; deliveryId: number }) {
  const [busyOs, setBusyOs] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleDownload(osKey: string) {
    setBusyOs(osKey);
    setError(null);
    try {
      const token = getUserToken();
      const headers = new Headers();
      if (token) headers.set("Authorization", `Bearer ${token}`);

      const url = `${API_BASE}/api/v1/store/orders/${encodeURIComponent(orderNo)}/deliveries/${deliveryId}/bundle?os=${encodeURIComponent(osKey)}`;
      const response = await fetch(url, { headers });
      if (!response.ok) {
        let message = `下载失败（${response.status}）`;
        try {
          const data = (await response.json()) as { detail?: unknown; message?: unknown };
          if (typeof data.detail === "string" && data.detail) message = data.detail;
          else if (typeof data.message === "string" && data.message) message = data.message;
        } catch {
          // keep fallback status message
        }
        setError(message);
        return;
      }

      const blob = await response.blob();
      const filename = filenameFromDisposition(
        response.headers.get("content-disposition"),
        `moliply-claude-${osKey}.zip`,
      );
      const objectUrl = window.URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        window.URL.revokeObjectURL(objectUrl);
      }
    } catch {
      setError("网络异常，暂时无法下载整合包，请稍后重试。");
    } finally {
      setBusyOs(null);
    }
  }

  return (
    <div className="mt-3 rounded-lg border border-brand-400/20 bg-brand-500/[0.045] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-slate-200">一键客户端整合包（解压双击即用）</span>
        <span className="text-[10px] text-slate-500">已自动写入专属密钥与网关证书</span>
      </div>
      <div className="mt-2.5 flex flex-wrap gap-2">
        {OS_OPTIONS.map((opt) => {
          const loading = busyOs === opt.key;
          return (
            <button
              key={opt.key}
              type="button"
              disabled={busyOs !== null}
              onClick={() => void handleDownload(opt.key)}
              className={
                opt.primary
                  ? "btn-primary py-2 text-[11px] disabled:opacity-60"
                  : "btn-secondary py-2 text-[11px] disabled:opacity-60"
              }
            >
              {loading ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
              {loading ? "打包中..." : opt.label}
            </button>
          );
        })}
      </div>
      {error && <p className="mt-2 text-[11px] text-rose-300">{error}</p>}
    </div>
  );
}
