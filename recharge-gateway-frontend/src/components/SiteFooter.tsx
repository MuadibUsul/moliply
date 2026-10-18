import Link from "next/link";
import { BrandLogo } from "@/components/BrandLogo";
import { BRAND, FOOTER_LINKS } from "@/config/brand";

export function SiteFooter() {
  return (
    <footer className="mt-12 border-t border-white/[0.06] bg-[#040710]/60">
      <div className="container-page py-12">
        <div className="grid gap-10 md:grid-cols-[1.3fr_1fr_1fr]">
          <div>
            <BrandLogo />
            <p className="mt-5 max-w-md text-xs leading-6 text-slate-500">
              {BRAND.slogan}
              <br />
              {BRAND.subtitle} · 24 小时自动交付 · 订单与充能进度全程可查
            </p>
          </div>
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">快速导航</p>
            <div className="mt-4 grid gap-2.5 text-xs">
              <Link href="/products" className="text-slate-400 hover:text-white">全部商品</Link>
              <Link href="/redeem" className="text-slate-400 hover:text-white">自助充能中心</Link>
              <Link href="/orders" className="text-slate-400 hover:text-white">订单查询</Link>
              <Link href="/help" className="text-slate-400 hover:text-white">帮助中心</Link>
            </div>
          </div>
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">服务与支持</p>
            <div className="mt-4 grid gap-2.5 text-xs">
              {FOOTER_LINKS.map((link) => (
                <Link key={link.label} href={link.href} className="text-slate-400 hover:text-white">
                  {link.label}
                </Link>
              ))}
              <Link href="/admin/login" className="text-slate-600 hover:text-slate-400">运营后台</Link>
            </div>
          </div>
        </div>

        <div className="mt-10 border-t border-white/[0.055] pt-6">
          <p className="text-[10px] leading-5 text-slate-600">
            {BRAND.nameEn}（{BRAND.nameCn}）仅提供正版软件订阅代付与数字商品自动交付服务，请确保在合法合规的网络环境下使用。
            商品页展示的会员价格与权益以对应服务商的官方政策为准；售后保障范围以商品说明及订单记录为准。
          </p>
          <div className="mt-4 flex flex-col gap-2 text-[10px] text-slate-600 sm:flex-row sm:items-center sm:justify-between">
            <span>© 2026 {BRAND.nameEn} · {BRAND.nameCn}. All rights reserved.</span>
            <span>{BRAND.subtitle}</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
