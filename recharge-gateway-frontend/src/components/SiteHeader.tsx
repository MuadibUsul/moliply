"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  ArrowUpRight,
  Menu,
  ReceiptText,
  Search,
  ShoppingBag,
  X,
} from "lucide-react";
import { useState } from "react";
import { BrandLogo } from "@/components/BrandLogo";
import { NAV_LINKS } from "@/config/brand";
import { useCart } from "@/lib/cart";

function navActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/products") return pathname === "/products";
  if (href.startsWith("/products?")) return false;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const { count } = useCart();
  const [menuOpen, setMenuOpen] = useState(false);
  const [query, setQuery] = useState("");

  function search(event: React.FormEvent) {
    event.preventDefault();
    const value = query.trim();
    if (!value) return;
    setMenuOpen(false);
    router.push(`/products?q=${encodeURIComponent(value)}`);
  }

  return (
    <>
      <header className="sticky top-0 z-50 border-b border-white/[0.06] bg-[#05080f]/78 backdrop-blur-2xl">
        <div className="container-page flex h-[72px] items-center justify-between gap-5">
          <BrandLogo />

          <nav className="hidden items-center gap-1 lg:flex" aria-label="主导航">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.label}
                href={link.href}
                className={`rounded-lg px-3.5 py-2 text-[13px] transition ${
                  navActive(pathname, link.href)
                    ? "bg-white/[0.055] text-white"
                    : "text-slate-400 hover:bg-white/[0.03] hover:text-slate-100"
                }`}
              >
                {link.label}
              </Link>
            ))}
          </nav>

          <div className="flex items-center gap-1.5 sm:gap-2.5">
            <form onSubmit={search} className="relative hidden md:block">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索商品、服务"
                className="h-10 w-[190px] rounded-xl border border-white/10 bg-white/[0.035] pl-9 pr-12 text-xs text-slate-200 outline-none transition placeholder:text-slate-500 focus:w-[240px] focus:border-brand-400/50 focus:bg-ink-900/80"
                aria-label="搜索商品"
              />
              <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded border border-white/10 px-1.5 py-0.5 text-[9px] text-slate-500">
                ↵
              </span>
            </form>

            <Link
              href="/orders"
              className="hidden h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.035] text-slate-400 transition hover:border-white/20 hover:text-white sm:flex"
              aria-label="我的订单"
            >
              <ReceiptText size={16} />
            </Link>

            <Link
              href="/checkout"
              className="relative flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.035] text-slate-400 transition hover:border-white/20 hover:text-white"
              aria-label="购物车"
            >
              <ShoppingBag size={16} />
              {count > 0 && (
                <span className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-brand-500 px-1 text-[10px] font-semibold text-white shadow-glow-sm">
                  {count > 99 ? "99+" : count}
                </span>
              )}
            </Link>

            <Link
              href="/orders"
              className="hidden items-center gap-1.5 rounded-xl border border-brand-400/20 bg-brand-500/10 px-4 py-2.5 text-xs font-medium text-brand-200 transition hover:bg-brand-500/15 sm:inline-flex"
            >
              我的订单
              <ArrowUpRight size={13} />
            </Link>

            <button
              type="button"
              onClick={() => setMenuOpen((value) => !value)}
              className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.035] text-slate-300 lg:hidden"
              aria-label={menuOpen ? "关闭菜单" : "打开菜单"}
              aria-expanded={menuOpen}
            >
              {menuOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>
        </div>
      </header>

      {menuOpen && (
        <div className="fixed inset-x-0 top-[72px] z-40 border-b border-white/10 bg-[#070c16]/98 px-5 pb-6 pt-4 backdrop-blur-2xl lg:hidden">
          <form onSubmit={search} className="relative mb-4">
            <Search size={16} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索商品、服务"
              className="field pl-11"
              aria-label="移动端搜索商品"
            />
          </form>
          <div className="grid gap-1">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.label}
                href={link.href}
                onClick={() => setMenuOpen(false)}
                className="flex items-center justify-between rounded-xl px-3 py-3 text-sm text-slate-300 transition hover:bg-white/[0.05] hover:text-white"
              >
                {link.label}
                <ArrowUpRight size={15} className="text-slate-600" />
              </Link>
            ))}
            <Link
              href="/orders"
              onClick={() => setMenuOpen(false)}
              className="flex items-center justify-between rounded-xl px-3 py-3 text-sm text-slate-300 transition hover:bg-white/[0.05] hover:text-white"
            >
              我的订单
              <ArrowUpRight size={15} className="text-slate-600" />
            </Link>
            <Link
              href="/redeem"
              onClick={() => setMenuOpen(false)}
              className="mt-2 flex items-center justify-center gap-2 rounded-xl bg-brand-600 px-4 py-3 text-sm font-semibold text-white"
            >
              进入自助充能中心
              <ArrowUpRight size={15} />
            </Link>
          </div>
        </div>
      )}
    </>
  );
}
