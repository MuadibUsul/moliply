"use client";

import { ExternalLink, ShieldCheck } from "lucide-react";
import { CopyButton } from "@/components/CopyButton";
import { Modal } from "@/components/Modal";

const SESSION_URL = "https://chatgpt.com/api/auth/session";

export function SessionGuideModal({
  open,
  onClose,
  kind,
}: {
  open: boolean;
  onClose: () => void;
  kind: "chatgpt" | "claude";
}) {
  const chatgptSteps = [
    "在同一个浏览器中登录需要充值的 ChatGPT 账号。",
    `打开 ${SESSION_URL}，页面会显示一段以 { 开头的 JSON。`,
    "全选并复制整段 JSON，不要修改或截断内容。",
    "回到充能中心粘贴内容，提交后等待自动核验与充值。",
  ];
  const claudeSteps = [
    "在同一个浏览器中登录需要充值的 Claude 账号。",
    "打开浏览器开发者工具（F12）→ Application / 存储 → Cookies。",
    "找到 claude.ai 下的 sessionKey，复制它的值（通常以 sk-ant- 开头）。",
    "回到充能中心粘贴 sessionKey，提交后等待上游通道完成充值。",
  ];
  const steps = kind === "chatgpt" ? chatgptSteps : claudeSteps;

  return (
    <Modal open={open} onClose={onClose} title={kind === "chatgpt" ? "如何获取 ChatGPT Session" : "如何获取 Claude Session"} maxWidth="max-w-xl">
      <div className="space-y-3">
        {kind === "chatgpt" && (
          <div className="flex items-center gap-2 rounded-xl border border-white/[0.07] bg-white/[0.025] p-3">
            <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-300">{SESSION_URL}</code>
            <CopyButton value={SESSION_URL} label="复制网址" />
          </div>
        )}
        {steps.map((step, index) => (
          <div key={step} className="flex gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-brand-400/25 bg-brand-500/10 font-mono text-[10px] text-brand-300">
              {index + 1}
            </span>
            <p className="text-[11px] leading-6 text-slate-400">{step}</p>
          </div>
        ))}
        {kind === "chatgpt" && (
          <a
            href={SESSION_URL}
            target="_blank"
            rel="noreferrer"
            className="btn-soft w-full"
          >
            打开 Session 页面
            <ExternalLink size={13} />
          </a>
        )}
        <div className="flex items-start gap-2 rounded-xl border border-emerald-400/15 bg-emerald-400/[0.045] p-3.5 text-[10px] leading-5 text-emerald-200/75">
          <ShieldCheck size={14} className="mt-0.5 shrink-0" />
          请勿向任何人提供账号密码。Session 仅用于本次订阅升级，任务完成后系统会在 5 分钟内清除登录凭证。
        </div>
      </div>
    </Modal>
  );
}
