"use client";

import { useSearchParams } from "next/navigation";
import {
  CheckCircle2,
  Copy,
  KeyRound,
  Loader2,
  PackageCheck,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { Suspense, useCallback, useEffect, useState } from "react";
import { BrandTile } from "@/components/BrandTile";
import { CopyButton } from "@/components/CopyButton";
import { Modal } from "@/components/Modal";
import { StatusBadge } from "@/components/StatusBadge";
import { api, queryString } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatDateTime, formatMoney, formatTime } from "@/lib/format";
import type { AdminOrderList, Order } from "@/lib/types";

const STATUS_TABS = [
  { key: "ALL", label: "全部" },
  { key: "PENDING", label: "待支付" },
  { key: "PAID", label: "已支付" },
  { key: "DELIVERED", label: "已交付" },
  { key: "FAILED", label: "失败" },
  { key: "CANCELLED", label: "已取消" },
];

type OrderResponse = { order: Order };

function AdminOrdersContent() {
  const searchParams = useSearchParams();
  const initialQuery = searchParams.get("q") || "";
  const [status, setStatus] = useState("ALL");
  const [queryInput, setQueryInput] = useState(initialQuery);
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AdminOrderList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Order | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api<AdminOrderList>(
        `/api/v1/admin/orders${queryString({
          status,
          q: submittedQuery || undefined,
          page,
          page_size: 20,
        })}`,
      );
      setData(result);
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, [status, submittedQuery, page]);

  useEffect(() => {
    void load();
  }, [load]);

  async function orderAction(action: "mark-paid" | "fulfill") {
    if (!selected) return;
    if (!window.confirm(action === "mark-paid" ? "确认将该订单标记为已支付并发货？" : "确认重新执行自动发货？")) return;
    setActionLoading(true);
    try {
      const result = await api<OrderResponse>(`/api/v1/admin/orders/${selected.order_no}/${action}`, {
        method: "POST",
      });
      setSelected(result.order);
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function resetCustomerPassword() {
    if (!selected) return;
    const next = window.prompt(`为 ${selected.email} 设置新的订单密码（至少 8 位）`);
    if (!next) return;
    if (next.length < 8) {
      window.alert("密码至少需要 8 位");
      return;
    }
    if (!window.confirm("确认重置？该客户现有的登录状态会立即失效。")) return;
    setActionLoading(true);
    setError(null);
    try {
      await api("/api/v1/admin/users/reset-password", {
        method: "POST",
        body: { email: selected.email, new_password: next },
      });
      window.alert("客户密码已重置，请把新密码安全地告知客户。");
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setActionLoading(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / 20));

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">ORDER MANAGEMENT</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">订单管理</h1>
          <p className="mt-2 text-[11px] text-slate-500">查看支付状态、交付内容与售后处理记录。</p>
        </div>
        <div className="flex items-center gap-2">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setPage(1);
              setSubmittedQuery(queryInput.trim());
            }}
            className="relative"
          >
            <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
            <input
              value={queryInput}
              onChange={(event) => setQueryInput(event.target.value)}
              placeholder="订单编号 / 邮箱"
              className="admin-input h-9 w-[210px] pl-8"
              aria-label="搜索订单"
            />
          </form>
          <button
            type="button"
            onClick={() => void load()}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] text-slate-500 hover:text-slate-200"
            aria-label="刷新"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap gap-2">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => {
              setStatus(tab.key);
              setPage(1);
            }}
            className={`rounded-lg border px-3 py-2 text-[10px] transition ${
              status === tab.key
                ? "border-brand-400/30 bg-brand-500/15 text-brand-200"
                : "border-white/[0.07] bg-white/[0.02] text-slate-500 hover:text-slate-200"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0a111e]/90">
        <div className="overflow-x-auto">
          <table className="admin-table min-w-[900px]">
            <thead>
              <tr>
                <th>订单编号</th>
                <th>接收邮箱</th>
                <th>商品</th>
                <th>金额</th>
                <th>状态</th>
                <th>下单时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {loading && !data ? (
                <tr>
                  <td colSpan={7} className="py-16 text-center text-slate-600">
                    <Loader2 size={18} className="mx-auto animate-spin" />
                  </td>
                </tr>
              ) : data?.items.length ? (
                data.items.map((order) => (
                  <tr key={order.order_no}>
                    <td className="font-mono text-[10px] text-slate-400">{order.order_no}</td>
                    <td className="max-w-[180px] truncate text-[11px] text-slate-400">{order.email}</td>
                    <td className="max-w-[220px] truncate text-[11px] text-slate-300">
                      {order.items.map((item) => `${item.product_name} ${item.spec_label}`).join("、")}
                    </td>
                    <td className="text-[11px] text-slate-300">{formatMoney(order.amount)}</td>
                    <td><StatusBadge status={order.status} label={order.status_label} /></td>
                    <td className="text-[10px] text-slate-500">{formatDateTime(order.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        onClick={() => setSelected(order)}
                        className="text-[10px] text-brand-300 hover:text-brand-200"
                      >
                        查看详情 →
                      </button>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7} className="py-16 text-center text-[11px] text-slate-600">暂无符合条件的订单</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between border-t border-white/[0.055] px-5 py-3 text-[10px] text-slate-600">
          <span>共 {data?.total || 0} 条订单</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              className="rounded border border-white/[0.08] px-2.5 py-1.5 disabled:opacity-30"
            >
              上一页
            </button>
            <span>{page} / {totalPages}</span>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage((value) => value + 1)}
              className="rounded border border-white/[0.08] px-2.5 py-1.5 disabled:opacity-30"
            >
              下一页
            </button>
          </div>
        </div>
      </div>

      <Modal
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected ? `订单 ${selected.order_no}` : ""}
        maxWidth="max-w-3xl"
      >
        {selected && (
          <div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <StatusBadge status={selected.status} label={selected.status_label} />
              <div className="flex items-center gap-2">
                {selected.status === "PENDING" && (
                  <button
                    type="button"
                    disabled={actionLoading}
                    onClick={() => void orderAction("mark-paid")}
                    className="btn-soft py-2"
                  >
                    <CheckCircle2 size={13} /> 标记已支付
                  </button>
                )}
                {selected.status === "PAID" && selected.stock_pending && (
                  <button
                    type="button"
                    disabled={actionLoading}
                    onClick={() => void orderAction("fulfill")}
                    className="btn-soft py-2"
                  >
                    <PackageCheck size={13} /> 重新发货
                  </button>
                )}
                <button
                  type="button"
                  disabled={actionLoading}
                  onClick={() => void resetCustomerPassword()}
                  className="btn-soft py-2"
                >
                  <KeyRound size={13} /> 重置客户密码
                </button>
                <span className="flex items-center gap-1 font-mono text-[10px] text-slate-600">
                  <Copy size={11} /> {selected.order_no}
                </span>
              </div>
            </div>

            <div className="mt-4 grid gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 text-[11px] sm:grid-cols-2">
              <div className="flex justify-between gap-3"><span className="text-slate-500">接收邮箱</span><span className="text-slate-300">{selected.email}</span></div>
              <div className="flex justify-between gap-3"><span className="text-slate-500">支付方式</span><span className="text-slate-300">{selected.payment_label || "—"}</span></div>
              <div className="flex justify-between gap-3"><span className="text-slate-500">订单金额</span><span className="text-slate-300">{formatMoney(selected.amount)}</span></div>
              <div className="flex justify-between gap-3"><span className="text-slate-500">优惠</span><span className="text-slate-300">-{formatMoney(selected.discount_amount)}</span></div>
              <div className="flex justify-between gap-3"><span className="text-slate-500">下单时间</span><span className="text-slate-300">{formatDateTime(selected.created_at)}</span></div>
              <div className="flex justify-between gap-3"><span className="text-slate-500">支付时间</span><span className="text-slate-300">{formatDateTime(selected.paid_at)}</span></div>
            </div>

            <div className="mt-4 space-y-3">
              {selected.items.map((item) => (
                <div key={item.id} className="rounded-xl border border-white/[0.06] bg-white/[0.015] p-4">
                  <div className="flex items-center gap-3">
                    <BrandTile logoKey={item.logo_key} size={40} />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-slate-200">{item.product_name}</p>
                      <p className="mt-0.5 text-[10px] text-slate-600">{item.spec_label} · {item.delivery_type === "ACTIVATION_CODE" ? "激活码" : "账密"}</p>
                    </div>
                    <span className="text-[10px] text-slate-500">x{item.quantity} · {formatMoney(item.unit_price * item.quantity)}</span>
                  </div>
                  <div className="mt-3 space-y-2">
                    {item.deliveries.map((delivery) => (
                      <div key={delivery.id} className="flex items-start gap-2 rounded-lg border border-white/[0.05] bg-ink-950/45 p-2.5">
                        <code className="min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-[10px] leading-5 text-slate-400">
                          {delivery.payload || "—"}
                        </code>
                        {delivery.payload && <CopyButton value={delivery.payload} compact />}
                      </div>
                    ))}
                    {!item.deliveries.length && (
                      <p className="rounded-lg border border-amber-400/15 bg-amber-400/[0.05] px-2.5 py-2 text-[10px] text-amber-200/80">
                        尚未交付
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-4 grid gap-2 rounded-xl border border-white/[0.05] bg-white/[0.015] p-3 sm:grid-cols-2">
              {selected.timeline.map((step) => (
                <div key={step.key} className="flex justify-between gap-2 text-[10px]">
                  <span className={step.done ? "text-slate-400" : "text-slate-600"}>{step.label}</span>
                  <span className="text-slate-600">{step.at ? formatTime(step.at) : "待完成"}</span>
                </div>
              ))}
            </div>

            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setSelected(null)} className="btn-secondary py-2.5">
                关闭
                <X size={13} />
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

export default function AdminOrdersPage() {
  return (
    <Suspense fallback={<div className="admin-card h-64" />}>
      <AdminOrdersContent />
    </Suspense>
  );
}
