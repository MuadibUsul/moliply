"use client";

import Link from "next/link";
import { ArrowRight, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect } from "react";

export default function StoreError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="container-page flex flex-col items-center py-24 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-rose-400/20 bg-rose-400/10 text-rose-300">
        <TriangleAlert size={24} />
      </div>
      <h1 className="mt-5 text-xl font-semibold text-slate-100">页面加载失败</h1>
      <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
        网络或服务暂时异常，已支付的订单不会丢失，可以稍后重试或到「我的订单」查看状态。
      </p>
      <div className="mt-6 flex flex-wrap justify-center gap-3">
        <button type="button" onClick={reset} className="btn-primary">
          <RefreshCw size={15} />
          重新加载
        </button>
        <Link href="/orders" className="btn-secondary">
          我的订单
          <ArrowRight size={15} />
        </Link>
      </div>
      {error.digest && <p className="mt-5 font-mono text-[10px] text-slate-600">错误编号 {error.digest}</p>}
    </div>
  );
}
