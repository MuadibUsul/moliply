"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { QRCodeSVG } from "qrcode.react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle2,
  Clock3,
  LogIn,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { BrandTile } from "@/components/BrandTile";
import { CopyButton } from "@/components/CopyButton";
import { StatusBadge } from "@/components/StatusBadge";
import { ApiError, api } from "@/lib/api";
import { formatCountdown, formatDateTime, formatMoney, formatTime } from "@/lib/format";
import type { BrandInfo, Order, PaymentIntent } from "@/lib/types";

type OrderResponse = { order: Order };

type PaymentProviderKey = "wechat" | "alipay" | "crypto";

const BASE_PROVIDERS: { key: PaymentProviderKey; label: string }[] = [
  { key: "wechat", label: "微信支付" },
  { key: "alipay", label: "支付宝支付" },
];

function PayContent() {
  const params = useParams<{ orderNo: string }>();
  const searchParams = useSearchParams();
  const orderNo = params?.orderNo || "";
  const requestedProvider = searchParams.get("provider");
  const initialProvider: PaymentProviderKey =
    requestedProvider === "alipay" || requestedProvider === "crypto" ? requestedProvider : "wechat";

  const [channel, setChannel] = useState<PaymentProviderKey>(initialProvider);
  const [cryptoEnabled, setCryptoEnabled] = useState(false);
  const [cryptoCoins, setCryptoCoins] = useState<string[]>([]);
  const [order, setOrder] = useState<Order | null>(null);
  const [intent, setIntent] = useState<PaymentIntent | null>(null);
  const [loading, setLoading] = useState(true);
  const [intentLoading, setIntentLoading] = useState(false);
  const [payError, setPayError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [authRequired, setAuthRequired] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [settling, setSettling] = useState(false);
  const [checkingCrypto, setCheckingCrypto] = useState(false);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    api<BrandInfo>("/api/v1/brand")
      .then((data) => {
        setCryptoEnabled(data.crypto_enabled);
        setCryptoCoins(data.crypto_coins || []);
      })
      .catch(() => undefined);
  }, []);

  const providers = cryptoEnabled ? [...BASE_PROVIDERS, { key: "crypto" as const, label: "加密货币" }] : BASE_PROVIDERS;

  const loadOrder = useCallback(async () => {
    const data = await api<OrderResponse>(`/api/v1/store/orders/${orderNo}`);
    setOrder(data.order);
    return data.order;
  }, [orderNo]);

  const startPayment = useCallback(
    async (provider: PaymentProviderKey) => {
      setIntentLoading(true);
      setPayError(null);
      try {
        const data = await api<PaymentIntent>(`/api/v1/store/orders/${orderNo}/pay`, {
          method: "POST",
          body: { provider },
        });
        setIntent(data);
        if (data.expires_at) {
          setOrder((current) => (current ? { ...current, expires_at: data.expires_at as string } : current));
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          setAuthRequired(true);
        } else {
          setPayError((err as Error).message);
        }
      } finally {
        setIntentLoading(false);
      }
    },
    [orderNo],
  );

  useEffect(() => {
    if (!orderNo) return;
    let active = true;
    setLoading(true);
    setPageError(null);
    loadOrder()
      .then((data) => {
        if (!active) return;
        if (data.status === "PENDING") {
          void startPayment(initialProvider);
        }
      })
      .catch((err: Error) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === 401) {
          setAuthRequired(true);
        } else {
          setPageError(err.message);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [orderNo, loadOrder, startPayment, initialProvider]);

  useEffect(() => {
    if (!order || order.status !== "PENDING") {
      if (pollRef.current) window.clearInterval(pollRef.current);
      return;
    }
    pollRef.current = window.setInterval(() => {
      api<{ order: Order }>(`/api/v1/store/orders/${orderNo}/status`)
        .then((data) => {
          setOrder(data.order);
        })
        .catch(() => undefined);
    }, 2500);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [order, orderNo]);

  useEffect(() => {
    if (!order?.expires_at) {
      setRemaining(0);
      return;
    }
    const update = () => {
      const seconds = Math.max(0, Math.floor((new Date(order.expires_at as string).getTime() - Date.now()) / 1000));
      setRemaining(seconds);
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [order?.expires_at]);

  const paid = order?.status === "PAID" || order?.status === "DELIVERED";
  const delivered = order?.status === "DELIVERED";

  async function switchChannel(next: PaymentProviderKey) {
    setChannel(next);
    if (order?.status === "PENDING") {
      await startPayment(next);
    }
  }

  async function mockSettle() {
    setSettling(true);
    setPayError(null);
    try {
      const data = await api<OrderResponse>(`/api/v1/store/orders/${orderNo}/mock-settle?channel=${channel}`, {
        method: "POST",
      });
      setOrder(data.order);
    } catch (err) {
      setPayError((err as Error).message);
    } finally {
      setSettling(false);
    }
  }

  async function checkCryptoPayment() {
    setCheckingCrypto(true);
    setPayError(null);
    try {
      const data = await api<{ matched: boolean; order: Order }>(
        `/api/v1/store/orders/${orderNo}/crypto-check`,
        { method: "POST" },
      );
      setOrder(data.order);
      if (!data.matched) {
        setPayError("暂未检测到已确认的 USDT-TRC20 转账，请核对收款地址、金额和网络后再试。");
      }
    } catch (err) {
      setPayError((err as Error).message);
    } finally {
      setCheckingCrypto(false);
    }
  }

  if (loading) {
    return (
      <div className="container-page py-16">
        <div className="mx-auto max-w-xl text-center">
          <div className="skeleton mx-auto h-8 w-52" />
          <div className="skeleton mx-auto mt-4 h-4 w-72" />
        </div>
        <div className="mx-auto mt-10 grid max-w-4xl gap-6 md:grid-cols-[1.1fr_0.9fr]">
          <div className="surface h-[440px]" />
          <div className="surface h-[440px]" />
        </div>
      </div>
    );
  }

  if (authRequired) {
    return (
      <div className="container-page flex flex-col items-center py-24 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-brand-400/20 bg-brand-500/10 text-brand-300">
          <LogIn size={24} />
        </div>
        <h1 className="mt-5 text-xl font-semibold text-slate-100">请先登录订单账户</h1>
        <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
          使用下单时填写的邮箱和密码登录后，即可继续支付并查看交付内容。
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Link href={`/orders?order_no=${encodeURIComponent(orderNo)}`} className="btn-primary">
            登录我的订单
            <ArrowRight size={15} />
          </Link>
          <Link href="/products" className="btn-secondary">返回商城</Link>
        </div>
      </div>
    );
  }

  if (pageError || !order) {
    return (
      <div className="container-page flex flex-col items-center py-24 text-center">
        <p className="text-sm text-slate-400">{pageError || "订单不存在"}</p>
        <div className="mt-5 flex gap-3">
          <Link href="/orders" className="btn-soft">我的订单</Link>
          <Link href="/products" className="btn-secondary">返回商城</Link>
        </div>
      </div>
    );
  }

  if (paid) {
    return (
      <div className="container-page pb-16 pt-12">
        <div className="mx-auto max-w-3xl text-center">
          <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-full border border-emerald-400/30 bg-emerald-400/10 text-emerald-300 shadow-[0_0_60px_rgba(52,211,153,0.18)]">
            <CheckCircle2 size={36} />
          </div>
          <p className="eyebrow mt-7 justify-center">
            {delivered ? "ORDER PAID · AUTO DELIVERY" : "PAYMENT RECEIVED · PREPARING"}
          </p>
          <h1 className="mt-3 font-display text-[34px] font-bold tracking-tight text-white sm:text-[44px]">
            {delivered ? "支付成功，已自动交付" : "支付成功，正在备货"}
          </h1>
          <p className="mt-3 text-sm text-slate-500">
            {delivered
              ? "感谢选择 MOLIPLY，您的数字商品已发送至订单。"
              : "支付已到账，商品库存正在补充，客服会尽快处理并完成交付。"}
          </p>
        </div>

        <div className="mx-auto mt-9 grid max-w-4xl gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <section className="surface p-5 sm:p-6">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-100">本次交付</h2>
              <span className={`inline-flex items-center gap-1.5 text-[11px] ${delivered ? "text-emerald-300" : "text-amber-300"}`}>
                <span className="h-1.5 w-1.5 rounded-full bg-current" />
                {delivered ? "已完成" : "备货中"}
              </span>
            </div>

            <div className="mt-5 space-y-5">
              {order.items.map((item) => (
                <div key={item.id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <div className="flex items-center gap-3">
                    <BrandTile logoKey={item.logo_key} size={44} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-slate-100">{item.product_name}</p>
                      <p className="mt-0.5 text-[10px] text-slate-500">{item.spec_label} · {item.delivery_type === "ACTIVATION_CODE" ? "激活码" : "账密交付"}</p>
                    </div>
                    <span className="text-[10px] text-emerald-300">已发货</span>
                  </div>

                  {item.deliveries.length > 0 ? (
                    <div className="mt-4 space-y-3">
                      {item.deliveries.map((delivery) =>
                        delivery.kind === "ACTIVATION_CODE" ? (
                          <div key={delivery.id}>
                            <div className="flex items-center gap-2 rounded-xl border border-dashed border-brand-400/30 bg-brand-500/[0.045] p-3">
                              <code className="min-w-0 flex-1 break-all font-mono text-[13px] tracking-wider text-slate-200">
                                {delivery.payload}
                              </code>
                              {delivery.payload && <CopyButton value={delivery.payload} label="复制卡密" />}
                            </div>
                            <p className="mt-2 text-[10px] text-slate-600">请妥善保管激活码，并在自助充能中心完成核销。</p>
                            <Link
                              href={`/redeem?code=${encodeURIComponent(delivery.payload || "")}`}
                              className="btn-primary mt-3 w-full"
                            >
                              立即前往自助充能中心
                              <ArrowUpRight size={15} />
                            </Link>
                          </div>
                        ) : (
                          <div key={delivery.id} className="rounded-xl border border-white/[0.08] bg-ink-950/50 p-3">
                            <div className="mb-2 flex items-center justify-between">
                              <span className="text-[10px] text-slate-500">账号凭证</span>
                              {delivery.payload && <CopyButton value={delivery.payload} label="复制全部" />}
                            </div>
                            <pre className="whitespace-pre-wrap break-all font-mono text-[11px] leading-6 text-slate-300">
                              {delivery.payload}
                            </pre>
                            <p className="mt-2 text-[10px] leading-5 text-slate-600">
                              建议首次登录后立即修改密码并保存辅助邮箱。
                            </p>
                          </div>
                        ),
                      )}
                    </div>
                  ) : (
                    <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-400/[0.06] p-3 text-[10px] leading-5 text-amber-200">
                      已支付，正在补充库存。可凭订单号联系在线客服优先处理。
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>

          <section className="surface p-5 sm:p-6">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-100">订单进度</h2>
              <span className="font-mono text-[10px] text-slate-600">#{order.order_no}</span>
            </div>
            <div className="mt-5 space-y-0">
              {order.timeline.map((step, index) => (
                <div key={step.key} className="relative flex gap-3 pb-6 last:pb-0">
                  {index < order.timeline.length - 1 && (
                    <span className={`absolute left-[7px] top-4 h-full w-px ${step.done ? "bg-emerald-400/25" : "bg-white/[0.07]"}`} />
                  )}
                  <span
                    className={`relative z-10 mt-1 h-[15px] w-[15px] shrink-0 rounded-full border-2 ${
                      step.done
                        ? "border-emerald-400 bg-emerald-400/20"
                        : "border-slate-700 bg-ink-950"
                    }`}
                  >
                    {step.done && <span className="absolute inset-[3px] rounded-full bg-emerald-300" />}
                  </span>
                  <div className="flex flex-1 items-start justify-between gap-3">
                    <span className={`text-xs ${step.done ? "text-slate-200" : "text-slate-500"}`}>{step.label}</span>
                    <span className="text-[10px] text-slate-600">{step.at ? formatTime(step.at) : "待完成"}</span>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-6 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex justify-between text-[11px]">
                <span className="text-slate-500">实付金额</span>
                <span className="price text-lg">{formatMoney(order.amount)}</span>
              </div>
              <div className="mt-2 flex justify-between text-[10px] text-slate-600">
                <span>支付方式</span>
                <span>{order.payment_label || "—"}</span>
              </div>
              <div className="mt-1.5 flex justify-between text-[10px] text-slate-600">
                <span>支付时间</span>
                <span>{formatDateTime(order.paid_at)}</span>
              </div>
            </div>

            <Link href={`/orders?order_no=${order.order_no}`} className="btn-secondary mt-4 w-full">
              查看完整订单
              <ArrowRight size={14} />
            </Link>
          </section>
        </div>

        <div className="mt-6 flex justify-center gap-3">
          <Link href="/products" className="btn-secondary">返回商城</Link>
          <Link href="/orders" className="btn-secondary">查看我的订单</Link>
        </div>
      </div>
    );
  }

  const expired = remaining <= 0 && order.status === "PENDING";
  const directCrypto = intent?.provider === "usdt_trc20" && Boolean(intent.payment_address);
  const directAmount = Number(intent?.payment_amount || 0);

  return (
    <div className="container-page pb-16 pt-12">
      <div className="text-center">
        <p className="eyebrow justify-center">PAYMENT / PENDING</p>
        <h1 className="mt-3 font-display text-[36px] font-bold tracking-tight text-white sm:text-[46px]">
          {directCrypto ? "USDT-TRC20 转账" : "扫码完成支付"}
        </h1>
        <p className="mt-3 text-sm text-slate-500">
          {directCrypto
            ? "请向下方 TRON 地址转入精确金额；到账并确认后系统自动交付。"
            : "请使用微信或支付宝扫描下方二维码，完成后系统将自动更新订单状态。"}
        </p>
      </div>

      <div className="mx-auto mt-9 grid max-w-4xl gap-4 md:grid-cols-[1.1fr_0.9fr]">
        <section className="surface relative overflow-hidden p-5 sm:p-7">
          <div className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(ellipse_at_top,rgba(37,99,235,0.14),transparent)]" />
          <div className="relative">
            <div className="mx-auto flex w-full max-w-[360px] gap-2 rounded-xl border border-white/10 bg-white/[0.025] p-1">
              {providers.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => void switchChannel(item.key)}
                  className={`flex-1 rounded-lg px-3 py-2.5 text-[11px] transition ${
                    channel === item.key ? "bg-brand-600 text-white shadow-glow-sm" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>

            <div className="mt-7 flex flex-col items-center">
              <div className="relative rounded-2xl bg-white p-4 shadow-2xl">
                {intentLoading ? (
                  <div className="flex h-[200px] w-[200px] items-center justify-center">
                    <RefreshCw size={24} className="animate-spin text-slate-400" />
                  </div>
                ) : expired ? (
                  <div className="flex h-[200px] w-[200px] flex-col items-center justify-center text-center">
                    <Clock3 size={28} className="text-slate-400" />
                    <p className="mt-3 text-xs text-slate-500">账单已过期</p>
                  </div>
                ) : (
                  <QRCodeSVG
                    value={intent?.qr_content || `${order.order_no}`}
                    size={196}
                    level="M"
                    bgColor="#ffffff"
                    fgColor="#07101f"
                    marginSize={0}
                  />
                )}
              </div>

              <p className="mt-5 price text-[32px]">
                {directCrypto ? `${directAmount.toFixed(2)} USDT` : formatMoney(order.amount)}
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                {expired ? "订单已超时，请重新下单" : `请在 ${formatCountdown(remaining)} 内完成支付`}
              </p>
              <p className="mt-2 text-[10px] text-slate-600">
                {directCrypto ? "只支持 TRON（TRC20）网络，转错链无法找回。" : "扫码支付后，页面将自动跳转至交付结果。"}
              </p>

              {payError && (
                <div className="mt-4 w-full rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2.5 text-center text-[11px] leading-5 text-rose-200">
                  {payError}
                </div>
              )}

              {intent?.mock && !expired && (
                <div className="mt-5 w-full rounded-xl border border-dashed border-amber-400/30 bg-amber-400/[0.05] p-3">
                  <p className="text-center text-[10px] text-amber-200/80">本地演示模式 · 未接入真实支付通道</p>
                  <button type="button" onClick={mockSettle} disabled={settling} className="btn-soft mt-2 w-full">
                    {settling ? "正在确认支付..." : "模拟扫码支付成功"}
                    <Sparkles size={13} />
                  </button>
                </div>
              )}

              {directCrypto && intent && !expired && (
                <div className="mt-5 w-full space-y-3 rounded-xl border border-sky-400/20 bg-sky-400/[0.045] p-4 text-left">
                  <div className="flex items-center justify-between">
                    <span className="rounded-full border border-sky-400/25 bg-sky-400/10 px-2 py-0.5 text-[9px] font-medium text-sky-300">
                      USDT · TRC20
                    </span>
                    <span className="text-[9px] text-slate-500">自动确认到账</span>
                  </div>
                  <div>
                    <p className="text-[10px] text-slate-500">收款地址</p>
                    <div className="mt-1 flex items-center gap-2">
                      <code className="min-w-0 flex-1 break-all rounded-lg bg-black/25 px-2.5 py-2 font-mono text-[10px] leading-5 text-slate-300">
                        {intent.payment_address}
                      </code>
                      <CopyButton value={intent.payment_address || ""} compact />
                    </div>
                  </div>
                  <div>
                    <p className="text-[10px] text-slate-500">精确金额</p>
                    <div className="mt-1 flex items-center justify-between gap-2">
                      <span className="font-mono text-lg font-semibold text-sky-200">{directAmount.toFixed(2)} USDT</span>
                      <CopyButton value={directAmount.toFixed(2)} label="复制金额" />
                    </div>
                    <p className="mt-1 text-[9px] text-slate-600">必须转入与上方完全一致的金额，否则无法自动匹配。</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void checkCryptoPayment()}
                    disabled={checkingCrypto}
                    className="btn-primary w-full py-2.5"
                  >
                    {checkingCrypto ? <RefreshCw size={13} className="animate-spin" /> : <CheckCircle2 size={13} />}
                    {checkingCrypto ? "正在查询链上记录..." : "我已完成转账，立即查询"}
                  </button>
                  <p className="text-center text-[9px] leading-5 text-slate-600">
                    通常需要 1–2 分钟完成区块确认；到账后本页会自动更新。
                  </p>
                </div>
              )}

              {channel === "crypto" && intent && !expired && !directCrypto && (
                <div className="mt-5 w-full rounded-xl border border-amber-400/20 bg-amber-400/[0.045] p-3">
                  <p className="text-center text-[10px] leading-5 text-amber-200/75">
                    支持 {cryptoCoins.length ? cryptoCoins.join(" · ") : "USDT · USDC · BTC · ETH"}；请在收银台选择币种并完成支付。
                  </p>
                  <a
                    href={intent.pay_url || intent.qr_content}
                    target="_blank"
                    rel="noreferrer"
                    className="btn-soft mt-2 w-full"
                  >
                    打开加密货币收银台
                    <ArrowUpRight size={13} />
                  </a>
                </div>
              )}

              {expired && (
                <Link href="/products" className="btn-primary mt-5 w-full">
                  重新选购
                  <ArrowRight size={14} />
                </Link>
              )}
            </div>
          </div>
        </section>

        <aside className="space-y-3">
          <div className="surface p-5 sm:p-6">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-100">订单信息</h2>
              <StatusBadge status={order.status} label={order.status_label} />
            </div>
            <div className="mt-5 space-y-3 text-[11px]">
              <div className="flex justify-between gap-4">
                <span className="text-slate-500">订单编号</span>
                <span className="font-mono text-slate-300">{order.order_no}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-slate-500">下单时间</span>
                <span className="text-slate-300">{formatDateTime(order.created_at)}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-slate-500">接收邮箱</span>
                <span className="text-slate-300">{order.email}</span>
              </div>
            </div>

            <div className="mt-5 space-y-3 border-t border-white/[0.07] pt-5">
              {order.items.map((item) => (
                <div key={item.id} className="flex items-center justify-between gap-3 text-[11px]">
                  <span className="min-w-0 truncate text-slate-400">
                    {item.product_name} · {item.spec_label}
                  </span>
                  <span className="shrink-0 text-slate-300">{formatMoney(item.unit_price * item.quantity)}</span>
                </div>
              ))}
            </div>

            <div className="mt-5 flex items-end justify-between border-t border-white/[0.07] pt-5">
              <span className="text-xs text-slate-400">合计</span>
              <span className="price text-[26px]">{formatMoney(order.amount)}</span>
            </div>

            <div className="mt-4 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 text-[10px] leading-5 text-slate-600">
              如果完成支付但页面未更新，可使用订单编号查询状态或联系客服。
            </div>

            <button
              type="button"
              onClick={() => {
                void loadOrder();
              }}
              className="btn-secondary mt-3 w-full"
            >
              刷新订单状态
              <RefreshCw size={13} />
            </button>
          </div>

          <div className="flex items-start gap-2 rounded-xl border border-brand-400/15 bg-brand-500/[0.05] px-4 py-3 text-[10px] leading-5 text-slate-500">
            <ShieldCheck size={14} className="mt-0.5 shrink-0 text-brand-300" />
            支付通道签名回调校验，支付金额与订单金额严格一致后才会触发自动交付。
          </div>

          <Link href="/orders" className="inline-flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-300">
            <ArrowLeft size={12} />
            返回我的订单
          </Link>
        </aside>
      </div>
    </div>
  );
}

export default function PayPage() {
  return (
    <Suspense
      fallback={
        <div className="container-page py-16">
          <div className="skeleton mx-auto h-10 w-56" />
          <div className="mx-auto mt-8 grid max-w-4xl gap-6 md:grid-cols-[1.1fr_0.9fr]">
            <div className="surface h-[440px]" />
            <div className="surface h-[440px]" />
          </div>
        </div>
      }
    >
      <PayContent />
    </Suspense>
  );
}
