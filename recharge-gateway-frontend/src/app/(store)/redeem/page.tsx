"use client";

import { useSearchParams } from "next/navigation";
import { Activity, Headphones, ShieldCheck, Zap } from "lucide-react";
import { Suspense } from "react";
import { RedeemWizard } from "@/components/RedeemWizard";

const FEATURES = [
  { icon: ShieldCheck, label: "安全核销", desc: "凭证加密，任务结束即清除" },
  { icon: Activity, label: "实时进度", desc: "任务状态自动轮询更新" },
  { icon: Headphones, label: "专业售后", desc: "异常任务转人工复核" },
  { icon: Zap, label: "24 小时服务", desc: "免密自助，随时充能" },
];

function RedeemContent() {
  const searchParams = useSearchParams();
  const code = searchParams.get("code") || "";

  return (
    <div className="relative">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[520px] overflow-hidden">
        <div className="absolute left-1/2 top-0 h-[420px] w-[900px] -translate-x-1/2 rounded-full bg-brand-600/[0.14] blur-[120px]" />
        <div className="absolute left-[18%] top-40 h-px w-[380px] rotate-[12deg] bg-gradient-to-r from-transparent via-cyan-300/15 to-transparent" />
        <div className="absolute right-[14%] top-52 h-px w-[300px] -rotate-[18deg] bg-gradient-to-r from-transparent via-brand-300/15 to-transparent" />
      </div>

      <div className="container-page relative pb-16 pt-12">
        <div className="text-center">
          <p className="eyebrow justify-center">MOLIPLY AUTO-RECHARGE GATEWAY</p>
          <h1 className="mt-4 font-display text-[30px] font-bold tracking-tight text-white sm:text-[40px]">
            MOLIPLY · 免密自助充能中心
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm text-slate-500">
            输入 MOLI 激活码，按指引完成核销，实时查看自动充能进度。
          </p>
        </div>

        <RedeemWizard initialCode={code} />

        <div className="mt-8 grid gap-2 rounded-2xl border border-white/[0.06] bg-white/[0.018] p-3 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((feature) => {
            const Icon = feature.icon;
            return (
              <div key={feature.label} className="flex items-center gap-3 rounded-xl px-3 py-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.07] bg-white/[0.03] text-brand-300">
                  <Icon size={15} />
                </span>
                <div>
                  <p className="text-[11px] font-medium text-slate-300">{feature.label}</p>
                  <p className="mt-0.5 text-[10px] text-slate-600">{feature.desc}</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function RedeemPage() {
  return (
    <Suspense
      fallback={
        <div className="container-page py-16">
          <div className="skeleton mx-auto h-9 w-72" />
          <div className="mx-auto mt-8 grid max-w-5xl gap-5 lg:grid-cols-[1.55fr_0.85fr]">
            <div className="surface h-[440px]" />
            <div className="surface h-[440px]" />
          </div>
        </div>
      }
    >
      <RedeemContent />
    </Suspense>
  );
}
