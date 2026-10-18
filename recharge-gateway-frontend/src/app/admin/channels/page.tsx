"use client";

import Link from "next/link";
import { Activity, ArrowUpRight, CheckCircle2, CreditCard, RefreshCw, ServerCog, ShieldAlert, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";

type ChannelData = {
  upstream: {
    name: string;
    mode: string;
    status: string;
    label: string;
  };
  self_vcc: {
    enabled: boolean;
    dry_run: boolean;
    available_card_id: number | null;
  };
  manual_review: number;
};

export default function AdminChannelsPage() {
  const [data, setData] = useState<ChannelData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api<ChannelData>("/api/v1/admin/channels"));
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">SUPPLY & CHANNELS</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">渠道状态</h1>
          <p className="mt-2 text-[11px] text-slate-500">查看上游 API 与自营 VCC 双通道运行状态。</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] text-slate-500 hover:text-slate-200"
          aria-label="刷新"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <section className="admin-card">
          <div className="flex items-start justify-between">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-blue-400/15 bg-blue-500/10 text-blue-300">
              <ServerCog size={20} />
            </span>
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[9px] ${
              data?.upstream.status === "online"
                ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
                : "border-amber-400/20 bg-amber-400/10 text-amber-300"
            }`}>
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {data?.upstream.label || "加载中"}
            </span>
          </div>
          <h2 className="mt-4 text-base font-semibold text-slate-100">上游 API 代充通道</h2>
          <p className="mt-2 text-[11px] leading-6 text-slate-500">
            默认主力通道。将用户 Session 与上游卡密转发给供应商 API，随后按指数退避轮询充值结果。
          </p>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <p className="text-[9px] text-slate-600">当前模式</p>
              <p className="mt-1 text-[11px] text-slate-300">{data?.upstream.mode === "mock" ? "模拟模式" : "实盘模式"}</p>
            </div>
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <p className="text-[9px] text-slate-600">连接状态</p>
              <p className="mt-1 text-[11px] text-slate-300">{data?.upstream.status === "online" ? "运行正常" : "待配置"}</p>
            </div>
          </div>
          <div className="mt-4 rounded-xl border border-white/[0.055] bg-white/[0.015] p-3 text-[10px] leading-5 text-slate-600">
            上线前在 <code className="text-slate-400">.env</code> 填写 UPSTREAM_API_BASE / KEY / SECRET，并将
            UPSTREAM_MODE 切换为 http。若供应商字段不同，仅需调整 upstream_provider.py。
          </div>
          <Link href="/admin/tasks" className="link-arrow mt-4">
            查看充值任务
            <ArrowUpRight size={11} />
          </Link>
        </section>

        <section className="admin-card">
          <div className="flex items-start justify-between">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-cyan-400/15 bg-cyan-500/10 text-cyan-300">
              <CreditCard size={20} />
            </span>
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[9px] ${
              data?.self_vcc.enabled
                ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
                : "border-white/10 bg-white/[0.04] text-slate-500"
            }`}>
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {data?.self_vcc.enabled ? "已启用" : "未启用"}
            </span>
          </div>
          <h2 className="mt-4 text-base font-semibold text-slate-100">自营 VCC 通道</h2>
          <p className="mt-2 text-[11px] leading-6 text-slate-500">
            使用站长自营 RDVCC 卡池，通过美国住宅代理执行结账，并从 IMAP 邮箱自动读取 3DS 验证码。
          </p>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <p className="text-[9px] text-slate-600">运行模式</p>
              <p className="mt-1 text-[11px] text-slate-300">
                {data?.self_vcc.enabled ? (data.self_vcc.dry_run ? "演练模式" : "实盘模式") : "已关闭"}
              </p>
            </div>
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <p className="text-[9px] text-slate-600">可调度卡片</p>
              <p className="mt-1 text-[11px] text-slate-300">
                {data?.self_vcc.available_card_id ? `#${data.self_vcc.available_card_id}` : "暂无"}
              </p>
            </div>
          </div>
          <div className="mt-4 rounded-xl border border-amber-400/15 bg-amber-400/[0.045] p-3 text-[10px] leading-5 text-amber-200/65">
            启用顺序：录入 VCC 卡片 → 配置美国住宅代理 → 配置 RDVCC IMAP 邮箱 → 关闭 SELF_VCC_DRY_RUN →
            设置 SELF_VCC_ENABLED=true。未完成代理与邮箱验证前请保持演练模式。
          </div>
          <div className="mt-4 flex gap-4">
            <Link href="/admin/cards" className="link-arrow">
              管理卡池
              <ArrowUpRight size={11} />
            </Link>
            <Link href="/admin/tasks" className="link-arrow">
              人工复核（{data?.manual_review || 0}）
              <ArrowUpRight size={11} />
            </Link>
          </div>
        </section>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        {[
          { icon: Activity, title: "幂等与锁", desc: "激活码使用 SELECT FOR UPDATE + Redis 锁，杜绝一码多充。" },
          { icon: Zap, title: "失败释放", desc: "可恢复失败自动回滚激活码；支付中途异常保持锁定等待人工复核。" },
          { icon: ShieldAlert, title: "失败熔断", desc: "VCC 拒付一次即挂起卡片，避免失败交易费与连续风控。" },
        ].map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.title} className="admin-card">
              <Icon size={17} className="text-brand-300" />
              <p className="mt-3 text-xs font-medium text-slate-200">{item.title}</p>
              <p className="mt-1.5 text-[10px] leading-5 text-slate-600">{item.desc}</p>
            </div>
          );
        })}
      </div>

      {loading && (
        <div className="mt-4 flex items-center gap-2 text-[10px] text-slate-600">
          <CheckCircle2 size={12} /> 正在同步通道状态...
        </div>
      )}
    </div>
  );
}
