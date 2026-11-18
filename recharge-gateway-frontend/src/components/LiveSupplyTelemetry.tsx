"use client";

import { Activity, CheckCircle2, Gauge, Radar, WifiOff } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Telemetry = {
  gateway_status: "NORMAL" | "BUSY" | "DEGRADED";
  uptime_percent: number;
  window_hours: number;
  avg_seconds: number;
  succeeded_in_window: number;
  sample_size: number;
  recent: { at: string; email: string; sku_label: string }[];
};

const STATUS_STYLE: Record<Telemetry["gateway_status"], { label: string; dot: string; text: string }> = {
  NORMAL: {
    label: "正常",
    dot: "bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.9)]",
    text: "text-emerald-300",
  },
  BUSY: {
    label: "繁忙",
    dot: "bg-amber-300 shadow-[0_0_10px_rgba(252,211,77,0.9)]",
    text: "text-amber-300",
  },
  DEGRADED: {
    label: "波动",
    dot: "bg-rose-400 shadow-[0_0_10px_rgba(251,113,133,0.9)]",
    text: "text-rose-300",
  },
};

/** "3 分钟前" with a floor of "刚刚"; absolute past 1 hour. */
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "刚刚";
  const minutes = Math.floor((Date.now() - then) / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时前`;
}

export function LiveSupplyTelemetry({ className = "" }: { className?: string }) {
  const [data, setData] = useState<Telemetry | null>(null);
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api<Telemetry>("/api/v1/store/live-telemetry")
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setFailed(false);
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the "n minutes ago" labels honest without refetching.
  useEffect(() => {
    const timer = window.setInterval(() => setTick((value) => value + 1), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  if (failed && !data) {
    return (
      <div
        className={`flex items-center gap-2 rounded-2xl border border-white/[0.06] bg-white/[0.018] px-4 py-3 text-[11px] text-slate-500 ${className}`}
      >
        <WifiOff size={13} className="text-slate-600" />
        实时状态暂时不可用，充能网关仍在正常服务
      </div>
    );
  }

  if (!data) {
    return (
      <div className={`h-[58px] animate-pulse rounded-2xl border border-white/[0.06] bg-white/[0.018] ${className}`} />
    );
  }

  const style = STATUS_STYLE[data.gateway_status] ?? STATUS_STYLE.NORMAL;
  const latest = data.recent[0];

  return (
    <div
      className={`relative overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.02] px-4 py-3 ${className}`}
      data-tick={tick}
    >
      <div className="pointer-events-none absolute inset-y-0 left-0 w-24 bg-[radial-gradient(ellipse_at_left,rgba(37,99,235,0.16),transparent_72%)]" />
      <div className="relative flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px]">
        <span className="inline-flex items-center gap-2">
          <Radar size={13} className="text-brand-300" />
          <span className="text-slate-400">充能网关状态</span>
          <span className={`inline-flex items-center gap-1.5 font-medium ${style.text}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
            {style.label} ({data.uptime_percent.toFixed(1)}%)
          </span>
        </span>

        <span className="hidden h-3 w-px bg-white/10 sm:block" />

        <span className="inline-flex items-center gap-2 text-slate-400">
          <Gauge size={13} className="text-slate-600" />
          近 {data.window_hours}h 平均充能耗时：
          <span className="font-medium text-slate-200">{data.avg_seconds} 秒</span>
        </span>

        <span className="hidden h-3 w-px bg-white/10 sm:block" />

        <span className="inline-flex items-center gap-2 text-slate-400">
          <Activity size={13} className="text-slate-600" />
          近 {data.window_hours}h 成功
          <span className="font-medium text-slate-200">{data.succeeded_in_window}</span> 单
        </span>

        {latest && (
          <span className="inline-flex min-w-0 items-center gap-1.5 text-slate-600">
            <CheckCircle2 size={12} className="shrink-0 text-emerald-400/70" />
            <span className="truncate">
              {relativeTime(latest.at)} {latest.email} 完成{latest.sku_label ? ` ${latest.sku_label}` : ""} 自动充能
            </span>
          </span>
        )}
      </div>
    </div>
  );
}
