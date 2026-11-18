"use client";

import { CheckCircle2, Clock, Copy, RefreshCw, ShieldCheck, Smartphone } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type SmsOption = { key: string; label: string };

type SmsOptions = {
  services: SmsOption[];
  countries: SmsOption[];
  provider_ready: boolean;
  window_seconds: number;
};

type SmsTask = {
  task_id: string;
  status: "WAITING" | "RECEIVED" | "EXPIRED" | "CANCELLED" | "FAILED";
  phone_number: string;
  service: string;
  service_label: string;
  country: string;
  country_label: string;
  otp_code: string | null;
  raw_sms: string | null;
  seconds_remaining: number;
  window_seconds: number;
  code_status: string;
  error_message: string | null;
  can_change_number: boolean;
};

const POLL_MS = 3000;

function CopyField({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] px-4 py-4">
      <p className="text-[10px] uppercase tracking-[0.2em] text-slate-600">{label}</p>
      <div className="mt-2 flex items-center justify-between gap-3">
        <span className="select-all font-mono text-lg text-slate-100">{value}</span>
        <button type="button" onClick={copy} className="btn-soft shrink-0 px-3 py-2 text-[10px]">
          <Copy size={12} />
          {copied ? "已复制" : "复制"}
        </button>
      </div>
    </div>
  );
}

