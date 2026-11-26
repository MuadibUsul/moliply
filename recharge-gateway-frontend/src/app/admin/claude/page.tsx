"use client";

import Link from "next/link";
import {
  ArrowUpRight,
  CheckCircle2,
  CreditCard,
  Flame,
  KeyRound,
  Layers,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sprout,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatMoney } from "@/lib/format";

type ClaudeProductInfo = {
  id: number;
  slug: string;
  name: string;
  subtitle: string;
  delivery_type: string;
  fulfillment_strategy: string;
  bound_to_cce: boolean;
  active: boolean;
  price: number;
  sold_count: number;
  available_stock: number;
};

type ClaudeOverview = {
  pool: {
    reachable: boolean;
    transport: string;
    ready: boolean;
    problems: string[];
    ready_pro: number;
    mature_free: number;
    warming_free: number;
    hot_standby_target: number;
    cards_available: number;
    vcc_issuer?: {
      configured?: boolean;
      provider?: string | null;
      label?: string;
      auto_mint?: boolean;
    };
    clients?: string[];
  };
  pending_claude_orders: number;
  products: ClaudeProductInfo[];
};

export default function AdminClaudePoolPage() {
  const [data, setData] = useState<ClaudeOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [bindingId, setBindingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api<ClaudeOverview>("/api/v1/admin/claude/overview");
      setData(res);
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function bindProduct(productId: number) {
    setBindingId(productId);
    setError(null);
    setNotice(null);
    try {
      const res = await api<{ ok: boolean; auto_fulfilled: number }>(
        `/api/v1/admin/claude/bind-product/${productId}`,
        { method: "POST" },
      );
      setNotice(
        res.auto_fulfilled > 0
          ? `已接入 CCE 号池自动发货，并自动补发了 ${res.auto_fulfilled} 笔待发货订单`
          : "已将该商品绑定至 CCE 号池自动交付",
      );
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setBindingId(null);
    }
  }

  const pool = data?.pool;
  const issuerLabel = pool?.vcc_issuer?.configured
    ? pool.vcc_issuer.label || "已连接自动发卡 API"
    : `本地卡池（可用 ${pool?.cards_available ?? 0} 张）`;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">CLAUDE HIGH-WEIGHT POOL & AUTO DELIVERY</p>
          <h1 className="mt-1.5 font-display text-[26px] font-bold tracking-tight text-white">
            Claude 高权重号池与商城交付中心
          </h1>
          <p className="mt-1 text-xs text-slate-400">
            2–3 天静态住宅 ISP 自然养号防 KYC · 成熟白号 $0 存池 0 时间损耗 · 商城付款自动出池开 Pro 并打包一键客户端。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void load()}
            className="btn-secondary py-2 text-xs"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
            刷新状态
          </button>
          <a
            href="/cce/ui"
            target="_blank"
            rel="noreferrer"
            className="btn-soft py-2 text-xs"
          >
            新窗口全屏打开工作台
            <ArrowUpRight size={13} />
          </a>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-xs text-rose-200">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.08] px-4 py-3 text-xs text-emerald-200">
          {notice}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="admin-card p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>池内成熟白号（0时间损耗）</span>
            <ShieldCheck size={15} className="text-emerald-400" />
          </div>
          <p className="mt-2 font-display text-2xl font-bold text-emerald-300">
            {pool?.mature_free ?? 0}
          </p>
          <p className="mt-1 text-[11px] text-slate-500">
            已养满 3 天 · 计入商城可售库存 · 付款即触发开 Pro
          </p>
        </div>

        <div className="admin-card p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>2–3 天自然养号中</span>
            <Sprout size={15} className="text-cyan-400" />
          </div>
          <p className="mt-2 font-display text-2xl font-bold text-cyan-300">
            {pool?.warming_free ?? 0}
          </p>
          <p className="mt-1 text-[11px] text-slate-500">
            固定住宅 IP 每日自动对话 · 期间 $0 不耗卡
          </p>
        </div>

        <div className="admin-card p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>架上现成秒发 Pro</span>
            <Flame size={15} className="text-brand-300" />
          </div>
          <p className="mt-2 font-display text-2xl font-bold text-white">
            {pool?.ready_pro ?? 0}
            <span className="ml-2 text-xs font-normal text-slate-500">
              （热备水位：{pool?.hot_standby_target ?? 0}）
            </span>
          </p>
          <p className="mt-1 text-[11px] text-slate-500">
            {(pool?.hot_standby_target ?? 0) === 0
              ? "当前为 0 损耗按单现开模式"
              : `常备 ${pool?.hot_standby_target} 个供 0.5 秒极速秒发`}
          </p>
        </div>

        <div className="admin-card p-4">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>双轨发卡与待发订单</span>
            <CreditCard size={15} className="text-amber-300" />
          </div>
          <p className="mt-2 truncate text-sm font-semibold text-slate-100">
            {issuerLabel}
          </p>
          <p className="mt-1.5 text-[11px] text-slate-500">
            商城排队出池订单：{" "}
            <span className={data?.pending_claude_orders ? "font-semibold text-amber-300" : "text-slate-400"}>
              {data?.pending_claude_orders ?? 0} 笔
            </span>
          </p>
        </div>
      </div>

      {data?.products && data.products.length > 0 && (
        <div className="admin-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Layers size={15} className="text-brand-300" />
              <h2 className="text-xs font-semibold text-slate-200">
                商城 Claude 商品与 CCE 号池履约绑定
              </h2>
            </div>
            <Link href="/admin/products" className="text-[11px] text-brand-300 hover:text-brand-200">
              前往商品管理 →
            </Link>
          </div>
          <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
            {data.products.map((prod) => (
              <div
                key={prod.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-xs font-medium text-slate-100">{prod.name}</span>
                    <span className="text-[11px] text-slate-400">{formatMoney(prod.price)}</span>
                  </div>
                  <p className="mt-0.5 truncate text-[11px] text-slate-500">
                    {prod.subtitle} · 商城显示可售库存：{prod.available_stock}
                  </p>
                </div>
                {prod.bound_to_cce ? (
                  <span className="inline-flex items-center gap-1 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-[11px] text-emerald-300">
                    <CheckCircle2 size={12} />
                    已接入 CCE 自动交付
                  </span>
                ) : prod.delivery_type === "ACCOUNT_CREDENTIALS" ? (
                  <button
                    type="button"
                    disabled={bindingId === prod.id}
                    onClick={() => void bindProduct(prod.id)}
                    className="btn-primary py-1.5 text-[11px]"
                  >
                    {bindingId === prod.id ? <Loader2 size={12} className="animate-spin" /> : <KeyRound size={12} />}
                    一键接入 CCE 号池交付
                  </button>
                ) : (
                  <span className="rounded-full border border-white/10 px-2.5 py-1 text-[10px] text-slate-400">
                    代充激活码商品
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-white/[0.08] bg-[#0d0f14] shadow-2xl">
        <iframe
          src="/cce/ui"
          title="Claude 账号工作台"
          className="h-[820px] w-full border-0"
        />
      </div>
    </div>
  );
}
