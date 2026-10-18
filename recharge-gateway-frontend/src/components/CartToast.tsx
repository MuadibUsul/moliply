"use client";

import { CheckCircle2 } from "lucide-react";
import { useCart } from "@/lib/cart";

export function CartToast() {
  const { notice } = useCart();
  if (!notice) return null;
  return (
    <div className="fixed right-4 top-24 z-[70] flex items-center gap-2 rounded-xl border border-emerald-400/25 bg-[#071612]/95 px-4 py-3 text-xs text-emerald-200 shadow-xl backdrop-blur-xl animate-fade-up">
      <CheckCircle2 size={16} />
      {notice}
    </div>
  );
}
