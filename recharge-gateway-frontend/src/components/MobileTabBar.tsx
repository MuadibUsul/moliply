"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, LayoutGrid, ReceiptText, Zap } from "lucide-react";

const TABS = [
  { href: "/", label: "首页", icon: Home },
  { href: "/products", label: "分类", icon: LayoutGrid },
  { href: "/orders", label: "订单", icon: ReceiptText },
  { href: "/redeem", label: "充能", icon: Zap },
];

export function MobileTabBar() {
  const pathname = usePathname();
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-50 border-t border-white/[0.08] bg-[#070c16]/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-2xl lg:hidden"
      aria-label="移动端快捷导航"
    >
      <div className="grid grid-cols-4">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const active = tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={`flex flex-col items-center gap-1.5 py-3 text-[10px] ${
                active ? "text-brand-300" : "text-slate-500"
              }`}
            >
              <Icon size={19} strokeWidth={active ? 2.4 : 1.8} />
              {tab.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