export function SmsWorkbench({ code }: { code: string }) {
  const [options, setOptions] = useState<SmsOptions | null>(null);
  const [service, setService] = useState("claude");
  const [country, setCountry] = useState("US");
  const [task, setTask] = useState<SmsTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<SmsOptions>("/api/v1/sms/options")
      .then((data) => {
        setOptions(data);
        if (data.services[0]) setService(data.services[0].key);
        if (data.countries[0]) setCountry(data.countries[0].key);
      })
      .catch(() => setOptions(null));
  }, []);

  const take = useCallback(
    async (nextService = service, nextCountry = country) => {
      setBusy(true);
      setError(null);
      try {
        const data = await api<{ task: SmsTask }>("/api/v1/sms/lease", {
          method: "POST",
          body: { code, service: nextService, country: nextCountry },
        });
        setTask(data.task);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [code, service, country],
  );


  // Countdown is local: each poll re-anchors it from the server's remaining
  // seconds, so the display keeps moving smoothly between polls.
  const [remaining, setRemaining] = useState(0);
  useEffect(() => {
    if (task?.status === "WAITING") setRemaining(task.seconds_remaining);
  }, [task?.task_id, task?.seconds_remaining, task?.status]);

  useEffect(() => {
    if (!task || task.status !== "WAITING" || remaining <= 0) return;
    const timer = window.setTimeout(() => setRemaining((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [remaining, task]);

  const polling = task?.status === "WAITING";
  const taskId = task?.task_id ?? null;
  useEffect(() => {
    if (!taskId || !polling) return;
    const timer = window.setInterval(async () => {
      try {
        const data = await api<{ task: SmsTask }>(`/api/v1/sms/tasks/${taskId}`);
        setTask(data.task);
      } catch {
        // A transient poll error must not tear down the countdown.
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [taskId, polling]);

  async function changeNumber() {
    if (!task) return;
    setBusy(true);
    setError(null);
    try {
      const data = await api<{ task: SmsTask }>(`/api/v1/sms/tasks/${task.task_id}/change`, {
        method: "POST",
        body: {},
      });
      setTask(data.task);
      setRemaining(data.task.seconds_remaining);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const elapsed = task ? task.window_seconds - remaining : 0;
  const onCooldown = !!task && task.status === "WAITING" && (task.can_change_number || elapsed >= 45);
  const received = task?.status === "RECEIVED";
  const ended = task?.status === "EXPIRED" || task?.status === "CANCELLED" || task?.status === "FAILED";

  const expiredLabel = useMemo(() => {
    if (task?.status === "EXPIRED") return "超时未收到验证码，号码已退回";
    if (task?.status === "CANCELLED") return "已取消本次取号";
    if (task?.status === "FAILED") return task.error_message || "取号失败";
    return "";
  }, [task]);

  if (options && !options.provider_ready) {
    return (
      <div className="rounded-2xl border border-amber-400/25 bg-amber-400/[0.06] p-5 text-[11px] leading-6 text-amber-200">
        接码通道尚未配置上游密钥，暂时无法取号。请稍后再试或联系客服。
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Step 1 — choose platform + region */}
      <div>
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-500/15 text-[10px] text-brand-200">1</span>
          <p className="text-[11px] font-medium text-slate-300">选择目标平台与地区</p>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="text-[10px] text-slate-500">目标平台</span>
            <select
              value={service}
              onChange={(event) => setService(event.target.value)}
              disabled={busy || !!task}
              className="field mt-1 w-full text-[11px]"
            >
              {(options?.services || []).map((item) => (
                <option key={item.key} value={item.key}>{item.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-[10px] text-slate-500">国家 / 号段</span>
            <select
              value={country}
              onChange={(event) => setCountry(event.target.value)}
              disabled={busy || !!task}
              className="field mt-1 w-full text-[11px]"
            >
              {(options?.countries || []).map((item) => (
                <option key={item.key} value={item.key}>{item.label}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {!task && (
        <button type="button" onClick={() => void take()} disabled={busy} className="btn-primary w-full">
          <Smartphone size={14} className={busy ? "animate-pulse" : ""} />
          {busy ? "正在取号..." : "立即获取海外实体卡号码"}
        </button>
      )}

      {error && (
        <p className="rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-3.5 py-3 text-[11px] text-rose-200">
          {error}
        </p>
      )}

      {/* Step 2 — the number and the countdown */}
      {task && (
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-500/15 text-[10px] text-brand-200">2</span>
            <p className="text-[11px] font-medium text-slate-300">把下面的号码填入 {task.service_label}</p>
          </div>

          <div className="mt-3">
            <CopyField value={task.phone_number} label="接收号码（含国际区号）" />
          </div>

          {polling && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-[10px] text-slate-500">
                <span className="inline-flex items-center gap-1.5">
                  <Clock size={11} />
                  等待短信，剩余 {remaining} 秒
                </span>
                <span>{task.country_label}</span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-brand-500 to-cyan-400 transition-all duration-1000"
                  style={{
                    width: `${Math.min(100, Math.max(0, remaining / task.window_seconds * 100))}%`,
                  }}
                />
              </div>
            </div>
          )}

          {polling && (
            <div className="mt-3 flex items-center gap-2 text-[10px] text-slate-600">
              <RefreshCw size={11} className="animate-spin" />
              每 3 秒自动查询，无需刷新页面
            </div>
          )}
        </div>
      )}

      {/* Step 3 — the code, or the free change button */}
      {received && task?.otp_code && (
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-500/15 text-[10px] text-emerald-200">3</span>
            <p className="text-[11px] font-medium text-emerald-200">验证码已收到</p>
          </div>
          <div className="mt-3 rounded-2xl border border-emerald-400/30 bg-emerald-400/[0.08] p-5 text-center">
            <p className="select-all font-mono text-[40px] font-semibold tracking-[0.18em] text-emerald-200">
              {task.otp_code}
            </p>
            <CopyField value={task.otp_code} label="验证码" />
          </div>
          {task.raw_sms && (
            <div className="mt-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5">
              <p className="text-[10px] text-slate-600">短信原文</p>
              <p className="mt-1.5 text-[11px] leading-5 text-slate-400">{task.raw_sms}</p>
            </div>
          )}
          <p className="mt-3 inline-flex items-center gap-1.5 text-[10px] text-emerald-300/80">
            <CheckCircle2 size={11} />
            本次接码已完成，激活码已核销
          </p>
        </div>
      )}

      {polling && (
        <div className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-4">
          {onCooldown ? (
            <button type="button" onClick={() => void changeNumber()} disabled={busy} className="btn-secondary w-full">
              <RefreshCw size={13} className={busy ? "animate-spin" : ""} />
              未收到验证码？点击免费更换新号码（不扣减激活码次数）
            </button>
          ) : (
            <p className="text-center text-[10px] leading-5 text-slate-600">
              45 秒后仍未收到即可免费换号，号码与激活码都不会损失
            </p>
          )}
        </div>
      )}

      {ended && (
        <div className="rounded-xl border border-amber-400/25 bg-amber-400/[0.06] p-4">
          <p className="flex items-center gap-2 text-[11px] text-amber-200">
            <Smartphone size={13} />
            {expiredLabel}
          </p>
          <p className="mt-1.5 text-[10px] leading-5 text-amber-200/70">
            激活码已恢复可用，重新取号不会扣减次数。
          </p>
          <button type="button" onClick={() => void take()} disabled={busy} className="btn-primary mt-3 w-full py-2.5">
            <RefreshCw size={13} />
            重新取号
          </button>
        </div>
      )}

      <p className="flex items-start gap-2 text-[10px] leading-5 text-slate-600">
        <ShieldCheck size={12} className="mt-0.5 shrink-0 text-slate-600" />
        收到验证码才核销激活码；超时或换号会同步向上游取消并退款，你与平台都不扣钱。
      </p>
    </div>
  );
}
