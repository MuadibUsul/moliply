"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

export function CopyButton({
  value,
  label = "复制",
  compact = false,
  className = "",
}: {
  value: string;
  label?: string;
  compact?: boolean;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        const area = document.createElement("textarea");
        area.value = value;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border border-brand-400/25 bg-brand-500/12 text-xs font-medium text-brand-300 transition hover:bg-brand-500/20 ${compact ? "h-8 w-8" : "px-3 py-2"} ${className}`}
      aria-label={copied ? "已复制" : label}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {!compact && <span>{copied ? "已复制" : label}</span>}
    </button>
  );
}
