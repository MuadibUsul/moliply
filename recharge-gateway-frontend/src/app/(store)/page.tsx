"use client";

import Link from "next/link";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  Gem,
  Headphones,
  Layers3,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { BrandTile } from "@/components/BrandTile";
import { ProductCard } from "@/components/ProductCard";
import { ASSURANCES, BRAND, CATEGORY_CARDS } from "@/config/brand";
import { api } from "@/lib/api";
import type { Product, ProductsResponse } from "@/lib/types";

const CATEGORY_ICONS = {
  sparkles: Sparkles,
  layers: Layers3,
  gem: Gem,
  zap: Zap,
} as const;

const TONE_CLASSES: Record<string, { icon: string; glow: string }> = {
  blue: { icon: "text-blue-300 bg-blue-500/10 border-blue-400/20", glow: "bg-blue-500/10" },
  violet: { icon: "text-violet-300 bg-violet-500/10 border-violet-400/20", glow: "bg-violet-500/10" },
  amber: { icon: "text-amber-300 bg-amber-500/10 border-amber-400/20", glow: "bg-amber-500/10" },
  cyan: { icon: "text-cyan-300 bg-cyan-500/10 border-cyan-400/20", glow: "bg-cyan-500/10" },
};

const ASSURANCE_ICONS = {
  bolt: Zap,
  shield: ShieldCheck,
  pulse: Activity,
  headset: Headphones,
} as const;

