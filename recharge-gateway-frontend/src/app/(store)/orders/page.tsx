"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ArrowRight,
  ArrowUpRight,
  ClipboardList,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { Suspense, useCallback, useEffect, useState } from "react";
import { BrandTile } from "@/components/BrandTile";
import { CopyButton } from "@/components/CopyButton";
import { StatusBadge } from "@/components/StatusBadge";
import { api, queryString } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/format";
import type { Order } from "@/lib/types";

type OrderListResponse = { items: Order[] };

function OrdersContent() {
  const searchParams = useSearchParams();
  const initialQuery = searchParams.get("order_no") || searchParams.get("q") || "";
  const [input, setInput] = useState(initialQuery);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [selectedNo, setSelectedNo] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  const search = useCallback(async (value: string) => {
    const keyword = value.trim();
    if (!keyword) return;
    setLoading(true);
    setError(null);
    setSearched(true);
    try {
      const data = await api<OrderListResponse>(`/api/v1/store/orders${queryString({ q: keyword })}`);
      setOrders(data.items);
      setSelectedNo(data.items[0]?.order_no || null);
    } catch (err) {
      setOrders([]);
      setSelectedNo(null);
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (initialQuery) {
      setInput(initialQuery);
      void search(initialQuery);
    }
  }, [initialQuery, search]);

  const selected = orders.find((order) => order.order_no === selectedNo) || orders[0] || null;

  return (
    <div className="relative overflow-hidden">
      <div className="pointer-events-none absolute left-1/2 top-0 h-[380px] w-[900px] -translate-x-1/2 rounded-full bg-brand-600/[0.08] blur-[120px]" />
      <div className="container-page relative pb-16 pt-12">
        <p className="eyebrow">ORDER LOOKUP</p>
        <h1 className="mt-3 font-display text-[36px] font-bold tracking-tight text-white sm:text-[46px]">
          查询我的订单<span className="text-brand-400">.</span>
        </h1>
        <p className="mt-3 text-sm text-slate-500">随时查询支付状态、交付结果、激活码和售后记录。</p>

        <div className="mt-9 grid gap-5 lg:grid-cols-[0.82fr_1.18fr]">
          <section className="surface h-fit p-5 sm:p-6 lg:sticky lg:top-24">
            <h2 className="text-sm font-semibold text-slate-100">快速查询</h2>
            <p className="mt-2 text-[11px] leading-5 text-slate-500">请输入订单编号或下单时填写的接收邮箱。</p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void search(input);
              }}
              className="mt-5"
            >
              <div className="relative">
                <Search size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  placeholder="订单编号 / 邮箱"
                  className="field pl-10"
                  aria-label="订单编号或邮箱"
                />
              </div>
              <button type="submit" disabled={loading || !input.trim()} className="btn-primary mt-3 w-full">
                {loading ? "正在查询..." : "查询订单"}
                {!loading && <ArrowRight size={15} />}
              </button>
            </form>

            {orders.length > 1 && (
              <div className="mt-6">
                <p className="mb-2 text-[10px] uppercase tracking-[0.16em] text-slate-600">查询结果 · {orders.length} 条订单</p>
                <div className="grid gap-2">
                  {orders.map((order) => (
                    <button
                      key={order.order_no}
                      type="button"
                      onClick={() => setSelectedNo(order.order_no)}
                      className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left transition ${
                        selected?.order_no === order.order_no
                          ? "border-brand-400/35 bg-brand-500/[0.08]"
                          : "border-white/[0.07] bg-white/[0.02] hover:border-white/15"
                      }`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate font-mono text-[10px] text-slate-300">{order.order_no}</span>
                        <span className="mt-0.5 block text-[10px] text-slate-600">{formatDateTime(order.created_at)}</span>
                      </span>
                      <span className="shrink-0 text-[10px] text-slate-500">{formatMoney(order.amount)}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-6 space-y-2 border-t border-white/[0.06] pt-4 text-[10px] leading-5 text-slate-600">
              <p className="flex items-start gap-2"><ShieldCheck size={13} className="mt-0.5 shrink-0" /> 订单信息仅用于交付与售后，我们不会向第三方出售。</p>
              <p className="flex items-start gap-2"><RefreshCw size={13} className="mt-0.5 shrink-0" /> 支付后页面会自动更新，也可手动刷新查询。</p>
            </div>
          </section>

          <section className="surface min-h-[400px] p-5 sm:p-6">
            {loading ? (
              <div className="space-y-4">
                <div className="skeleton h-6 w-40" />
                <div className="skeleton h-24 w-full" />
                <div className="skeleton h-16 w-full" />
              </div>
            ) : error ? (
              <div className="flex min-h-[340px] flex-col items-center justify-center text-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] text-slate-500">
                  <Search size={20} />
                </div>
                <p className="mt-4 text-sm text-slate-300">{error}</p>
                <p className="mt-1.5 text-xs text-slate-600">请检查订单编号是否完整，或确认下单邮箱是否正确。</p>
                <Link href="/help#contact" className="btn-soft mt-5">联系客服</Link>
              </div>
            ) : !selected ? (
              <div className="flex min-h-[340px] flex-col items-center justify-center text-center">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.025] text-slate-600">
                  <ClipboardList size={24} />
                </div>
                <p className="mt-5 text-sm text-slate-400">{searched ? "没有找到对应订单" : "输入订单信息开始查询"}</p>
                <p className="mt-1.5 max-w-xs text-xs leading-5 text-slate-600">
                  订单编号可在支付成功页或交付邮件中找到，邮箱为下单时填写的接收邮箱。
                </p>
                {searched && (
                  <div className="mt-5 flex gap-2">
                    <Link href="/products" className="btn-soft">去选购</Link>
                    <Link href="/help#contact" className="btn-secondary">联系客服</Link>
                  </div>
                )}
              </div>
            ) : (
              <div>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-slate-100">订单详情</h2>
                    <p className="mt-1 font-mono text-[10px] text-slate-600">{selected.order_no}</p>
                  </div>
                  <StatusBadge status={selected.status} label={selected.status_label} />
                </div>

                <div className="mt-5 grid gap-4 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 sm:grid-cols-2 lg:grid-cols-4">
                  <div>
                    <p className="text-[10px] text-slate-600">订单编号</p>
                    <p className="mt-1 text-[11px] text-slate-300">{selected.order_no}</p>
                  </div>
                  <div>
                    <p className="text-[10px] text-slate-600">支付方式</p>
                    <p className="mt-1 text-[11px] text-slate-300">{selected.payment_label || "—"}</p>
                  </div>
                  <div>
                    <p className="text-[10px] text-slate-600">订单金额</p>
                    <p className="mt-1 text-[11px] text-slate-300">{formatMoney(selected.amount)}</p>
                  </div>
                  <div>
                    <p className="text-[10px] text-slate-600">下单时间</p>
                    <p className="mt-1 text-[11px] text-slate-300">{formatDateTime(selected.created_at)}</p>
                  </div>
                </div>

                <div className="mt-5 space-y-4">
                  {selected.items.map((item) => (
                    <div key={item.id} className="rounded-xl border border-white/[0.07] bg-white/[0.015] p-4">
                      <div className="flex flex-wrap items-center gap-3">
                        <BrandTile logoKey={item.logo_key} size={46} />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-slate-100">{item.product_name}</p>
                          <p className="mt-0.5 text-[10px] text-slate-500">
                            {item.spec_label} · {item.delivery_type === "ACTIVATION_CODE" ? "MOLI 激活码" : "账密交付"} · 自动发货
                          </p>
                        </div>
                        <div className="text-right">
                          <p className={`text-[10px] ${item.delivered ? "text-emerald-300" : "text-amber-300"}`}>
                            {item.delivered ? "已发货" : selected.status === "PAID" ? "备货中" : "待支付"}
                          </p>
                          <p className="mt-1 text-[11px] text-slate-400">{formatMoney(item.unit_price * item.quantity)}</p>
                        </div>
                      </div>

                      {item.deliveries.length > 0 && (
                        <div className="mt-4 space-y-3">
                          {item.deliveries.map((delivery) =>
                            delivery.kind === "ACTIVATION_CODE" ? (
                              <div key={delivery.id}>
                                <div className="flex items-center gap-2 rounded-lg border border-dashed border-brand-400/30 bg-brand-500/[0.04] p-3">
                                  <code className="min-w-0 flex-1 break-all font-mono text-[12px] tracking-wider text-slate-200">
                                    {delivery.payload}
                                  </code>
                                  {delivery.payload && <CopyButton value={delivery.payload} label="复制" />}
                                </div>
                                <div className="mt-3 flex flex-wrap gap-2">
                                  <Link
                                    href={`/redeem?code=${encodeURIComponent(delivery.payload || "")}`}
                                    className="btn-soft"
                                  >
                                    前往核销中心
                                    <ArrowUpRight size={13} />
                                  </Link>
                                  <Link href="/help#contact" className="btn-secondary py-2.5">申请售后</Link>
                                </div>
                              </div>
                            ) : (
                              <div key={delivery.id} className="rounded-lg border border-white/[0.08] bg-ink-950/50 p-3">
                                <div className="mb-2 flex items-center justify-between">
                                  <span className="text-[10px] text-slate-500">账号凭证</span>
                                  {delivery.payload && <CopyButton value={delivery.payload} label="复制全部" />}
                                </div>
                                <pre className="whitespace-pre-wrap break-all font-mono text-[11px] leading-6 text-slate-300">
                                  {delivery.payload}
                                </pre>
                              </div>
                            ),
                          )}
                        </div>
                      )}

                      {selected.status === "PAID" && item.deliveries.length === 0 && (
                        <p className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2.5 text-[10px] text-amber-200">
                          已支付，正在备货。客服会尽快完成交付。
                        </p>
                      )}
                    </div>
                  ))}
                </div>

                <div className="mt-5 flex flex-wrap gap-2 border-t border-white/[0.06] pt-5">
                  {selected.status === "PENDING" && (
                    <Link href={`/pay/${selected.order_no}`} className="btn-primary py-2.5">
                      继续支付
                      <ArrowRight size={14} />
                    </Link>
                  )}
                  <Link href="/help#contact" className="btn-secondary py-2.5">申请售后</Link>
                  <button
                    type="button"
                    onClick={() => void search(selected.order_no)}
                    className="btn-secondary py-2.5"
                  >
                    刷新状态
                    <RefreshCw size={13} />
                  </button>
                </div>

                <div className="mt-5 grid gap-2 rounded-xl border border-white/[0.05] bg-white/[0.015] p-4 sm:grid-cols-2">
                  {selected.timeline.map((step) => (
                    <div key={step.key} className="flex items-center justify-between gap-3 text-[10px]">
                      <span className={step.done ? "text-slate-300" : "text-slate-600"}>{step.label}</span>
                      <span className="text-slate-600">{step.at ? formatDateTime(step.at) : "待完成"}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

export default function OrdersPage() {
  return (
    <Suspense
      fallback={
        <div className="container-page py-16">
          <div className="skeleton h-10 w-64" />
          <div className="mt-8 grid gap-5 lg:grid-cols-[0.82fr_1.18fr]">
            <div className="surface h-64" />
            <div className="surface h-96" />
          </div>
        </div>
      }
    >
      <OrdersContent />
    </Suspense>
  );
}
