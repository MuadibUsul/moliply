import { statusTone } from "@/lib/format";

const TONES: Record<string, string> = {
  success: "border-emerald-400/25 bg-emerald-400/10 text-emerald-300",
  warning: "border-amber-400/25 bg-amber-400/10 text-amber-300",
  danger: "border-rose-400/25 bg-rose-400/10 text-rose-300",
  muted: "border-white/10 bg-white/[0.04] text-slate-400",
  brand: "border-brand-400/25 bg-brand-500/10 text-brand-300",
};

export function StatusBadge({ status, label }: { status: string; label: string }) {
  const tone = statusTone(status);
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium ${TONES[tone]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      {label}
    </span>
  );
}
