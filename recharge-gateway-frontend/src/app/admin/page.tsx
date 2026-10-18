"use client";

import Link from "next/link";
import {
  ArrowUpRight,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  ReceiptText,
  RefreshCw,
  TrendingUp,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatMoney, formatNumber } from "@/lib/format";
import type { AdminOverview } from "@/lib/types";

const STAT_CARDS = [
  {
    key: "orders_total",
    label: "订单总数",
    icon: ReceiptText,
    tone: "text-blue-300 bg-blue-500/10 border-blue-400/15",
    hint: "累计创建订单",
  },
  {
    key: "revenue_today",
    label: "今日收入",
    icon: CircleDollarSign,
    tone: "text-cyan-300 bg-cyan-500/10 border-cyan-400/15",
    hint: "已支付订单金额",
  },
  {
    key: "pending_orders",
    label: "待处理订单",
    icon: Clock3,
    tone: "text-amber-300 bg-amber-500/10 border-amber-400/15",
    hint: "等待支付或备货",
  },
  {
    key: "delivery_success_rate",
    label: "交付成功率",
    icon: CheckCircle2,
    tone: "text-emerald-300 bg-emerald-500/10 border-emerald-400/15",
    hint: "已交付 / 已支付",
  },
] as const;

function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  icon: typeof ReceiptText;
  tone: string;
}) {
  return (
    <div className="admin-card relative overflow-hidden">
      <div className="pointer-events-none absolute -right-8 -top-10 h-28 w-28 rounded-full bg-brand-500/[0.055] blur-2xl" />
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{label}</p>
          <p className="mt-3 font-display text-[30px] font-semibold leading-none text-slate-50">{value}</p>
        </div>
        <span className={`flex h-9 w-9 items-center justify-center rounded-lg border ${tone}`}>
          <Icon size={16} />
        </span>
      </div>
      <p className="mt-4 text-[10px] text-slate-600">{hint}</p>
    </div>
  );
}

function channelTone(status: string) {
  if (status === "online") return "bg-emerald-400";
  if (status === "attention") return "bg-amber-400";
  return "bg-slate-600";
}

