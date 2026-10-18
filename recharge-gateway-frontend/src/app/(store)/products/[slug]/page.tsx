"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronDown,
  Headphones,
  Minus,
  Plus,
  ShieldCheck,
  ShoppingBag,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { BrandTile } from "@/components/BrandTile";
import { useCart } from "@/lib/cart";
import { formatMoney, formatNumber, splitDescription } from "@/lib/format";
import { api } from "@/lib/api";
import type { Product } from "@/lib/types";

const FEATURE_ICONS = {
  bolt: Zap,
  pulse: Activity,
  shield: ShieldCheck,
  headset: Headphones,
} as const;

const TABS = [
  { key: "intro", label: "商品介绍" },
  { key: "usage", label: "使用流程" },
  { key: "faq", label: "常见问题" },
  { key: "warranty", label: "售后保障" },
] as const;

type ProductResponse = { product: Product };

export default function ProductDetailPage() {
  const params = useParams<{ slug: string }>();
  const router = useRouter();
  const { addItem } = useCart();
  const [product, setProduct] = useState<Product | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [variantId, setVariantId] = useState<number | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("intro");
  const [openFaq, setOpenFaq] = useState<number | null>(0);

  const load = useCallback(() => {
    if (!params?.slug) return;
    setLoading(true);
    setError(null);
    api<ProductResponse>(`/api/v1/store/products/${encodeURIComponent(params.slug)}`)
      .then((data) => {
        setProduct(data.product);
        setVariantId(data.product.variants[0]?.id ?? null);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [params?.slug]);

  useEffect(() => {
    load();
  }, [load]);

  const variant = useMemo(
    () => product?.variants.find((item) => item.id === variantId) || product?.variants[0] || null,
    [product, variantId],
  );

  const currentPrice = variant?.price ?? product?.price ?? 0;
  const originalPrice = variant?.original_price ?? product?.original_price ?? null;
  const outOfStock = product?.stock !== null && product?.stock !== undefined && product.stock <= 0;

  function cartPayload() {
    if (!product) return null;
    return {
      productId: product.id,
      slug: product.slug,
      name: product.name,
      subtitle: product.subtitle,
      variantId: variant?.id ?? null,
      specLabel: variant?.label ?? "",
      price: currentPrice,
      originalPrice,
      logoKey: product.logo_key,
      brand: product.brand,
      deliveryType: product.delivery_type,
    };
  }

  function addToCart() {
    const payload = cartPayload();
    if (!payload) return;
    addItem(payload, quantity);
  }

  function buyNow() {
    if (!product) return;
    if (outOfStock) {
      router.push("/help#contact");
      return;
    }
    const search = new URLSearchParams({
      product: product.slug,
      qty: String(quantity),
    });
    if (variant?.id) search.set("variant", String(variant.id));
    router.push(`/checkout?${search.toString()}`);
  }

  if (loading) {
    return (
      <div className="container-page py-16">
        <div className="grid gap-10 lg:grid-cols-2">
          <div className="surface aspect-square animate-pulse" />
          <div>
            <div className="skeleton h-4 w-32" />
            <div className="skeleton mt-6 h-12 w-80" />
            <div className="skeleton mt-3 h-6 w-56" />
            <div className="skeleton mt-8 h-10 w-40" />
            <div className="skeleton mt-8 h-12 w-full" />
            <div className="skeleton mt-3 h-12 w-full" />
          </div>
        </div>
      </div>
    );
  }

  if (error || !product) {
    return (
      <div className="container-page flex flex-col items-center py-28 text-center">
        <p className="text-sm text-slate-400">{error || "商品不存在或已下架"}</p>
        <div className="mt-5 flex gap-3">
          <button type="button" onClick={load} className="btn-soft">重新加载</button>
          <Link href="/products" className="btn-secondary">返回商品列表</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="container-page pb-16 pt-6">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-600">
        <Link href="/" className="hover:text-slate-300">首页</Link>
        <span>/</span>
        <Link href={`/products?category=${product.category}`} className="hover:text-slate-300">
          {product.category_label}
        </Link>
        <span>/</span>
        <span>{product.brand}</span>
        <span>/</span>
        <span className="text-slate-400">{product.name}</span>
      </div>

      <div className="mt-6 grid gap-8 lg:grid-cols-[1.02fr_0.98fr] lg:gap-12">
        {/* Visual */}
        <div className="relative overflow-hidden rounded-3xl border border-white/[0.07] bg-gradient-to-b from-[#0a1427] to-[#060c17] p-6 sm:p-8">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_40%,rgba(37,99,235,0.18),transparent_58%)]" />
          <div className="relative flex min-h-[390px] flex-col items-center justify-center sm:min-h-[480px]">
            <div className="absolute h-[300px] w-[300px] rounded-full border border-brand-400/10 sm:h-[380px] sm:w-[380px]" />
            <div className="absolute h-[230px] w-[380px] rounded-[50%] border border-brand-400/20 sm:h-[300px] sm:w-[480px]" />
            <div className="absolute h-[370px] w-[370px] animate-spin-slow rounded-full border border-dashed border-cyan-200/[0.08]" />
            <div className="absolute h-56 w-56 rounded-full bg-brand-500/15 blur-3xl" />
            <BrandTile logoKey={product.logo_key} size={180} className="relative animate-float" />
            <h2 className="relative mt-8 font-display text-[30px] font-semibold text-white">{product.name}</h2>
            <p className="relative mt-2 text-[10px] uppercase tracking-[0.32em] text-slate-500">
              {product.brand} · {variant?.label || "DIGITAL SUPPLY"}
            </p>
          </div>

          <div className="relative mt-2 flex items-center justify-center gap-2">
            {product.variants.map((item, index) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setVariantId(item.id)}
                className={`flex h-8 w-8 items-center justify-center rounded-lg border text-[11px] transition ${
                  variant?.id === item.id
                    ? "border-brand-400/40 bg-brand-500/15 text-brand-200"
                    : "border-white/10 bg-white/[0.025] text-slate-500 hover:text-slate-200"
                }`}
                aria-label={`选择规格 ${item.label}`}
              >
                {index + 1}
              </button>
            ))}
          </div>

          <div className="relative mt-5 grid grid-cols-3 gap-2 text-center text-[10px] text-slate-500">
            <span className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2 py-2.5">24H 自动交付</span>
            <span className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2 py-2.5">状态可追踪</span>
            <span className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-2 py-2.5">专业售后</span>
          </div>
        </div>

        {/* Purchase panel */}
        <div className="flex flex-col">
          <div className="flex flex-wrap items-center gap-2">
            <span className="eyebrow">AI MEMBERSHIP / INSTANT DELIVERY</span>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {product.badge && <span className="badge-soft">{product.badge}</span>}
            {product.tags.slice(0, 3).map((tag) => (
              <span key={tag} className="badge">{tag}</span>
            ))}
          </div>

          <h1 className="mt-5 font-display text-[34px] font-bold leading-tight tracking-tight text-white sm:text-[42px]">
            {product.name}
            <br />
            <span className="text-slate-200">{variant?.label || product.subtitle}</span>
          </h1>
          <p className="mt-3 text-sm text-slate-500">{product.subtitle}</p>

          <div className="mt-6 flex items-end justify-between gap-4 border-b border-white/[0.07] pb-6">
            <div className="flex items-baseline gap-3">
              <span className="price text-[34px]">{formatMoney(currentPrice)}</span>
              {originalPrice && (
                <span className="text-sm text-slate-600 line-through">{formatMoney(originalPrice)}</span>
              )}
            </div>
            <span className="pb-1 text-[11px] text-slate-600">已售 {formatNumber(product.sold_count)}+</span>
          </div>

          <div className="mt-6">
            <p className="mb-3 text-xs font-medium text-slate-400">选择服务规格</p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {product.variants.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setVariantId(item.id)}
                  className={`rounded-xl border px-4 py-3 text-left transition ${
                    variant?.id === item.id
                      ? "border-brand-400/45 bg-brand-500/[0.12] shadow-[inset_0_0_0_1px_rgba(96,165,250,0.12)]"
                      : "border-white/[0.09] bg-white/[0.02] hover:border-white/20"
                  }`}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-slate-200">{item.label}</span>
                    <span className="text-[10px] text-slate-500">{formatMoney(item.price, 0)}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="mt-6">
            <p className="mb-3 text-xs font-medium text-slate-400">交付形式</p>
            <div className="flex flex-wrap gap-2">
              <span className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[11px] text-slate-300">
                <Check size={12} className="text-emerald-300" />
                {product.delivery_label}
              </span>
              <span className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-[11px] text-slate-300">
                <Check size={12} className="text-emerald-300" />
                {product.delivery_type === "ACTIVATION_CODE" ? "免密自助充能" : "账密直发"}
              </span>
              {outOfStock && (
                <span className="inline-flex items-center gap-2 rounded-lg border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-300">
                  暂时缺货
                </span>
              )}
            </div>
          </div>

          <div className="mt-7 flex items-center gap-3">
            <div className="flex h-12 items-center rounded-xl border border-white/10 bg-white/[0.025]">
              <button
                type="button"
                onClick={() => setQuantity((value) => Math.max(1, value - 1))}
                className="flex h-full w-10 items-center justify-center text-slate-500 hover:text-white"
                aria-label="减少数量"
              >
                <Minus size={14} />
              </button>
              <span className="w-6 text-center text-xs text-slate-200">{quantity}</span>
              <button
                type="button"
                onClick={() => setQuantity((value) => Math.min(5, value + 1))}
                className="flex h-full w-10 items-center justify-center text-slate-500 hover:text-white"
                aria-label="增加数量"
              >
                <Plus size={14} />
              </button>
            </div>
            <span className="text-[11px] text-slate-600">单笔最多 5 件</span>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-[1.4fr_1fr]">
            <button type="button" onClick={buyNow} disabled={outOfStock} className="btn-primary w-full">
              {outOfStock ? "暂时缺货 · 咨询客服" : "立即购买"}
              {!outOfStock && <ArrowUpRight size={16} />}
            </button>
            <button type="button" onClick={addToCart} disabled={outOfStock} className="btn-secondary w-full">
              <ShoppingBag size={15} />
              加入购物车
            </button>
          </div>

          <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-[10px] text-slate-600">
            <span className="inline-flex items-center gap-1.5"><Zap size={11} /> 24 小时自动发货</span>
            <span className="inline-flex items-center gap-1.5"><ShieldCheck size={11} /> 专业售后</span>
            <span className="inline-flex items-center gap-1.5"><Activity size={11} /> 状态可追踪</span>
          </div>

          <div className="mt-6 rounded-xl border border-white/[0.07] bg-white/[0.025] px-4 py-3.5 text-[11px] leading-5 text-slate-500">
            {product.delivery_note}。{product.warranty}
          </div>
        </div>
      </div>

      {/* Detail tabs */}
      <div className="mt-12 overflow-hidden rounded-2xl border border-white/[0.07] bg-[#080e1a]/70">
        <div className="scrollbar-thin flex overflow-x-auto border-b border-white/[0.07] px-4">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setTab(item.key)}
              className={`relative shrink-0 px-4 py-4 text-xs transition ${
                tab === item.key ? "text-slate-100" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {item.label}
              {tab === item.key && (
                <span className="absolute inset-x-4 bottom-0 h-0.5 rounded-full bg-brand-500" />
              )}
            </button>
          ))}
        </div>

        <div className="p-5 sm:p-7">
          {tab === "intro" && (
            <div className="grid gap-8 lg:grid-cols-[1.1fr_0.9fr]">
              <div>
                <h3 className="text-sm font-semibold text-slate-200">产品说明</h3>
                <div className="mt-4 space-y-4 text-xs leading-7 text-slate-500">
                  {splitDescription(product.description).map((paragraph) => (
                    <p key={paragraph}>{paragraph}</p>
                  ))}
                </div>
                <div className="mt-5 flex flex-wrap gap-2">
                  {product.tags.map((tag) => (
                    <span key={tag} className="badge">{tag}</span>
                  ))}
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {product.features.map((feature, index) => {
                  const Icon = FEATURE_ICONS[(feature.icon || "bolt") as keyof typeof FEATURE_ICONS] || Zap;
                  return (
                    <div key={`${feature.title}-${index}`} className="rounded-xl border border-white/[0.06] bg-white/[0.025] p-4">
                      <Icon size={16} className="text-brand-300" />
                      <p className="mt-3 text-xs font-medium text-slate-200">{feature.title}</p>
                      <p className="mt-1.5 text-[10px] leading-5 text-slate-500">{feature.desc}</p>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {tab === "usage" && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
              {product.usage_steps.map((step, index) => (
                <div key={step} className="relative rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <span className="font-mono text-[10px] text-brand-300">STEP 0{index + 1}</span>
                  <p className="mt-3 text-[11px] leading-6 text-slate-400">{step}</p>
                </div>
              ))}
            </div>
          )}

          {tab === "faq" && (
            <div className="grid gap-2 lg:grid-cols-2">
              {product.faq.map((item, index) => (
                <div key={item.q} className="rounded-xl border border-white/[0.06] bg-white/[0.02]">
                  <button
                    type="button"
                    onClick={() => setOpenFaq(openFaq === index ? null : index)}
                    className="flex w-full items-center justify-between gap-4 px-4 py-3.5 text-left"
                  >
                    <span className="text-xs text-slate-300">{item.q}</span>
                    <ChevronDown
                      size={14}
                      className={`shrink-0 text-slate-500 transition ${openFaq === index ? "rotate-180" : ""}`}
                    />
                  </button>
                  {openFaq === index && (
                    <p className="border-t border-white/[0.05] px-4 py-3.5 text-[11px] leading-6 text-slate-500">
                      {item.a}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}

          {tab === "warranty" && (
            <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
                <h3 className="text-sm font-semibold text-slate-200">售后保障说明</h3>
                <p className="mt-3 text-xs leading-7 text-slate-500">{product.warranty}</p>
                <p className="mt-2 text-xs leading-7 text-slate-500">
                  {product.delivery_type === "ACTIVATION_CODE"
                    ? "本商品为 MOLI 代充激活码，保障充值到账；封号、自行修改账号安全设置或共享账号不在质保范围。"
                    : "本商品为独立成品号，不是代充；交付后请先登录确认。30 天内非买家原因出现掉订阅或封号，可按未使用天数补发或折算退款。"}
                </p>
                <p className="mt-2 text-xs leading-7 text-slate-500">
                  以下情况不在售后范围：自行修改密码、2FA、换绑邮箱/辅助邮箱、共享账号或频繁切换地区/网络；
                  官方服务器容量波动、模型暂不可用、地区限制、手机验证等非本站交付问题；违反服务商条款或法律法规导致的限制。
                </p>
                <p className="mt-2 text-xs leading-7 text-slate-500">
                  售后请提供订单编号、问题描述与截图。按剩余天数折算示例：30 天质保、使用 8 天，剩余价值 = 订单金额 × (30-8)/30，支付通道已产生费用不退。
                </p>
                <Link href="/help#warranty" className="btn-soft mt-5">
                  查看完整售后政策
                  <ArrowUpRight size={13} />
                </Link>
                <Link href="/help#contact" className="btn-soft mt-2">
                  联系售后
                  <ArrowUpRight size={13} />
                </Link>
              </div>
              <div className="grid gap-3">
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <p className="text-xs font-medium text-slate-200">交付记录可查</p>
                  <p className="mt-1.5 text-[10px] leading-5 text-slate-500">订单详情保留每一份交付内容与核销状态。</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <p className="text-xs font-medium text-slate-200">失败自动回退</p>
                  <p className="mt-1.5 text-[10px] leading-5 text-slate-500">可恢复的充值失败会自动释放激活码，可安全重试。</p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="mt-6 flex items-center justify-between">
        <Link href="/products" className="link-arrow">
          <ArrowLeft size={13} />
          返回商品列表
        </Link>
        <Link href="/redeem" className="link-arrow">
          前往自助充能中心
          <ArrowUpRight size={13} />
        </Link>
      </div>
    </div>
  );
}
