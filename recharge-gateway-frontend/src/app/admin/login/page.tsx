"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { useEffect, useState } from "react";
import { BrandLogo } from "@/components/BrandLogo";
import { api, getAdminToken, setAdminToken } from "@/lib/api";

type LoginResponse = { token: string; expires_in: number; username: string };

export default function AdminLoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (getAdminToken()) router.replace("/admin");
  }, [router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const data = await api<LoginResponse>("/api/v1/admin/login", {
        method: "POST",
        auth: false,
        body: { username: username.trim(), password, totp_code: totpCode.trim() },
      });
      setAdminToken(data.token);
      router.replace("/admin");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#05090f]">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-[10%] top-[-10%] h-[500px] w-[700px] rounded-full bg-brand-600/[0.12] blur-[130px]" />
        <div className="absolute bottom-[-20%] right-[5%] h-[500px] w-[600px] rounded-full bg-cyan-500/[0.06] blur-[130px]" />
        <div className="grid-lines absolute inset-0 opacity-30" />
      </div>

      <div className="relative mx-auto grid min-h-screen max-w-[1200px] gap-10 px-5 py-8 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:px-8">
        <div className="hidden lg:block">
          <BrandLogo />
          <p className="eyebrow mt-14">MOLIPLY WORKSPACE</p>
          <h1 className="mt-4 font-display text-[44px] font-bold leading-tight tracking-tight text-white">
            运营管理后台
          </h1>
          <p className="mt-4 max-w-md text-sm leading-7 text-slate-500">
            统一管理商品、订单、卡密库存与充值任务。所有关键操作均可审计，敏感凭证加密存储。
          </p>

          <div className="mt-9 max-w-md space-y-2">
            {[
              { icon: BarChart3, title: "实时经营概览", desc: "订单、收入、交付成功率一屏掌握" },
              { icon: KeyRound, title: "卡密与库存", desc: "批量制卡、导入上游卡密、状态追踪" },
              { icon: ShieldCheck, title: "任务与风控", desc: "充值任务状态机、失败重试与人工复核" },
            ].map((item) => {
              const Icon = item.icon;
              return (
                <div key={item.title} className="flex items-center gap-4 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <span className="flex h-10 w-10 items-center justify-center rounded-lg border border-brand-400/15 bg-brand-500/[0.07] text-brand-300">
                    <Icon size={17} />
                  </span>
                  <div>
                    <p className="text-xs font-medium text-slate-200">{item.title}</p>
                    <p className="mt-0.5 text-[10px] text-slate-600">{item.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-8 flex items-center gap-2 text-[10px] text-slate-700">
            <ShieldCheck size={13} />
            管理接口受 JWT 鉴权与限流保护
          </div>
        </div>

        <div className="mx-auto w-full max-w-[420px]">
          <div className="mb-8 flex items-center justify-between lg:hidden">
            <BrandLogo />
            <Link href="/" className="text-[10px] text-slate-500">返回商城</Link>
          </div>

          <form onSubmit={submit} className="surface relative p-6 sm:p-8">
            <div className="pointer-events-none absolute inset-x-8 top-0 h-px bg-gradient-to-r from-transparent via-brand-300/50 to-transparent" />
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-brand-600 to-cyan-500 text-white shadow-glow-sm">
              <ShieldCheck size={20} />
            </span>
            <h2 className="mt-5 text-xl font-semibold text-slate-50">登录运营后台</h2>
            <p className="mt-2 text-[11px] text-slate-500">使用管理员账号继续，仅限授权人员访问。</p>

            <div className="mt-6 space-y-4">
              <div>
                <label htmlFor="username" className="mb-2 block text-[11px] text-slate-400">管理员账号</label>
                <div className="relative">
                  <UserRound size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input
                    id="username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    className="field pl-10"
                    autoComplete="username"
                    required
                  />
                </div>
              </div>
              <div>
                <label htmlFor="password" className="mb-2 block text-[11px] text-slate-400">登录密码</label>
                <div className="relative">
                  <KeyRound size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    className="field pl-10 pr-11"
                    autoComplete="current-password"
                    placeholder="请输入管理员密码"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((value) => !value)}
                    className="absolute right-3 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center text-slate-500 hover:text-slate-300"
                    aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  >
                    {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>
              <div>
                <label htmlFor="totp" className="mb-2 block text-[11px] text-slate-400">谷歌验证码</label>
                <div className="relative">
                  <ShieldCheck size={15} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input
                    id="totp"
                    value={totpCode}
                    onChange={(event) => setTotpCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                    className="field pl-10 tracking-[0.35em]"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="6 位动态码"
                  />
                </div>
              </div>
            </div>

            {error && (
              <p className="mt-4 rounded-lg border border-rose-400/20 bg-rose-400/[0.07] px-3 py-2.5 text-[11px] leading-5 text-rose-200">
                {error}
              </p>
            )}

            <button type="submit" disabled={loading} className="btn-primary mt-6 w-full">
              {loading ? <Loader2 size={15} className="animate-spin" /> : null}
              {loading ? "正在登录..." : "进入运营后台"}
              {!loading && <ArrowRight size={15} />}
            </button>

            <p className="mt-4 text-center text-[10px] leading-5 text-slate-600">
              首次部署请修改 <code className="rounded bg-white/[0.05] px-1 py-0.5 text-slate-400">.env</code> 中的
              <code className="ml-1 rounded bg-white/[0.05] px-1 py-0.5 text-slate-400">ADMIN_PASSWORD</code>
            </p>
          </form>

          <div className="mt-6 flex items-center justify-between">
            <Link href="/" className="inline-flex items-center gap-1.5 text-[10px] text-slate-600 hover:text-slate-400">
              <ArrowLeft size={12} />
              返回商城
            </Link>
            <span className="text-[9px] text-slate-700">MOLIPLY 模力补给 · Admin</span>
          </div>
        </div>
      </div>
    </div>
  );
}
