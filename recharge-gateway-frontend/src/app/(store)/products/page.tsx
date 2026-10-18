"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ArrowRight,
  ArrowUpRight,
  Braces,
  ChevronDown,
  Code2,
  Gem,
  HelpCircle,
  Layers3,
  Palette,
  Search,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ProductCard } from "@/components/ProductCard";
import { api, queryString } from "@/lib/api";
import type { ProductsResponse } from "@/lib/types";

const TABS = [
  { key: "all", label: "全部" },
  { key: "ai", label: "AI 会员" },
  { key: "accounts", label: "独享成品号" },
  { key: "software", label: "软件订阅" },
  { key: "developer", label: "开发工具" },
  { key: "api", label: "API 额度" },
] as const;

const CATEGORY_ICONS: Record<string, typeof Sparkles> = {
  AI_MEMBERSHIP: Sparkles,
  SOFTWARE: Layers3,
  DEVELOPER: Code2,
  DESIGN: Palette,
  DIGITAL: Gem,
  API: Braces,
};

function ProductsContent() {
  const searchParams = useSearchParams();
  const initialCategory = searchParams.get("category") || "";
  const initialQuery = searchParams.get("q") || "";
  const [category, setCategory] = useState(initialCategory);
  const [delivery, setDelivery] = useState("");
  const [tab, setTab] = useState("all");
  const [query, setQuery] = useState(initialQuery);
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery);
  const [sort, setSort] = useState("default");
  const [data, setData] = useState<ProductsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setCategory(initialCategory);
    setQuery(initialQuery);
    setSubmittedQuery(initialQuery);
  }, [initialCategory, initialQuery]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api<ProductsResponse>(
      `/api/v1/store/products${queryString({
        category: category || undefined,
        delivery: delivery || undefined,
        q: submittedQuery || undefined,
        sort,
        page_size: 24,
      })}`,
    )
      .then(setData)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [category, delivery, submittedQuery, sort]);

  useEffect(() => {
    load();
  }, [load]);

  function selectTab(key: string) {
    setTab(key);
    if (key === "all") {
      setCategory("");
      setDelivery("");
    } else if (key === "accounts") {
      setCategory("");
      setDelivery("ACCOUNT_CREDENTIALS");
    } else {
      const categoryKey =
        key === "ai" ? "AI_MEMBERSHIP" : key === "software" ? "SOFTWARE" : key === "developer" ? "DEVELOPER" : "API";
      setCategory(categoryKey);
      setDelivery("");
    }
  }

  function selectCategory(key: string) {
    setCategory(key);
    setTab("all");
    setDelivery("");
  }

  const activeTab = tab;
  const products = data?.items || [];

  return (
    <div className="relative overflow-hidden">
      <div className="pointer-events-none absolute left-1/2 top-0 h-[420px] w-[1000px] -translate-x-1/2 rounded-full bg-brand-600/[0.09] blur-[120px]" />
      <div className="container-page relative pb-16 pt-12">
        <div className="max-w-2xl">
          <p className="eyebrow">MOLIPLY STORE</p>
          <h1 className="mt-3 font-display text-[36px] font-bold tracking-tight text-white sm:text-[48px]">
            探索所有数字服务<span className="text-brand-400">.</span>
          </h1>
          <p className="mt-4 text-sm text-slate-500">找到适合你的 AI 工具、会员订阅与数字权益。</p>
        </div>

        <div className="mt-10 grid gap-6 lg:grid-cols-[250px_1fr]">
          {/* Sidebar */}
          <aside className="hidden lg:block">
            <div className="sticky top-24 space-y-4">
              <div className="surface p-4">
                <div className="flex items-center justify-between px-2 pb-3">
                  <span className="text-xs font-semibold text-slate-300">商品分类</span>
                  <span className="text-[10px] text-slate-600">全部 {data?.total ?? 48}</span>
                </div>
                <div className="grid gap-0.5">
                  <button
                    type="button"
                    onClick={() => selectCategory("")}
                    className={`flex items-center justify-between rounded-lg px-3 py-2.5 text-left text-xs transition ${
                      !category ? "bg-brand-600 text-white" : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
                    }`}
                  >
                    <span className="flex items-center gap-2.5">
                      <SlidersHorizontal size={14} />
                      全部商品
                    </span>
                    <span className={!category ? "text-blue-100" : "text-slate-600"}>{data?.total ?? 0}</span>
                  </button>
                  {(data?.categories || []).map((item) => {
                    const Icon = CATEGORY_ICONS[item.key] || Sparkles;
                    return (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => selectCategory(item.key)}
                        className={`flex items-center justify-between rounded-lg px-3 py-2.5 text-left text-xs transition ${
                          category === item.key
                            ? "bg-white/[0.07] text-slate-100"
                            : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
                        }`}
                      >
                        <span className="flex items-center gap-2.5">
                          <Icon size={14} className="text-slate-500" />
                          {item.label}
                        </span>
                        <span className="text-slate-600">{item.count}</span>
                      </button>
                    );
                  })}
                </div>

                <div className="my-4 h-px bg-white/[0.06]" />
                <p className="px-2 pb-2 text-[11px] font-medium text-slate-500">交付方式</p>
                <div className="grid gap-0.5">
                  {[
                    { key: "", label: "全部交付" },
                    { key: "ACTIVATION_CODE", label: "激活码核销" },
                    { key: "ACCOUNT_CREDENTIALS", label: "账密直发" },
                  ].map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      onClick={() => setDelivery(item.key)}
                      className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-xs transition ${
                        delivery === item.key ? "bg-white/[0.06] text-brand-300" : "text-slate-400 hover:bg-white/[0.04]"
                      }`}
                    >
                      <span className={`h-3 w-3 rounded-sm border ${delivery === item.key ? "border-brand-400 bg-brand-500/30" : "border-slate-600"}`} />
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="relative overflow-hidden rounded-2xl border border-brand-400/15 bg-gradient-to-b from-brand-500/[0.09] to-cyan-500/[0.035] p-5">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-brand-400/20 bg-brand-500/10 text-brand-300">
                  <HelpCircle size={16} />
                </div>
                <p className="mt-4 text-sm font-semibold text-slate-100">需要帮助？</p>
                <p className="mt-1.5 text-[11px] leading-5 text-slate-500">
                  我们随时为你解答购买、核销和售后问题。
                </p>
                <Link href="/help" className="btn-soft mt-4 w-full">
                  联系支持
                  <ArrowRight size={13} />
                </Link>
              </div>
            </div>
          </aside>

          {/* Main */}
          <section>
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-3">
                <span className="eyebrow">PRODUCTS</span>
                <span className="text-[10px] text-slate-600">/</span>
                <span className="text-[10px] uppercase tracking-[0.2em] text-slate-500">{activeTab}</span>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    setSubmittedQuery(query.trim());
                  }}
                  className="relative"
                >
                  <Search size={14} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="搜索商品"
                    className="h-10 w-full rounded-lg border border-white/10 bg-ink-900/70 pl-9 pr-3 text-xs text-slate-200 outline-none transition placeholder:text-slate-600 focus:border-brand-400/50 sm:w-[210px]"
                    aria-label="搜索商品"
                  />
                </form>
                <div className="relative">
                  <select
                    value={sort}
                    onChange={(event) => setSort(event.target.value)}
                    className="h-10 w-full appearance-none rounded-lg border border-white/10 bg-ink-900/70 pl-3 pr-9 text-xs text-slate-300 outline-none sm:w-[130px]"
                    aria-label="排序方式"
                  >
                    <option value="default">默认排序</option>
                    <option value="popular">销量优先</option>
                    <option value="price-asc">价格从低到高</option>
                    <option value="price-desc">价格从高到低</option>
                    <option value="newest">最新上架</option>
                  </select>
                  <ChevronDown size={13} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-500" />
                </div>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-center justify-between gap-4">
              <h2 className="text-[26px] font-semibold tracking-tight text-slate-50">
                全部商品 <span className="ml-1 text-sm font-normal text-slate-500">{data?.total ?? 0}</span>
              </h2>
            </div>

            <div className="scrollbar-thin mt-4 flex gap-2 overflow-x-auto pb-1">
              {TABS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => selectTab(item.key)}
                  className={`shrink-0 rounded-lg border px-3.5 py-2 text-[11px] transition ${
                    activeTab === item.key
                      ? "border-brand-400/30 bg-brand-500/15 text-brand-200"
                      : "border-white/[0.07] bg-white/[0.025] text-slate-400 hover:border-white/15 hover:text-slate-200"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>

            {/* Mobile filters */}
            <div className="mt-4 flex gap-2 overflow-x-auto pb-1 lg:hidden">
              <button
                type="button"
                onClick={() => selectCategory("")}
                className={`shrink-0 rounded-lg px-3 py-2 text-[11px] ${
                  !category ? "bg-brand-600 text-white" : "border border-white/10 text-slate-400"
                }`}
              >
                全部分类
              </button>
              {(data?.categories || []).map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => selectCategory(item.key)}
                  className={`shrink-0 rounded-lg px-3 py-2 text-[11px] ${
                    category === item.key ? "bg-brand-600 text-white" : "border border-white/10 text-slate-400"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>

            <div className="mt-5">
              {error ? (
                <div className="surface flex flex-col items-center gap-3 px-6 py-16 text-center">
                  <p className="text-sm text-slate-400">加载失败：{error}</p>
                  <button type="button" onClick={load} className="btn-soft">重新加载</button>
                </div>
              ) : loading ? (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {Array.from({ length: 6 }).map((_, index) => (
                    <div key={index} className="surface min-h-[210px] p-5">
                      <div className="skeleton h-14 w-14" />
                      <div className="skeleton mt-5 h-4 w-28" />
                      <div className="skeleton mt-2 h-3 w-40" />
                      <div className="skeleton mt-8 h-6 w-24" />
                    </div>
                  ))}
                </div>
              ) : products.length === 0 ? (
                <div className="surface flex flex-col items-center gap-3 px-6 py-16 text-center">
                  <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.03] text-slate-500">
                    <Search size={20} />
                  </div>
                  <p className="text-sm text-slate-400">没有找到匹配的商品</p>
                  <p className="text-xs text-slate-600">试试其他关键词，或查看全部商品。</p>
                  <button
                    type="button"
                    onClick={() => {
                      setQuery("");
                      setSubmittedQuery("");
                      setCategory("");
                      setDelivery("");
                      setTab("all");
                    }}
                    className="btn-soft"
                  >
                    清空筛选
                  </button>
                </div>
              ) : (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {products.map((product) => (
                    <ProductCard key={product.id} product={product} />
                  ))}
                </div>
              )}
            </div>

            <div className="mt-8 flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] px-5 py-4">
              <p className="text-[11px] text-slate-500">所有商品均支持 24 小时自动交付与订单状态查询</p>
              <Link href="/redeem" className="link-arrow">
                激活码核销
                <ArrowUpRight size={12} />
              </Link>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

export default function ProductsPage() {
  return (
    <Suspense
      fallback={
        <div className="container-page py-16">
          <div className="skeleton h-10 w-72" />
          <div className="mt-8 grid gap-4 md:grid-cols-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="surface min-h-[210px] p-5">
                <div className="skeleton h-14 w-14" />
                <div className="skeleton mt-5 h-4 w-28" />
              </div>
            ))}
          </div>
        </div>
      }
    >
      <ProductsContent />
    </Suspense>
  );
}
