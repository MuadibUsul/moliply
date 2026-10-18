"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCircle2,
  Circle,
  FileJson,
  KeyRound,
  Loader2,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { SessionGuideModal } from "@/components/SessionGuideModal";
import { LAST_TASK_KEY } from "@/config/brand";
import { api } from "@/lib/api";
import type { RedeemPrecheck, RedeemTask, RedeemVerify } from "@/lib/types";

const STEP_LABELS = ["验证激活码", "提交 Session", "自动充能", "充值完成"];

type SavedTask = { taskId?: string; code?: string; savedAt?: number };

const FALLBACK_STEPS = [
  { key: "check", label: "验证激活码与订阅状态", status: "done" },
  { key: "create", label: "创建充值任务", status: "active" },
  { key: "pay", label: "提交支付请求", status: "waiting" },
  { key: "verify", label: "等待验证和支付结果", status: "waiting" },
  { key: "confirm", label: "确认订阅升级", status: "waiting" },
] as { key: string; label: string; status: string }[];

function periodFromSku(sku?: string) {
  if (!sku) return "以订单规格为准";
  if (sku.includes("12M")) return "12 个月";
  if (sku.includes("3M")) return "3 个月";
  return "1 个月";
}

export function RedeemWizard({ initialCode = "" }: { initialCode?: string }) {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [code, setCode] = useState(initialCode);
  const [verify, setVerify] = useState<RedeemVerify | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [sessionRaw, setSessionRaw] = useState("");
  const [precheck, setPrecheck] = useState<RedeemPrecheck | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<RedeemTask | null>(null);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const booted = useRef(false);

  const verifyCode = useCallback(
    async (raw?: string) => {
      const value = (raw ?? code).trim();
      if (!value) {
        setVerifyError("请输入订单中收到的 MOLI 激活码");
        return;
      }
      setVerifying(true);
      setVerifyError(null);
      setVerify(null);
      try {
        const data = await api<RedeemVerify>("/api/v1/redeem/verify-code", {
          method: "POST",
          body: { code: value },
        });
        setVerify(data);
        setCode(data.code);
        if (!data.redeemable) {
          setStep(1);
          setVerifyError(
            data.status === "USED"
              ? "该激活码已完成核销。如需再次充值，请重新购买。"
              : data.status === "LOCKED"
                ? "该激活码正在充能中，请稍后在订单中查看进度。"
                : "该激活码当前不可用，请联系客服。",
          );
          return;
        }
        setStep(2);
      } catch (err) {
        setVerifyError((err as Error).message);
        setStep(1);
      } finally {
        setVerifying(false);
      }
    },
    [code],
  );

  const pollTask = useCallback(async (id: string) => {
    try {
      const data = await api<RedeemTask>(`/api/v1/redeem/status/${id}`);
      setTask(data);
      setTaskError(null);
      if (data.state === "SUCCESS") {
        setStep(4);
      } else if (data.state === "FAILED") {
        setStep(3);
      }
    } catch (err) {
      setTaskError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    let saved: SavedTask | null = null;
    try {
      const raw = window.localStorage.getItem(LAST_TASK_KEY);
      if (raw) saved = JSON.parse(raw) as SavedTask;
    } catch {
      saved = null;
    }
    const fresh = saved?.savedAt ? Date.now() - saved.savedAt < 30 * 60 * 1000 : false;
    if (saved?.taskId && fresh && (!initialCode || saved.code === initialCode)) {
      setTaskId(saved.taskId);
      setCode(saved.code || "");
      setStep(3);
      void pollTask(saved.taskId);
      return;
    }
    if (initialCode) {
      void verifyCode(initialCode);
    }
  }, [initialCode, pollTask, verifyCode]);

  const terminal = task?.state === "SUCCESS" || task?.state === "FAILED";

  useEffect(() => {
    if (!taskId || terminal) return;
    void pollTask(taskId);
    const timer = window.setInterval(() => {
      void pollTask(taskId);
    }, 2200);
    return () => window.clearInterval(timer);
  }, [taskId, terminal, pollTask]);

  async function startRecharge() {
    if (!verify) return;
    if (!sessionRaw.trim()) {
      setSubmitError(verify.session_kind === "claude" ? "请先粘贴 Claude Session Key" : "请先粘贴 ChatGPT Session JSON");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const pre = await api<RedeemPrecheck>("/api/v1/redeem/parse-session", {
        method: "POST",
        body: { code: verify.code, session_raw: sessionRaw },
      });
      setPrecheck(pre);
      const submitted = await api<{ task_id: string; state: string }>("/api/v1/redeem/submit", {
        method: "POST",
        body: { code: verify.code, session_raw: sessionRaw },
      });
      setTaskId(submitted.task_id);
      setTask(null);
      setStep(3);
      window.localStorage.setItem(
        LAST_TASK_KEY,
        JSON.stringify({ taskId: submitted.task_id, code: verify.code, savedAt: Date.now() }),
      );
    } catch (err) {
      setSubmitError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  function reset() {
    setStep(1);
    setVerify(null);
    setVerifyError(null);
    setSessionRaw("");
    setPrecheck(null);
    setSubmitError(null);
    setTask(null);
    setTaskId(null);
    setTaskError(null);
    window.localStorage.removeItem(LAST_TASK_KEY);
  }

  function retry() {
    if (!verify) {
      setStep(1);
      return;
    }
    if (!sessionRaw.trim()) {
      setStep(2);
      setSubmitError("Session 内容已不在本页，请重新粘贴后再次提交");
      return;
    }
    void startRecharge();
  }

  const progress = task?.progress_percent ?? (step === 4 ? 100 : 12);
  const progressColor = task?.state === "FAILED" ? "#fb7185" : task?.state === "SUCCESS" ? "#34d399" : "url(#progressGradient)";
  const circumference = 2 * Math.PI * 66;
  const displaySteps = task?.steps?.length ? task.steps : FALLBACK_STEPS;
  const accountEmail = precheck?.account_email || task?.target_email || "—";

  return (
    <div>
      {/* Stepper */}
      <div className="mx-auto mt-9 flex max-w-3xl items-start">
        {STEP_LABELS.map((label, index) => {
          const number = (index + 1) as 1 | 2 | 3 | 4;
          const done = step > number;
          const active = step === number;
          return (
            <div key={label} className="flex flex-1 items-start last:flex-none">
              <div className="flex w-16 flex-col items-center gap-2 sm:w-20">
                <span
                  className={`flex h-9 w-9 items-center justify-center rounded-full border text-[11px] transition ${
                    done
                      ? "border-brand-400/40 bg-brand-500 text-white"
                      : active
                        ? "border-brand-400/60 bg-brand-500/15 text-brand-200 shadow-glow-sm"
                        : "border-slate-700 bg-ink-950 text-slate-500"
                  }`}
                >
                  {done ? <Check size={16} /> : number}
                </span>
                <span className={`text-center text-[10px] leading-4 ${active || done ? "text-slate-300" : "text-slate-600"}`}>
                  {label}
                </span>
              </div>
              {index < STEP_LABELS.length - 1 && (
                <span className={`mt-[17px] h-px flex-1 ${done ? "bg-brand-500/55" : "bg-slate-700/60"}`} />
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-8 grid gap-5 lg:grid-cols-[1.55fr_0.85fr]">
        <section className="surface min-h-[430px] p-5 sm:p-7">
          {step === 1 && (
            <div className="animate-fade-up">
              <p className="eyebrow">STEP 01 / CODE VERIFICATION</p>
              <h2 className="mt-3 text-xl font-semibold text-slate-50">验证 MOLI 激活码</h2>
              <p className="mt-2 text-xs leading-6 text-slate-500">请输入购买后获得的激活码，系统将验证卡密状态和对应商品。</p>

              <label htmlFor="activation-code" className="mb-2 mt-7 block text-[11px] text-slate-400">
                MOLI 激活码
              </label>
              <input
                id="activation-code"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void verifyCode();
                }}
                placeholder="MOLI-XXXX-XXXX-XXXX"
                className="field field-lg font-mono uppercase tracking-[0.14em]"
                autoComplete="off"
                spellCheck={false}
              />
              <p className="mt-2 text-[10px] text-slate-600">示例格式：MOLI-8F7K-2D9M-4P6H</p>

              {verifyError && (
                <div className="mt-4 flex items-start gap-2 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] p-3.5 text-[11px] leading-5 text-rose-200">
                  <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                  {verifyError}
                </div>
              )}

              {verify && !verify.redeemable && (
                <div className="mt-4 rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 text-[11px]">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-slate-300">{verify.code}</span>
                    <span className="text-slate-500">{verify.status_label}</span>
                  </div>
                  <p className="mt-2 text-slate-500">{verify.sku_label}</p>
                </div>
              )}

              <button type="button" onClick={() => void verifyCode()} disabled={verifying} className="btn-primary mt-6 w-full">
                {verifying ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />}
                {verifying ? "正在验证..." : "验证激活码"}
                {!verifying && <ArrowRight size={15} />}
              </button>

              <div className="mt-5 flex items-start gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5 text-[10px] leading-5 text-slate-500">
                <ShieldCheck size={14} className="mt-0.5 shrink-0 text-brand-300" />
                卡密验证通过后将显示绑定商品、核销状态和下一步操作。激活码仅可绑定一个账号。
              </div>
            </div>
          )}

          {step === 2 && verify && (
            <div className="animate-fade-up">
              <p className="eyebrow">STEP 02 / SESSION VERIFICATION</p>
              <h2 className="mt-3 text-xl font-semibold text-slate-50">
                {verify.session_kind === "claude" ? "提交 Claude Session Key" : "提交 Session JSON"}
              </h2>
              <p className="mt-2 text-xs leading-6 text-slate-500">
                {verify.session_kind === "claude"
                  ? "按指引粘贴 Claude sessionKey，由上游通道完成账户核验与充值。"
                  : "按照指引粘贴 ChatGPT Session JSON，由后台进行账号状态预检。"}
              </p>

              <div className="mt-5 flex items-center gap-2 rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] px-3.5 py-3 text-[11px]">
                <CheckCircle2 size={15} className="shrink-0 text-emerald-300" />
                <span className="text-emerald-200/90">MOLI 激活码验证成功</span>
                <span className="ml-auto text-[10px] text-emerald-200/60">{verify.sku_label}</span>
              </div>

              <div className="mt-5 flex items-center justify-between">
                <label htmlFor="session-raw" className="text-[11px] text-slate-400">
                  {verify.session_kind === "claude" ? "Claude Session Key" : "ChatGPT Session JSON"}
                </label>
                <button
                  type="button"
                  onClick={() => setGuideOpen(true)}
                  className="inline-flex items-center gap-1 text-[10px] text-brand-300 hover:text-brand-200"
                >
                  <FileJson size={12} />
                  如何获取？
                </button>
              </div>
              <textarea
                id="session-raw"
                value={sessionRaw}
                onChange={(event) => setSessionRaw(event.target.value)}
                placeholder={
                  verify.session_kind === "claude"
                    ? "sessionKey=sk-ant-..."
                    : '{\n  "user": { "email": "user@example.com" },\n  "accessToken": "…",\n  "expires": "…"\n}'
                }
                className="field mt-2 min-h-[210px] resize-y font-mono text-[11px] leading-6"
                spellCheck={false}
              />
              <div className="mt-2 flex items-center justify-between gap-3 text-[10px] text-slate-600">
                <span>凭证仅用于本次充值，加密保存并在任务结束后清除。</span>
                <button type="button" onClick={() => setGuideOpen(true)} className="shrink-0 text-slate-500 hover:text-brand-300">
                  查看图文指引
                </button>
              </div>

              {submitError && (
                <div className="mt-4 flex items-start gap-2 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] p-3.5 text-[11px] leading-5 text-rose-200">
                  <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                  {submitError}
                </div>
              )}

              <div className="mt-6 grid gap-2 sm:grid-cols-[1.4fr_0.8fr]">
                <button type="button" onClick={() => void startRecharge()} disabled={submitting} className="btn-primary w-full">
                  {submitting ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                  {submitting ? "正在预检并创建任务..." : "提交并开始充能"}
                  {!submitting && <ArrowRight size={15} />}
                </button>
                <button type="button" onClick={() => { setStep(1); setSubmitError(null); }} className="btn-secondary w-full">
                  返回修改激活码
                </button>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="animate-fade-up">
              <p className="eyebrow">STEP 03 / PROCESSING</p>
              <h2 className="mt-3 text-xl font-semibold text-slate-50">
                {task?.state === "FAILED" ? "充能未完成" : "自动充能进行中"}
              </h2>
              <p className="mt-2 text-xs leading-6 text-slate-500">
                {task?.state === "FAILED"
                  ? "任务遇到问题，系统已按安全策略处理。请查看下方提示。"
                  : "您的任务已创建，系统正在处理并实时更新执行状态。"}
              </p>

              <div className="mt-7 flex flex-col items-center">
                <div className="relative">
                  <svg width="164" height="164" viewBox="0 0 164 164" className="-rotate-90">
                    <defs>
                      <linearGradient id="progressGradient" x1="0" y1="0" x2="1" y2="1">
                        <stop offset="0%" stopColor="#60a5fa" />
                        <stop offset="60%" stopColor="#2563eb" />
                        <stop offset="100%" stopColor="#22d3ee" />
                      </linearGradient>
                    </defs>
                    <circle cx="82" cy="82" r="66" fill="none" stroke="rgba(148,163,184,0.12)" strokeWidth="11" />
                    <circle
                      cx="82"
                      cy="82"
                      r="66"
                      fill="none"
                      stroke={progressColor}
                      strokeWidth="11"
                      strokeLinecap="round"
                      strokeDasharray={circumference}
                      strokeDashoffset={circumference * (1 - Math.min(100, Math.max(0, progress)) / 100)}
                      className="transition-all duration-700"
                    />
                  </svg>
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <span className="font-display text-[30px] font-semibold text-slate-50">{Math.round(progress)}%</span>
                    <span className="mt-1 text-[10px] text-slate-500">
                      {task?.state === "FAILED" ? "任务已结束" : task?.state === "SUCCESS" ? "充值完成" : "充能处理中"}
                    </span>
                  </div>
                </div>
                <p className="mt-4 max-w-md text-center text-[11px] leading-6 text-slate-400">
                  {task?.step_message || "正在创建充值任务，请稍候..."}
                </p>
              </div>

              <div className="mt-6">
                <div className="mb-2 flex justify-between text-[10px] text-slate-600">
                  <span>任务进度</span>
                  <span>{Math.round(progress)}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className={`h-full rounded-full transition-all duration-700 ${
                      task?.state === "FAILED" ? "bg-rose-400" : "bg-gradient-to-r from-brand-500 to-cyan-400"
                    }`}
                    style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
                  />
                </div>
              </div>

              <div className="mt-6 space-y-1">
                {displaySteps.map((item) => (
                  <div key={item.key} className="flex items-center justify-between gap-3 py-2">
                    <span className="flex items-center gap-2.5 text-[11px] text-slate-400">
                      {item.status === "done" && <CheckCircle2 size={14} className="text-emerald-300" />}
                      {item.status === "active" && <Loader2 size={14} className="animate-spin text-brand-300" />}
                      {item.status === "failed" && <XCircle size={14} className="text-rose-300" />}
                      {item.status === "waiting" && <Circle size={14} className="text-slate-700" />}
                      <span className={item.status === "active" ? "text-slate-200" : ""}>{item.label}</span>
                    </span>
                    <span
                      className={`text-[10px] ${
                        item.status === "done"
                          ? "text-emerald-300/80"
                          : item.status === "failed"
                            ? "text-rose-300/80"
                            : item.status === "active"
                              ? "text-brand-300/80"
                              : "text-slate-700"
                      }`}
                    >
                      {item.status === "done" ? "已完成" : item.status === "active" ? "进行中" : item.status === "failed" ? "未完成" : "等待中"}
                    </span>
                  </div>
                ))}
              </div>

              {task?.task_id && (
                <div className="mt-4 rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 font-mono text-[10px] text-slate-600">
                  任务编号：{task.task_id}
                </div>
              )}

              {taskError && (
                <p className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/[0.07] px-3 py-2.5 text-[10px] text-rose-200">
                  状态查询失败：{taskError}
                </p>
              )}

              {task?.state === "FAILED" && (
                <div className="mt-5 rounded-xl border border-rose-400/20 bg-rose-400/[0.06] p-4">
                  <div className="flex items-start gap-2">
                    <AlertTriangle size={16} className="mt-0.5 shrink-0 text-rose-300" />
                    <div>
                      <p className="text-xs font-medium text-rose-200">
                        {task.error_code || "RECHARGE_FAILED"}
                      </p>
                      <p className="mt-1 text-[11px] leading-5 text-rose-200/70">{task.error_message}</p>
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {task.can_retry && (
                      <button type="button" onClick={retry} className="btn-primary py-2.5">
                        重新尝试充能
                        <ArrowRight size={14} />
                      </button>
                    )}
                    <Link href="/help#contact" className="btn-secondary py-2.5">联系客服</Link>
                  </div>
                </div>
              )}

              {task?.state !== "FAILED" && (
                <p className="mt-5 text-center text-[10px] text-slate-600">
                  充值期间请保持页面开启。关闭后可通过订单记录或本页自动恢复任务状态。
                </p>
              )}
            </div>
          )}

          {step === 4 && (
            <div className="animate-fade-up text-center">
              <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-full border border-emerald-400/30 bg-emerald-400/10 text-emerald-300 shadow-[0_0_60px_rgba(52,211,153,0.16)]">
                <CheckCircle2 size={38} />
              </div>
              <h2 className="mt-6 font-display text-[26px] font-semibold text-white">充值成功！</h2>
              <p className="mt-2 text-xs text-slate-500">
                您的 {verify?.sku_label || task?.sku_label || "会员服务"} 订阅已完成升级。
              </p>

              <div className="mx-auto mt-6 max-w-md divide-y divide-white/[0.06] rounded-xl border border-white/[0.07] bg-white/[0.02] px-4 text-left">
                <div className="flex items-center justify-between py-3.5 text-[11px]">
                  <span className="text-slate-500">充值产品</span>
                  <span className="text-slate-200">{verify?.sku_label || task?.sku_label || "—"}</span>
                </div>
                <div className="flex items-center justify-between py-3.5 text-[11px]">
                  <span className="text-slate-500">账户邮箱</span>
                  <span className="text-slate-200">{accountEmail}</span>
                </div>
                <div className="flex items-center justify-between py-3.5 text-[11px]">
                  <span className="text-slate-500">服务周期</span>
                  <span className="text-slate-200">{periodFromSku(verify?.sku_type)}</span>
                </div>
                <div className="flex items-center justify-between py-3.5 text-[11px]">
                  <span className="text-slate-500">订单状态</span>
                  <span className="text-emerald-300">充值成功</span>
                </div>
              </div>

              <div className="mx-auto mt-6 grid max-w-md gap-2">
                <Link href="/orders" className="btn-primary w-full">
                  查看订单状态
                  <ArrowRight size={15} />
                </Link>
                <Link href="/products" className="btn-secondary w-full">返回商城</Link>
                <button type="button" onClick={reset} className="text-[10px] text-slate-600 transition hover:text-slate-400">
                  为另一个账号充能
                </button>
              </div>

              <p className="mt-5 text-[10px] leading-5 text-slate-600">
                请刷新 ChatGPT / Claude 页面确认会员权益已生效。如未生效，请稍等 1–2 分钟后再次刷新或联系客服。
              </p>
            </div>
          )}
        </section>

        {/* Info column */}
        <aside className="space-y-4">
          <div className="surface p-5">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-brand-400/20 bg-brand-500/10 text-brand-300">
              <Sparkles size={18} />
            </span>
            <h3 className="mt-4 text-sm font-semibold text-slate-100">什么是 MOLI 激活码？</h3>
            <p className="mt-2.5 text-[11px] leading-6 text-slate-500">
              在 MOLIPLY 商城购买对应数字商品后，系统自动发放以 MOLI- 开头的一次性激活码。
            </p>
            <ul className="mt-4 space-y-2.5 text-[11px] text-slate-500">
              <li className="flex items-center gap-2"><Circle size={8} strokeWidth={3} /> 一次性激活码</li>
              <li className="flex items-center gap-2"><Circle size={8} strokeWidth={3} /> 支持订单状态查询</li>
              <li className="flex items-center gap-2"><Circle size={8} strokeWidth={3} /> 充值任务实时进度</li>
            </ul>
            <div className="my-4 h-px bg-white/[0.06]" />
            <p className="text-[11px] text-slate-500">没有激活码？</p>
            <Link href="/products" className="btn-secondary mt-3 w-full">
              前往商城选购
              <ArrowUpRight size={13} />
            </Link>
          </div>

          <div className="rounded-2xl border border-emerald-400/15 bg-emerald-400/[0.035] p-5">
            <div className="flex items-center gap-2 text-emerald-300">
              <ShieldCheck size={16} />
              <span className="text-xs font-medium">安全核销提示</span>
            </div>
            <p className="mt-2.5 text-[10px] leading-5 text-slate-500">
              请勿向任何人提供账号密码。MOLIPLY 仅使用临时 Session 完成订阅升级，任务结束后自动清除凭证。
            </p>
          </div>

          <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
            <p className="text-[11px] font-medium text-slate-300">需要人工协助？</p>
            <p className="mt-1.5 text-[10px] leading-5 text-slate-600">保留订单编号与任务编号，联系在线客服可快速定位问题。</p>
            <Link href="/help#contact" className="link-arrow mt-3">前往帮助中心 <ArrowRight size={12} /></Link>
          </div>
        </aside>
      </div>

      <SessionGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} kind={verify?.session_kind || "chatgpt"} />
    </div>
  );
}
