import Link from "next/link";

export function BrandLogo({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="group flex items-center gap-3" aria-label="MOLIPLY 模力补给首页">
      <span className="relative flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-[14px] bg-gradient-to-br from-[#1d4ed8] via-[#2563eb] to-[#06b6d4] shadow-[0_8px_30px_rgba(37,99,235,0.45)]">
        <span className="absolute inset-0 bg-[radial-gradient(circle_at_30%_15%,rgba(255,255,255,0.38),transparent_45%)]" />
        <svg viewBox="0 0 32 32" className="relative h-7 w-7" fill="none" aria-hidden="true">
          <path
            d="M7 23.5V10.8c0-.9 1.1-1.3 1.7-.6l6.2 7.4c.6.7 1.6.7 2.2 0l6.2-7.4c.6-.7 1.7-.3 1.7.6v12.7"
            stroke="white"
            strokeWidth="3.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path d="M7.5 23.5h17" stroke="rgba(255,255,255,0.42)" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        <span className="absolute -bottom-3 left-1/2 h-6 w-14 -translate-x-1/2 rounded-full bg-cyan-300/30 blur-lg" />
      </span>
      {!compact && (
        <span className="leading-none">
          <span className="block font-display text-[19px] font-bold tracking-[0.08em] text-white">
            MOLIPLY
          </span>
          <span className="mt-1.5 block text-[10px] tracking-[0.36em] text-slate-400">
            模力补给
          </span>
        </span>
      )}
    </Link>
  );
}