export default function HomePage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api<ProductsResponse>("/api/v1/store/products?sort=popular&page_size=5")
      .then((data) => setProducts(data.items))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="overflow-hidden">
      {/* Hero */}
      <section className="relative">
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute left-1/2 top-0 h-[560px] w-[1100px] -translate-x-1/2 rounded-full bg-brand-600/[0.12] blur-[120px]" />
          <div className="grid-lines absolute inset-0 opacity-40" />
        </div>

        <div className="container-page relative grid items-center gap-10 pb-10 pt-14 lg:grid-cols-[1.05fr_0.95fr] lg:pb-16 lg:pt-20">
          <div className="relative z-10 animate-fade-up">
            <div className="eyebrow">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-300 shadow-[0_0_12px_rgba(103,232,249,0.9)]" />
              YOUR DIGITAL MEMBERSHIP HUB
              <span className="h-px w-8 bg-slate-600/70" />
              24/7
            </div>
            <h1 className="mt-6 font-display text-[40px] font-bold leading-[1.14] tracking-tight sm:text-[58px]">
              <span className="text-slate-50">让优质数字服务，</span>
              <br />
              <span className="text-gradient-blue">触手可及。</span>
            </h1>
            <p className="mt-6 text-sm font-medium tracking-wide text-slate-300 sm:text-base">
              {BRAND.subtitle}
            </p>
            <p className="mt-3 max-w-xl text-sm leading-7 text-slate-500">
              全球数字订阅与生产力工具，一站式选购。
              <br className="hidden sm:block" />
              从下单到交付，简单、透明、快捷。
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/products" className="btn-primary group">
                立即选购
                <ArrowUpRight size={16} className="transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
              </Link>
              <Link href="/redeem" className="btn-secondary group">
                进入自助充能中心
                <ArrowRight size={15} className="transition group-hover:translate-x-0.5" />
              </Link>
            </div>

            <div className="mt-9 grid max-w-xl grid-cols-2 gap-x-6 gap-y-3 text-[11px] text-slate-500 sm:flex sm:flex-wrap sm:gap-6">
              {ASSURANCES.map((item) => {
                const Icon = ASSURANCE_ICONS[item.icon as keyof typeof ASSURANCE_ICONS] || ShieldCheck;
                return (
                  <span key={item.title} className="inline-flex items-center gap-2">
                    <Icon size={13} className="text-slate-500" />
                    {item.title}
                  </span>
                );
              })}
            </div>
          </div>

          <div className="relative z-0 mx-auto w-full max-w-[600px] scale-[0.86] sm:scale-100">
            <div className="relative h-[390px] sm:h-[460px]">
              <div className="absolute inset-x-[4%] top-[18%] h-[58%] rounded-[50%] border border-brand-400/10 bg-[radial-gradient(ellipse_at_center,rgba(37,99,235,0.18),transparent_68%)]" />
              <div className="absolute left-1/2 top-[52%] h-[260px] w-[480px] -translate-x-1/2 -translate-y-1/2 rounded-[50%] border border-brand-400/15" />
              <div className="absolute left-1/2 top-[52%] h-[330px] w-[540px] -translate-x-1/2 -translate-y-1/2 rounded-[50%] border border-white/[0.045]" />
              <div className="absolute left-1/2 top-[52%] h-[410px] w-[580px] -translate-x-1/2 -translate-y-1/2 animate-spin-slow rounded-[50%] border border-dashed border-cyan-300/[0.09]" />
              <div className="absolute left-1/2 top-1/2 h-[440px] w-[440px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand-500/[0.045] blur-3xl" />

              <BrandTile
                logoKey="gpt"
                size={122}
                className="absolute left-[14%] top-[17%] animate-float"
                style={{ animationDelay: "0.4s" }}
              />
              <BrandTile
                logoKey="claude"
                size={100}
                className="absolute right-[15%] top-[6%] animate-float-slow"
              />
              <BrandTile
                logoKey="cursor"
                size={94}
                className="absolute left-[6%] top-[51%] animate-float-slow"
                style={{ animationDelay: "1.2s" }}
              />
              <BrandTile
                logoKey="midjourney"
                size={106}
                className="absolute right-[6%] top-[48%] animate-float"
                style={{ animationDelay: "0.8s" }}
              />

              <div className="absolute bottom-[8%] left-1/2 h-[86px] w-[82%] -translate-x-1/2 rounded-[50%] border border-brand-400/25 bg-[radial-gradient(ellipse_at_center,rgba(37,99,235,0.34),rgba(8,13,23,0.55)_62%,transparent_74%)] shadow-[0_30px_80px_rgba(37,99,235,0.28)]" />
              <div className="absolute bottom-[13%] left-1/2 flex -translate-x-1/2 flex-col items-center">
                <span className="font-display text-[22px] font-semibold tracking-[0.22em] text-white">
                  MOLIPLY
                </span>
                <span className="mt-1 text-[8px] tracking-[0.46em] text-brand-300/70">DIGITAL SUPPLY</span>
              </div>
              <div className="absolute left-[30%] top-[34%] h-px w-[220px] rotate-[18deg] bg-gradient-to-r from-transparent via-cyan-300/20 to-transparent" />
              <div className="absolute right-[28%] top-[40%] h-px w-[190px] -rotate-[24deg] bg-gradient-to-r from-transparent via-brand-300/20 to-transparent" />
            </div>
          </div>
        </div>
      </section>

      {/* Category cards */}
      <section className="container-page section-space pt-6">
        <div className="mb-7 flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow">EXPLORE BY CATEGORY</p>
            <h2 className="section-heading mt-2.5">探索数字服务</h2>
          </div>
          <Link href="/products" className="link-arrow hidden sm:inline-flex">
            查看全部分类
            <ArrowUpRight size={13} />
          </Link>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {CATEGORY_CARDS.map((category) => {
            const Icon = CATEGORY_ICONS[category.icon as keyof typeof CATEGORY_ICONS] || Sparkles;
            const tone = TONE_CLASSES[category.tone] || TONE_CLASSES.blue;
            return (
              <Link
                key={category.key}
                href={category.href}
                className="surface surface-hover group relative overflow-hidden p-6"
              >
                <div className={`pointer-events-none absolute -right-8 -top-10 h-32 w-32 rounded-full blur-3xl ${tone.glow}`} />
                <span className={`flex h-11 w-11 items-center justify-center rounded-xl border ${tone.icon}`}>
                  <Icon size={20} strokeWidth={1.9} />
                </span>
                <h3 className="mt-5 text-[15px] font-semibold text-slate-100">{category.title}</h3>
                <p className="mt-1.5 text-[11px] text-slate-500">{category.subtitle}</p>
                <span className="mt-5 inline-flex items-center gap-1 text-[11px] text-slate-500 transition group-hover:text-brand-300">
                  {category.key === "REDEEM" ? "立即前往" : "探索产品"}
                  <ArrowUpRight size={12} />
                </span>
              </Link>
            );
          })}
        </div>
      </section>

      {/* Hot products */}
      <section className="container-page section-space pt-2">
        <div className="mb-7 flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow">HANDPICKED FOR YOU</p>
            <div className="mt-2.5 flex items-baseline gap-3">
              <h2 className="section-heading">热门补给</h2>
              <span className="hidden text-xs text-slate-500 sm:inline">精选数字权益</span>
            </div>
          </div>
          <Link href="/products" className="link-arrow">
            浏览全部商品
            <ArrowUpRight size={13} />
          </Link>
        </div>

        {error ? (
          <div className="surface flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
            <p className="text-sm text-slate-400">商品列表暂时没有加载出来：{error}</p>
            <button type="button" onClick={load} className="btn-soft">重新加载</button>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {loading
              ? Array.from({ length: 5 }).map((_, index) => (
                  <div key={index} className="surface min-h-[210px] p-5">
                    <div className="skeleton h-14 w-14" />
                    <div className="skeleton mt-5 h-4 w-28" />
                    <div className="skeleton mt-2 h-3 w-36" />
                    <div className="skeleton mt-8 h-6 w-20" />
                  </div>
                ))
              : products.map((product) => <ProductCard key={product.id} product={product} />)}
          </div>
        )}
      </section>

      {/* Value section */}
      <section className="container-page section-space">
        <div className="surface relative overflow-hidden px-6 py-9 sm:px-10 sm:py-12">
          <div className="pointer-events-none absolute -left-20 top-0 h-64 w-64 rounded-full bg-brand-600/10 blur-3xl" />
          <div className="pointer-events-none absolute -right-16 bottom-0 h-64 w-64 rounded-full bg-cyan-500/[0.07] blur-3xl" />
          <div className="relative grid gap-10 lg:grid-cols-[0.95fr_1.05fr] lg:items-center">
            <div>
              <p className="eyebrow">BUILT FOR CONTINUITY</p>
              <h2 className="mt-3 max-w-md font-display text-[30px] font-semibold leading-tight tracking-tight text-slate-50 sm:text-[36px]">
                一个入口，
                <br />
                补给所有生产力。
              </h2>
              <p className="mt-5 max-w-md text-sm leading-7 text-slate-500">
                从 AI 会员到开发工具，MOLIPLY 把分散的订阅与交付流程收拢到同一个 24 小时在线入口。
                支付后自动发货，激活码与充能进度全程可查，售后有据可依。
              </p>
              <div className="mt-7 flex flex-wrap gap-3">
                <Link href="/products" className="btn-primary">开始补给</Link>
                <Link href="/help" className="btn-secondary">查看帮助</Link>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {ASSURANCES.map((item, index) => {
                const Icon = ASSURANCE_ICONS[item.icon as keyof typeof ASSURANCE_ICONS] || ShieldCheck;
                return (
                  <div key={item.title} className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-5">
                    <div className="flex items-center justify-between">
                      <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-brand-400/15 bg-brand-500/[0.08] text-brand-300">
                        <Icon size={16} />
                      </span>
                      <span className="font-mono text-[10px] text-slate-700">0{index + 1}</span>
                    </div>
                    <h3 className="mt-4 text-[13px] font-semibold text-slate-200">{item.title}</h3>
                    <p className="mt-1.5 text-[11px] leading-5 text-slate-500">{item.desc}</p>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="container-page pb-16">
        <div className="relative overflow-hidden rounded-3xl border border-brand-400/20 bg-gradient-to-br from-[#0b1a38] via-[#0a1328] to-[#07101f] px-6 py-10 text-center sm:px-12 sm:py-14">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(37,99,235,0.35),transparent_58%)]" />
          <div className="pointer-events-none absolute left-1/2 top-0 h-px w-2/3 -translate-x-1/2 bg-gradient-to-r from-transparent via-brand-300/50 to-transparent" />
          <div className="relative">
            <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl border border-brand-400/25 bg-brand-500/15 text-brand-200 shadow-glow-sm">
              <CheckCircle2 size={20} />
            </div>
            <h2 className="mt-5 font-display text-[26px] font-semibold tracking-tight text-white sm:text-[34px]">
              让每一次续费，都更简单。
            </h2>
            <p className="mx-auto mt-3 max-w-xl text-sm text-slate-400">
              24 小时自动交付，激活码免密核销。选好服务，剩下的交给 MOLIPLY。
            </p>
            <div className="mt-7 flex flex-wrap justify-center gap-3">
              <Link href="/products" className="btn-primary">
                立即选购
                <ArrowUpRight size={15} />
              </Link>
              <Link href="/redeem" className="btn-secondary">我有激活码</Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
