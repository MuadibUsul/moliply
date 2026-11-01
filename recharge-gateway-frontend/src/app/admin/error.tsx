"use client";

import { RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect } from "react";

export default function AdminError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="rounded-2xl border border-rose-400/20 bg-rose-400/[0.06] p-8 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl border border-rose-400/25 bg-rose-400/10 text-rose-300">
        <TriangleAlert size={22} />
      </div>
      <h2 className="mt-4 text-sm font-semibold text-slate-100">数据加载失败</h2>
      <p className="mx-auto mt-2 max-w-md text-[11px] leading-5 text-slate-500">
        接口返回异常，可能是登录状态过期或后端不可用。可先重试；仍失败请检查后端健康状态。
      </p>
      <button type="button" onClick={reset} className="btn-secondary mx-auto mt-5 py-2.5">
        <RefreshCw size={13} />
        重试
      </button>
      {error.digest && <p className="mt-4 font-mono text-[10px] text-slate-600">错误编号 {error.digest}</p>}
    </div>
  );
}
