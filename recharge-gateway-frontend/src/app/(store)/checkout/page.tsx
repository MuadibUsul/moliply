"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Eye,
  EyeOff,
  KeyRound,
  Minus,
  Plus,
  ShieldCheck,
  ShoppingBag,
  Tag,
  Trash2,
} from "lucide-react";
import { Suspense, useEffect, useMemo, useState } from "react";
import { BrandTile } from "@/components/BrandTile";
import { useCart, type CartItem } from "@/lib/cart";
import { api, getUserEmail, setUserEmail, setUserToken } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import type { BrandInfo, OrderCreateResult, Product } from "@/lib/types";

type CouponResult = { valid: boolean; code: string; discount: number; message: string };
type PaymentMethod = "wechat" | "alipay" | "crypto";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function PaymentMark({ method }: { method: PaymentMethod }) {
  if (method === "crypto") {
    return (
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-amber-300 to-orange-500 text-lg font-bold text-slate-950">
        ₿
      </span>
    );
  }
  if (method === "wechat") {
    return (
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#07C160] text-sm font-bold text-white">
        微
      </span>
    );
  }
  return (
    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#1677FF] text-sm font-bold text-white">
      支
    </span>
  );
}

function CheckoutContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { items, hydrated, clear, removeItem, setQuantity } = useCart();
  const [directItem, setDirectItem] = useState<CartItem | null>(null);
  const [directLoading, setDirectLoading] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [couponInput, setCouponInput] = useState("");
  const [coupon, setCoupon] = useState<CouponResult | null>(null);
  const [couponLoading, setCouponLoading] = useState(false);
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("wechat");
  const [cryptoEnabled, setCryptoEnabled] = useState(false);
  const [cryptoCoins, setCryptoCoins] = useState<string[]>([]);
  const [agreed, setAgreed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const directProductId = searchParams.get("product");
  const directVariantId = searchParams.get("variant");
  const directQty = Math.min(5, Math.max(1, Number(searchParams.get("qty") || 1)));

  useEffect(() => {
    api<BrandInfo>("/api/v1/brand")
      .then((data) => {
        setCryptoEnabled(data.crypto_enabled);
        setCryptoCoins(data.crypto_coins || []);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const savedEmail = getUserEmail();
    if (savedEmail) setEmail(savedEmail);
  }, []);

  useEffect(() => {
    if (!directProductId) {
      setDirectItem(null);
      return;
    }
    let cancelled = false;
    setDirectLoading(true);
    api<{ product: Product }>(`/api/v1/store/products/${directProductId}`)
      .then(({ product }) => {
        if (cancelled) return;
        const variants = product.variants;
        const selected =
          variants.find((item) => String(item.id) === String(directVariantId)) || variants[0] || null;
        setDirectItem({
          productId: product.id,
          slug: product.slug,
          name: product.name,
          subtitle: product.subtitle,
          variantId: selected?.id ?? null,
          specLabel: selected?.label ?? "",
          price: selected?.price ?? product.price,
          originalPrice: selected?.original_price ?? product.original_price,
          logoKey: product.logo_key,
          brand: product.brand,
          deliveryType: product.delivery_type,
          quantity: directQty,
        });
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => {
        if (!cancelled) setDirectLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [directProductId, directVariantId, directQty]);

  const displayItems = useMemo(() => (directItem ? [directItem] : items), [directItem, items]);
  const subtotal = useMemo(
    () => displayItems.reduce((sum, item) => sum + item.price * item.quantity, 0),
    [displayItems],
  );
  const discount = coupon?.discount || 0;
  const total = Math.max(0.01, subtotal - discount);
  const isLoading =
    (!hydrated && !directItem) ||
    directLoading ||
    (Boolean(directProductId) && !directItem && !error);

  function updateQuantity(productId: number, variantId: number | null, quantity: number) {
    setCoupon(null);
    setQuantity(productId, variantId, quantity);
  }

  function remove(productId: number, variantId: number | null) {
    setCoupon(null);
    removeItem(productId, variantId);
  }

  async function applyCoupon() {
    const code = couponInput.trim();
    if (!code) return;
    setCouponLoading(true);
    setError(null);
    try {
      const result = await api<CouponResult>("/api/v1/store/coupons/validate", {
        method: "POST",
        body: { code, amount: subtotal },
      });
      setCoupon(result);
    } catch (err) {
      setCoupon(null);
      setError((err as Error).message);
    } finally {
      setCouponLoading(false);
    }
  }

  async function submit() {
    setError(null);
    if (!EMAIL_RE.test(email.trim())) {
      setError("请填写正确的接收邮箱，订单与交付内容将关联到此账号");
      return;
    }
    if (password.length < 8) {
      setError("订单管理密码至少需要 8 位，用于之后登录查看订单");
      return;
    }
    if (!agreed) {
      setError("请先阅读并同意服务条款与售后政策");
      return;
    }
    if (!displayItems.length) {
      setError("订单中没有商品");
      return;
    }
    setSubmitting(true);
    try {
      const result = await api<OrderCreateResult>("/api/v1/store/orders", {
        method: "POST",
        body: {
          email: email.trim(),
          password,
          items: displayItems.map((item) => ({
            product_id: item.productId,
            variant_id: item.variantId,
            quantity: item.quantity,
          })),
          coupon_code: coupon?.code || undefined,
          payment_method: paymentMethod,
        },
      });
      setUserToken(result.user_token);
      setUserEmail(result.user_email);
      if (!directItem) clear();
      router.push(`/pay/${result.order.order_no}?provider=${paymentMethod}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  if (isLoading) {
    return (
      <div className="container-page py-16">
        <div className="skeleton h-10 w-56" />
        <div className="mt-8 grid gap-6 lg:grid-cols-[1.5fr_0.9fr]">
          <div className="space-y-4">
            <div className="surface h-48" />
            <div className="surface h-52" />
          </div>
          <div className="surface h-80" />
        </div>
      </div>
    );
  }

  if (!displayItems.length) {
    return (
      <div className="container-page flex flex-col items-center py-24 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] text-slate-500">
          <ShoppingBag size={24} />
        </div>
        <h1 className="mt-5 text-xl font-semibold text-slate-100">购物车还是空的</h1>
        <p className="mt-2 text-sm text-slate-500">挑选需要的服务，支付后即可自动交付。</p>
        <Link href="/products" className="btn-primary mt-6">去选购</Link>
      </div>
    );
  }

  return (
    <div className="container-page pb-16 pt-10">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">SECURE CHECKOUT</p>
          <h1 className="mt-3 font-display text-[36px] font-bold tracking-tight text-white">确认订单</h1>
          <p className="mt-3 text-sm text-slate-500">核对商品与交付信息，选择支付方式完成购买。</p>
        </div>
        <ol className="flex items-center gap-3 text-[10px] text-slate-600">
          <li className="flex items-center gap-2 text-brand-300"><span className="flex h-5 w-5 items-center justify-center rounded-full border border-brand-400/40 bg-brand-500/15">1</span>确认订单</li>
          <li className="h-px w-8 bg-slate-700" />
          <li className="flex items-center gap-2"><span className="flex h-5 w-5 items-center justify-center rounded-full border border-slate-700">2</span>完成支付</li>
          <li className="h-px w-8 bg-slate-700" />
          <li className="flex items-center gap-2"><span className="flex h-5 w-5 items-center justify-center rounded-full border border-slate-700">3</span>自动交付</li>
        </ol>
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1.55fr_0.9fr]">
        <div className="space-y-4">
          <section className="surface p-5 sm:p-6">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-100">商品信息</h2>
              <span className="text-[11px] text-slate-500">共 {displayItems.reduce((sum, item) => sum + item.quantity, 0)} 件商品</span>
            </div>
            <div className="mt-4 divide-y divide-white/[0.06]">
              {displayItems.map((item) => (
                <div key={`${item.productId}-${item.variantId}`} className="flex gap-4 py-4 first:pt-0 last:pb-0">
                  <BrandTile logoKey={item.logoKey} size={52} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium text-slate-100">{item.name}</p>
                        <p className="mt-1 text-[11px] text-slate-500">
                          {item.specLabel} · {item.deliveryType === "ACTIVATION_CODE" ? "MOLI 激活码 · 自动发货" : "账密直发 · 自动发货"}
                        </p>
                      </div>
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 items-center rounded-lg border border-white/10 bg-white/[0.025]">
                          <button
                            type="button"
                            onClick={() => updateQuantity(item.productId, item.variantId, item.quantity - 1)}
                            className="flex h-full w-8 items-center justify-center text-slate-500 hover:text-white"
                            aria-label="减少数量"
                          >
                            <Minus size={12} />
                          </button>
                          <span className="w-5 text-center text-[11px] text-slate-200">{item.quantity}</span>
                          <button
                            type="button"
                            onClick={() => updateQuantity(item.productId, item.variantId, item.quantity + 1)}
                            className="flex h-full w-8 items-center justify-center text-slate-500 hover:text-white"
                            aria-label="增加数量"
                          >
                            <Plus size={12} />
                          </button>
                        </div>
                        <span className="w-20 text-right text-sm font-medium text-slate-200">
                          {formatMoney(item.price * item.quantity)}
                        </span>
                        {!directItem && (
                          <button
                            type="button"
                            onClick={() => remove(item.productId, item.variantId)}
                            className="text-slate-600 transition hover:text-rose-300"
                            aria-label="移除商品"
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="surface p-5 sm:p-6">
            <h2 className="text-sm font-semibold text-slate-100">交付信息</h2>
            <div className="mt-5">
              <label htmlFor="email" className="mb-2 block text-[11px] text-slate-400">
                接收邮箱 / 登录账号 <span className="text-slate-600">（用于关联订单与交付内容）</span>
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@example.com"
                className="field"
                autoComplete="email"
              />
            </div>
            <div className="mt-5">
              <label htmlFor="order-password" className="mb-2 block text-[11px] text-slate-400">
                订单管理密码
              </label>
              <div className="relative">
                <KeyRound size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  id="order-password"
                  name="order_password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="至少 8 位，用于登录查看订单"
                  className="field pl-10 pr-11"
                  autoComplete="current-password"
                  minLength={8}
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
              <p className="mt-2 text-[10px] leading-5 text-slate-600">
                首次下单会自动创建账户；已有账户请填写原密码。之后使用该邮箱和密码登录「我的订单」。
              </p>
            </div>
            <div className="mt-5">
              <label htmlFor="coupon" className="mb-2 block text-[11px] text-slate-400">优惠码</label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Tag size={14} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input
                    id="coupon"
                    value={couponInput}
                    onChange={(event) => setCouponInput(event.target.value)}
                    placeholder="输入优惠码（选填）"
                    className="field pl-10"
                  />
                </div>
                <button
                  type="button"
                  onClick={applyCoupon}
                  disabled={couponLoading || !couponInput.trim()}
                  className="btn-secondary px-5"
                >
                  {couponLoading ? "验证中" : "使用"}
                </button>
              </div>
              {coupon && (
                <p className="mt-2 flex items-center gap-1.5 text-[11px] text-emerald-300">
                  <Check size={12} /> {coupon.message}
                </p>
              )}
            </div>
          </section>

          <section className="surface p-5 sm:p-6">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-100">支付方式</h2>
              <span className="text-[10px] text-slate-600">支持微信 / 支付宝{cryptoEnabled ? " / 加密货币" : ""}</span>
            </div>
            <div className={`mt-4 grid gap-3 ${cryptoEnabled ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}>
              {(["wechat", "alipay", ...(cryptoEnabled ? (["crypto"] as const) : [])] as PaymentMethod[]).map((method) => (
                <button
                  key={method}
                  type="button"
                  onClick={() => setPaymentMethod(method)}
                  className={`flex items-center gap-3 rounded-xl border p-4 text-left transition ${
                    paymentMethod === method
                      ? "border-brand-400/50 bg-brand-500/[0.09]"
                      : "border-white/[0.09] bg-white/[0.02] hover:border-white/20"
                  }`}
                >
                  <PaymentMark method={method} />
                  <span className="flex-1">
                    <span className="block text-xs font-medium text-slate-200">
                      {method === "wechat" ? "微信支付" : method === "alipay" ? "支付宝支付" : "加密货币支付"}
                    </span>
                    <span className="mt-1 block text-[10px] text-slate-500">
                      {method === "wechat" ? "使用微信扫码支付" : method === "alipay" ? "使用支付宝扫码支付" : (cryptoCoins.length ? cryptoCoins.join(" · ") : "USDT · USDC · BTC · ETH")}
                    </span>
                  </span>
                  <span className={`flex h-4 w-4 items-center justify-center rounded-full border ${paymentMethod === method ? "border-brand-400 bg-brand-500" : "border-slate-600"}`}>
                    {paymentMethod === method && <Check size={10} className="text-white" />}
                  </span>
                </button>
              ))}
            </div>
          </section>
        </div>

        {/* Summary */}
        <aside className="sticky-summary">
          <div className="surface p-5 sm:p-6">
            <h2 className="text-sm font-semibold text-slate-100">订单摘要</h2>
            <div className="mt-5 space-y-3 text-xs">
              <div className="flex justify-between text-slate-500">
                <span>商品金额</span>
                <span className="text-slate-300">{formatMoney(subtotal)}</span>
              </div>
              <div className="flex justify-between text-slate-500">
                <span>优惠金额</span>
                <span className={discount ? "text-emerald-300" : "text-slate-400"}>-{formatMoney(discount)}</span>
              </div>
              <div className="flex justify-between text-slate-500">
                <span>交付方式</span>
                <span className="text-slate-300">自动发货</span>
              </div>
            </div>
            <div className="mt-5 flex items-end justify-between border-t border-white/[0.08] pt-5">
              <span className="text-xs text-slate-400">实付款</span>
              <span className="price text-[30px]">{formatMoney(total)}</span>
            </div>

            {error && (
              <p className="mt-4 rounded-lg border border-rose-400/20 bg-rose-400/10 px-3 py-2.5 text-[11px] leading-5 text-rose-200">
                {error}
              </p>
            )}

            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              className="btn-primary mt-5 w-full"
            >
              {submitting ? "正在创建订单..." : `立即支付 ${formatMoney(total)}`}
              {!submitting && <ArrowRight size={16} />}
            </button>

            <label className="mt-4 flex items-start gap-2 text-[10px] leading-5 text-slate-600">
              <input
                type="checkbox"
                checked={agreed}
                onChange={(event) => setAgreed(event.target.checked)}
                className="mt-1 h-3.5 w-3.5 accent-blue-500"
              />
              <span>
                我已阅读并同意
                <Link href="/help#terms" className="text-slate-400 hover:text-brand-300">《服务条款》</Link>
                与
                <Link href="/help#warranty" className="text-slate-400 hover:text-brand-300">《售后政策》</Link>
              </span>
            </label>
          </div>

          <div className="mt-3 flex items-start gap-2 rounded-xl border border-brand-400/15 bg-brand-500/[0.055] px-4 py-3 text-[10px] leading-5 text-slate-500">
            <ShieldCheck size={14} className="mt-0.5 shrink-0 text-brand-300" />
            支付成功后，卡密或数字商品将自动发放至订单详情；使用下单邮箱和密码即可登录「我的订单」查看。
          </div>

          <Link href="/products" className="mt-4 inline-flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-300">
            <ArrowLeft size={12} />
            继续选购
          </Link>
        </aside>
      </div>
    </div>
  );
}

export default function CheckoutPage() {
  return (
    <Suspense
      fallback={
        <div className="container-page py-16">
          <div className="skeleton h-10 w-56" />
          <div className="mt-8 grid gap-6 lg:grid-cols-[1.5fr_0.9fr]">
            <div className="surface h-64" />
            <div className="surface h-80" />
          </div>
        </div>
      }
    >
      <CheckoutContent />
    </Suspense>
  );
}
