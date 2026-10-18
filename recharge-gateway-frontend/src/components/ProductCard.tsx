import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { BrandTile } from "@/components/BrandTile";
import { formatMoney, formatNumber } from "@/lib/format";
import type { Product } from "@/lib/types";

export function ProductCard({ product }: { product: Product }) {
  const outOfStock = product.stock !== null && product.stock <= 0;
  return (
    <Link
      href={`/products/${product.slug}`}
      className="surface surface-hover group relative flex min-h-[210px] flex-col overflow-hidden p-5"
    >
      <div className="pointer-events-none absolute -right-12 -top-16 h-40 w-40 rounded-full bg-brand-500/[0.07] blur-2xl transition group-hover:bg-brand-500/[0.12]" />
      {product.badge && (
        <span className="absolute left-5 top-5 z-10 rounded-md border border-brand-400/25 bg-brand-500/10 px-2 py-0.5 text-[10px] font-medium text-brand-300">
          {product.badge}
        </span>
      )}
      <div className="flex items-start gap-3.5 pt-7">
        <BrandTile logoKey={product.logo_key} size={54} />
        <div className="min-w-0 pt-1">
          <h3 className="truncate text-[15px] font-semibold text-slate-100 transition group-hover:text-white">
            {product.name}
          </h3>
          <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-500">{product.subtitle}</p>
        </div>
      </div>

      <div className="mt-auto flex items-end justify-between gap-3 pt-5">
        <div>
          <div className="flex items-baseline gap-2">
            <span className="price text-[22px]">{formatMoney(product.price)}</span>
            {product.original_price && (
              <span className="text-[11px] text-slate-600 line-through">{formatMoney(product.original_price)}</span>
            )}
          </div>
          <p className="mt-1.5 text-[10px] text-slate-500">
            {outOfStock ? "补货中 · 可咨询客服" : product.delivery_note || "24 小时自动交付"}
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <span className="text-[10px] text-slate-600">已售 {formatNumber(product.sold_count)}</span>
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 text-white shadow-glow-sm transition group-hover:bg-brand-500">
            <ArrowUpRight size={16} />
          </span>
        </div>
      </div>
    </Link>
  );
}
