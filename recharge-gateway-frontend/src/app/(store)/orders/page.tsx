"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ArrowRight,
  ArrowUpRight,
  ClipboardList,
  Eye,
  EyeOff,
  KeyRound,
  LogIn,
  LogOut,
  Mail,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { Suspense, useCallback, useEffect, useState } from "react";
import { BrandTile } from "@/components/BrandTile";
import { CopyButton } from "@/components/CopyButton";
import { StatusBadge } from "@/components/StatusBadge";
import {
  ApiError,
  api,
  clearUserToken,
  getUserEmail,
  getUserToken,
  setUserEmail,
  setUserToken,
} from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/format";
import type { Order, UserLoginResult } from "@/lib/types";

type MyOrdersResponse = { email: string; items: Order[] };

function OrdersContent() {
  const searchParams = useSearchParams();
  const requestedOrder = searchParams.get("order_no") || "";

  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loginLoading, setLoginLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [selectedNo, setSelectedNo] = useState<string | null>(null);

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api<MyOrdersResponse>("/api/v1/store/my/orders");
      setOrders(data.items);
      setEmail(data.email);
      setUserEmail(data.email);
      setSelectedNo((current) => {
        const wanted = requestedOrder || current;
        if (wanted && data.items.some((order) => order.order_no === wanted)) return wanted;
        return data.items[0]?.order_no || null;
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearUserToken();
        setToken(null);
        setOrders([]);
        setSelectedNo(null);
        setError("登录已失效，请重新输入邮箱和密码");
      } else {
        setError((err as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, [requestedOrder]);

  useEffect(() => {
    const savedEmail = getUserEmail();
    if (savedEmail) setEmail(savedEmail);
    const savedToken = getUserToken();
    if (!savedToken) {
      setBooting(false);
      return;
    }
    setToken(savedToken);
    void loadOrders().finally(() => setBooting(false));
  }, [loadOrders]);

  async function login(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setError("请输入下单时填写的邮箱和密码");
      return;
    }
    setLoginLoading(true);
    setError(null);
    try {
      const data = await api<UserLoginResult>("/api/v1/auth/login", {
        method: "POST",
        body: { email: email.trim(), password },
      });
      setUserToken(data.token);
      setUserEmail(data.email);
      setToken(data.token);
      setEmail(data.email);
      setPassword("");
      await loadOrders();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoginLoading(false);
    }
  }

  function logout() {
    clearUserToken();
    setToken(null);
    setOrders([]);
    setSelectedNo(null);
    setPassword("");
    setError(null);
  }

  const selected = orders.find((order) => order.order_no === selectedNo) || orders[0] || null;

  return (
    <div className="relative overflow-hidden">
      <div className="pointer-events-none absolute left-1/2 top-0 h-[380px] w-[900px] -translate-x-1/2 rounded-full bg-brand-600/[0.08] blur-[120px]" />
      <div className="container-page relative pb-16 pt-12">
        <p className="eyebrow">MY ORDERS</p>
        <h1 className="mt-3 font-display text-[36px] font-bold tracking-tight text-white sm:text-[46px]">
          查询我的订单<span className="text-brand-400">.</span>
        </h1>
        <p className="mt-3 text-sm text-slate-500">
          使用下单时填写的邮箱和密码登录，管理订单、卡密与交付内容。
        </p>

        <div className="mt-9 grid gap-5 lg:grid-cols-[0.82fr_1.18fr]">
          <section className="surface h-fit p-5 sm:p-6 lg:sticky lg:top-24">
            {!token ? (
              <>
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-brand-400/20 bg-brand-500/10 text-brand-300">
                    <LogIn size={17} />
                  </span>
                  <div>
                    <h2 className="text-sm font-semibold text-slate-100">登录订单中心</h2>
                    <p className="mt-1 text-[10px] text-slate-600">首次下单会自动创建账户</p>
                  </div>
                </div>
                <p className="mt-4 text-[11px] leading-6 text-slate-500">
                  下单时填写的邮箱与密码就是你的订单账户。登录后可查看交付内容、充值进度与售后记录。
                </p>
                <form onSubmit={login} className="mt-5 space-y-3">
                  <div>
                    <label htmlFor="login-email" className="mb-2 block text-[11px] text-slate-400">下单邮箱</label>
                    <div className="relative">
                      <Mail size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                      <input
                        id="login-email"
                        type="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        placeholder="name@example.com"
                        className="field pl-10"
                        autoComplete="email"
                      />
                    </div>
                  </div>
                  <div>
                    <label htmlFor="login-password" className="mb-2 block text-[11px] text-slate-400">订单密码</label>
                    <div className="relative">
                      <KeyRound size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                      <input
                        id="login-password"
                        type={showPassword ? "text" : "password"}
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        placeholder="下单时设置的密码"
                        className="field pl-10 pr-11"
                        autoComplete="current-password"
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword((value) => !value)}
                        className="absolute right-3 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center text-slate-500 hover:text-slate-300"
                        aria-label={showPassword ? "隐藏密码" : "显示密码"}
                      >
                        {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                  </div>
                  {error && (
                    <p className="rounded-lg border border-rose-400/20 bg-rose-400/[0.07] px-3 py-2.5 text-[11px] leading-5 text-rose-200">
                      {error}
                    </p>
                  )}
                  <button type="submit" disabled={loginLoading} className="btn-primary w-full">
                    {loginLoading ? "正在登录..." : "登录并查看订单"}
                    {!loginLoading && <ArrowRight size={15} />}
                  </button>
                </form>
                <div className="mt-5 space-y-2 border-t border-white/[0.06] pt-4 text-[10px] leading-5 text-slate-600">
                  <p className="flex items-start gap-2"><ShieldCheck size={13} className="mt-0.5 shrink-0" /> 密码使用加盐哈希存储，客服也无法查看明文。</p>
                  <p className="flex items-start gap-2"><RefreshCw size={13} className="mt-0.5 shrink-0" /> 忘记密码请联系在线客服，核实订单后为你重置。</p>
                </div>
              </>
            ) : (
              <>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-100">我的账户</h2>
                    <p className="mt-1 break-all text-[11px] text-brand-300">{email}</p>
                  </div>
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10 text-emerald-300">
                    <ShieldCheck size={17} />
                  </span>
                </div>
                <div className="mt-5 grid gap-2">
                  <button type="button" onClick={() => void loadOrders()} className="btn-secondary w-full py-2.5">
                    刷新订单
                    <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
                  </button>
                  <button type="button" onClick={logout} className="btn-ghost w-full">
                    <LogOut size={13} />
                    退出登录
                  </button>
                </div>
                <div className="mt-4 rounded-lg border border-white/[0.05] bg-white/[0.02] p-3 text-[10px] leading-5 text-slate-600">
                  交付内容只对登录账户可见。请勿把订单密码告诉他人。
                </div>
              </>
            )}
          </section>

          <section className="surface min-h-[420px] p-5 sm:p-6">
            {booting || loading ? (
              <div className="space-y-4">
                <div className="skeleton h-6 w-40" />
                <div className="skeleton h-24 w-full" />
                <div className="skeleton h-16 w-full" />
              </div>
            ) : !token ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.025] text-slate-600">
                  <ClipboardList size={24} />
                </div>
                <p className="mt-5 text-sm text-slate-400">登录后查看你的订单</p>
                <p className="mt-1.5 max-w-xs text-xs leading-5 text-slate-600">
                  使用下单邮箱和密码登录。订单详情包含激活码、成品号账密、充值进度与售后记录。
                </p>
                <Link href="/products" className="btn-soft mt-5">去选购</Link>
              </div>
            ) : error && orders.length === 0 ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                <p className="text-sm text-slate-300">{error}</p>
                <button type="button" onClick={() => void loadOrders()} className="btn-soft mt-5">重新加载</button>
              </div>
            ) : !selected ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.025] text-slate-600">
                  <ClipboardList size={24} />
                </div>
                <p className="mt-5 text-sm text-slate-400">这个账户还没有订单</p>
                <p className="mt-1.5 text-xs text-slate-600">选购商品后，使用同一邮箱和密码即可在这里查看。</p>
                <Link href="/products" className="btn-primary mt-5">去选购</Link>
              </div>
            ) : (
              <div>
                {orders.length > 1 && (
                  <div className="mb-5">
                    <p className="mb-2 text-[10px] uppercase tracking-[0.16em] text-slate-600">我的订单 · {orders.length} 条</p>
                    <div className="scrollbar-thin flex gap-2 overflow-x-auto pb-1">
                      {orders.map((order) => (
                        <button
                          key={order.order_no}
                          type="button"
                          onClick={() => setSelectedNo(order.order_no)}
                          className={`shrink-0 rounded-lg border px-3 py-2.5 text-left transition ${
                            selected.order_no === order.order_no
                              ? "border-brand-400/35 bg-brand-500/[0.08]"
                              : "border-white/[0.07] bg-white/[0.02] hover:border-white/15"
                          }`}
                        >
                          <span className="block font-mono text-[10px] text-slate-300">{order.order_no}</span>
                          <span className="mt-0.5 block text-[10px] text-slate-600">{formatDateTime(order.created_at)}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-slate-100">订单详情</h2>
                    <p className="mt-1 font-mono text-[10px] text-slate-600">{selected.order_no}</p>
                  </div>
                  <StatusBadge status={selected.status} label={selected.status_label} />
                </div>

                {error && (
                  <p className="mt-4 rounded-lg border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2.5 text-[10px] text-amber-200">
                    {error}
                  </p>
                )}

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
                    onClick={() => void loadOrders()}
                    className="btn-secondary py-2.5"
                  >
                    刷新状态
                    <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
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
