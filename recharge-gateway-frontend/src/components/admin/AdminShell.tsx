"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Bell,
  Boxes,
  ChevronDown,
  CreditCard,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  Package,
  ReceiptText,
  Search,
  ServerCog,
  X,
  Zap,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { BrandLogo } from "@/components/BrandLogo";
import { clearAdminToken } from "@/lib/api";

const NAV_GROUPS = [
  {
    label: "工作台",
    items: [
      { href: "/admin", label: "概览", icon: LayoutDashboard },
      { href: "/admin/orders", label: "订单管理", icon: ReceiptText },
    ],
  },
  {
    label: "商品与库存",
    items: [
      { href: "/admin/products", label: "商品管理", icon: Package },
      { href: "/admin/codes", label: "卡密管理", icon: KeyRound },
      { href: "/admin/tasks", label: "充值任务", icon: Zap },
      { href: "/admin/cards", label: "VCC 卡池", icon: CreditCard },
      { href: "/admin/channels", label: "渠道状态", icon: ServerCog },
    ],
  },
];

const PAGE_TITLES: Record<string, string> = {
  "/admin": "运营概览",
  "/admin/orders": "订单管理",
  "/admin/products": "商品管理",
  "/admin/codes": "卡密管理",
  "/admin/tasks": "充值任务",
  "/admin/cards": "VCC 卡池",
  "/admin/channels": "渠道状态",
};

export function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);

  const currentTitle = PAGE_TITLES[pathname] || "运营后台";

  function logout() {
    clearAdminToken();
    router.replace("/admin/login");
  }

  function submitSearch(event: React.FormEvent) {
    event.preventDefault();
    const keyword = search.trim();
    if (!keyword) return;
    router.push(`/admin/orders?q=${encodeURIComponent(keyword)}`);
    setMobileOpen(false);
  }

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="px-5 pb-5 pt-6">
        <BrandLogo />
        <div className="mt-6 flex items-center justify-between rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2.5">
          <span className="text-[10px] uppercase tracking-[0.16em] text-slate-400">MOLIPLY WORKSPACE</span>
          <ChevronDown size={12} className="text-slate-600" />
        </div>
      </div>

      <nav className="flex-1 space-y-6 overflow-y-auto px-3 pb-4">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            <p className="px-2 pb-2 text-[9px] uppercase tracking-[0.2em] text-slate-600">{group.label}</p>
            <div className="grid gap-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                const active = item.href === "/admin" ? pathname === "/admin" : pathname.startsWith(item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setMobileOpen(false)}
                    className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-xs transition ${
                      active
                        ? "bg-brand-600 text-white shadow-[0_10px_30px_rgba(37,99,235,0.28)]"
                        : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
                    }`}
                  >
                    <Icon size={15} strokeWidth={active ? 2.2 : 1.8} />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-white/[0.06] px-5 py-4">
        <div className="flex items-center gap-2 text-[10px] text-slate-500">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
          </span>
          服务运行正常
        </div>
        <p className="mt-1.5 text-[9px] text-slate-700">全系统连接状态良好</p>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-[#05090f]">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[248px] border-r border-white/[0.06] bg-[#070c15] lg:block">
        {sidebar}
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="关闭菜单"
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="relative h-full w-[270px] border-r border-white/[0.08] bg-[#070c15]">
            {sidebar}
          </aside>
        </div>
      )}

      <div className="lg:pl-[248px]">
        <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-[#05090f]/88 backdrop-blur-xl">
          <div className="flex h-[64px] items-center justify-between gap-4 px-4 sm:px-6">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setMobileOpen(true)}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/10 text-slate-400 lg:hidden"
                aria-label="打开菜单"
              >
                <Menu size={17} />
              </button>
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-slate-600">运营管理后台</span>
                <span className="text-slate-700">/</span>
                <span className="text-slate-300">{currentTitle}</span>
              </div>
            </div>

            <div className="flex items-center gap-2 sm:gap-3">
              <form onSubmit={submitSearch} className="relative hidden md:block">
                <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
                <input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="搜索订单、商品"
                  className="h-9 w-[190px] rounded-lg border border-white/[0.08] bg-white/[0.025] pl-8 pr-3 text-[11px] text-slate-300 outline-none transition placeholder:text-slate-600 focus:border-brand-400/40"
                  aria-label="搜索订单"
                />
              </form>
              <button
                type="button"
                className="relative flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] text-slate-500 transition hover:text-slate-200"
                aria-label="通知"
              >
                <Bell size={15} />
                <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-brand-400" />
              </button>
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setMenuOpen((value) => !value)}
                  className="flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.025] px-2 py-1.5"
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-cyan-500 text-[10px] font-semibold text-white">
                    M
                  </span>
                  <span className="hidden text-[11px] text-slate-300 sm:block">管理员</span>
                  <ChevronDown size={12} className="hidden text-slate-600 sm:block" />
                </button>
                {menuOpen && (
                  <div className="absolute right-0 top-full z-50 mt-2 w-40 overflow-hidden rounded-xl border border-white/10 bg-[#0a1220] p-1 shadow-2xl">
                    <Link
                      href="/"
                      className="flex items-center gap-2 rounded-lg px-3 py-2 text-[11px] text-slate-400 hover:bg-white/5 hover:text-white"
                    >
                      <Boxes size={13} /> 返回商城
                    </Link>
                    <button
                      type="button"
                      onClick={logout}
                      className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[11px] text-slate-400 hover:bg-rose-500/10 hover:text-rose-300"
                    >
                      <LogOut size={13} /> 退出登录
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </header>

        <main className="min-h-[calc(100vh-64px)] px-4 py-6 sm:px-6 sm:py-8">{children}</main>
      </div>
    </div>
  );
}