export default function AdminOverviewPage() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [days, setDays] = useState(12);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api<AdminOverview>(`/api/v1/admin/overview?days=${days}`);
      setData(result);
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  const maxTrend = Math.max(1, ...(data?.trend.map((item) => item.count) || [1]));

  if (loading && !data) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-48" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="admin-card h-32" />
          ))}
        </div>
        <div className="grid gap-4 xl:grid-cols-[1.5fr_0.8fr]">
          <div className="admin-card h-80" />
          <div className="admin-card h-80" />
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="admin-card flex flex-col items-center gap-3 py-20 text-center">
        <p className="text-sm text-slate-400">{error}</p>
        <button type="button" onClick={load} className="btn-soft">重新加载</button>
      </div>
    );
  }

  const stats = data?.stats;

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">BUSINESS OVERVIEW</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">运营概览</h1>
          <p className="mt-2 text-[11px] text-slate-500">实时掌握订单、交付与充值任务。</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            className="h-9 rounded-lg border border-white/[0.08] bg-[#0a111e] px-3 text-[11px] text-slate-300 outline-none"
            aria-label="统计周期"
          >
            <option value={7}>最近 7 天</option>
            <option value={12}>最近 12 天</option>
            <option value={30}>最近 30 天</option>
          </select>
          <button
            type="button"
            onClick={() => void load()}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.025] text-slate-500 hover:text-slate-200"
            aria-label="刷新数据"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="订单总数"
          value={formatNumber(stats?.orders_total || 0)}
          hint={STAT_CARDS[0].hint}
          icon={STAT_CARDS[0].icon}
          tone={STAT_CARDS[0].tone}
        />
        <StatCard
          label="今日收入"
          value={formatMoney(stats?.revenue_today || 0)}
          hint={STAT_CARDS[1].hint}
          icon={STAT_CARDS[1].icon}
          tone={STAT_CARDS[1].tone}
        />
        <StatCard
          label="待处理订单"
          value={formatNumber(stats?.pending_orders || 0)}
          hint={stats?.manual_review ? `另有 ${stats.manual_review} 个任务需人工复核` : STAT_CARDS[2].hint}
          icon={STAT_CARDS[2].icon}
          tone={STAT_CARDS[2].tone}
        />
        <StatCard
          label="交付成功率"
          value={`${(stats?.delivery_success_rate || 0).toFixed(1)}%`}
          hint={STAT_CARDS[3].hint}
          icon={STAT_CARDS[3].icon}
          tone={STAT_CARDS[3].tone}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.55fr_0.85fr]">
        <section className="admin-card">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-slate-200">订单趋势</h2>
              <p className="mt-1 text-[10px] text-slate-600">每日新建订单数量</p>
            </div>
            <span className="badge"><TrendingUp size={11} /> 最近 {days} 天</span>
          </div>

          <div className="relative mt-7 h-56">
            <div className="absolute inset-0 flex flex-col justify-between pb-6">
              {[100, 75, 50, 25, 0].map((line) => (
                <div key={line} className="flex items-center gap-3">
                  <span className="w-5 text-right text-[8px] text-slate-700">{Math.round((maxTrend * line) / 100)}</span>
                  <span className="h-px flex-1 bg-white/[0.045]" />
                </div>
              ))}
            </div>
            <div className="relative ml-8 flex h-full items-end gap-1.5 pb-6">
              {data?.trend.map((item) => (
                <div key={item.key} className="group relative flex h-full flex-1 items-end">
                  <div
                    className="w-full rounded-t-sm bg-gradient-to-t from-brand-600/80 to-brand-400 transition group-hover:from-brand-500 group-hover:to-cyan-300"
                    style={{ height: `${Math.max(4, (item.count / maxTrend) * 100)}%` }}
                    title={`${item.key}：${item.count} 单`}
                  />
                  <span className="pointer-events-none absolute -top-6 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded border border-white/10 bg-ink-900 px-1.5 py-0.5 text-[8px] text-slate-300 group-hover:block">
                    {item.count}
                  </span>
                </div>
              ))}
            </div>
            <div className="ml-8 mt-1 flex gap-1.5">
              {data?.trend.map((item) => (
                <span key={`${item.key}-label`} className="flex-1 text-center text-[7px] text-slate-700">
                  {item.date}
                </span>
              ))}
            </div>
          </div>
        </section>

        <section className="admin-card">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-200">充值通道状态</h2>
            <span className="text-[9px] text-slate-600">实时</span>
          </div>
          <div className="mt-4 divide-y divide-white/[0.05]">
            {data?.channels.map((channel) => (
              <div key={channel.name} className="py-3.5">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2 text-[11px] text-slate-300">
                    <span className={`h-1.5 w-1.5 rounded-full ${channelTone(channel.status)}`} />
                    {channel.name}
                  </span>
                  <span className={`text-[9px] ${channel.status === "attention" ? "text-amber-300" : channel.status === "online" ? "text-emerald-300" : "text-slate-600"}`}>
                    {channel.label}
                  </span>
                </div>
                <p className="mt-1 pl-3.5 text-[9px] text-slate-600">
                  {channel.detail || (channel.mode ? `模式：${channel.mode}` : "")}
                </p>
              </div>
            ))}
          </div>

          <div className="mt-4 grid grid-cols-3 gap-2 rounded-xl border border-white/[0.055] bg-white/[0.02] p-3 text-center">
            <div>
              <p className="font-display text-base text-slate-100">{stats?.tasks_total || 0}</p>
              <p className="mt-1 text-[8px] text-slate-600">充值任务</p>
            </div>
            <div>
              <p className="font-display text-base text-emerald-300">{stats?.tasks_success || 0}</p>
              <p className="mt-1 text-[8px] text-slate-600">成功</p>
            </div>
            <div>
              <p className="font-display text-base text-amber-300">{stats?.manual_review || 0}</p>
              <p className="mt-1 text-[8px] text-slate-600">待人工</p>
            </div>
          </div>

          <Link href="/admin/channels" className="link-arrow mt-3">
            查看渠道详情
            <ArrowUpRight size={11} />
          </Link>
        </section>
      </div>

      <section className="mt-4 overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0a111e]/90">
        <div className="flex items-center justify-between px-5 py-4">
          <h2 className="text-sm font-semibold text-slate-200">最新订单</h2>
          <Link href="/admin/orders" className="link-arrow">
            查看全部
            <ArrowUpRight size={11} />
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="admin-table min-w-[720px]">
            <thead>
              <tr>
                <th>订单编号</th>
                <th>商品</th>
                <th>金额</th>
                <th>订单状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data?.latest_orders.length ? (
                data.latest_orders.map((order) => (
                  <tr key={order.order_no}>
                    <td className="font-mono text-[10px] text-slate-400">{order.order_no}</td>
                    <td className="text-slate-300">
                      {order.items.map((item) => item.product_name).join("、") || "—"}
                    </td>
                    <td className="text-slate-300">{formatMoney(order.amount)}</td>
                    <td><StatusBadge status={order.status} label={order.status_label} /></td>
                    <td>
                      <Link href={`/admin/orders?q=${order.order_no}`} className="text-[10px] text-brand-300 hover:text-brand-200">
                        查看详情 →
                      </Link>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="py-12 text-center text-slate-600">暂无订单</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
