"use client";

import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

export function Modal({
  open,
  onClose,
  title,
  children,
  maxWidth = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  maxWidth?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm sm:items-center sm:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label={title || "弹窗"}
    >
      <div
        className={`relative max-h-[92vh] w-full ${maxWidth} overflow-y-auto rounded-t-3xl border border-white/10 bg-[#0b1220] p-6 shadow-2xl sm:rounded-3xl sm:p-7`}
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          {title ? <h3 className="text-lg font-semibold text-slate-50">{title}</h3> : <span />}
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 text-slate-400 transition hover:bg-white/5 hover:text-white"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
