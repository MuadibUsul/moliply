"use client";

import { ExternalLink } from "lucide-react";

const CHATGPT_LOGIN = "https://chatgpt.com";
const CHATGPT_SESSION_API = "https://chatgpt.com/api/auth/session";

/**
 * Three concrete clicks from "logged out" to "JSON on the clipboard".
 *
 * Customers stall on step 2 far more than anywhere else in the flow, so the
 * links open in a new tab and the copy instruction sits between them instead of
 * inside a modal.
 */
export function SessionExtractGuide({ kind }: { kind: "chatgpt" | "claude" | "gemini" }) {
  if (kind === "gemini") {
    return (
      <ol className="mt-5 space-y-2.5">
        <li className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
          <p className="text-[11px] font-medium text-slate-200">① 填写您自己的 Gmail / Google 邮箱</p>
          <p className="mt-1 text-[10px] leading-5 text-slate-500">
            无需密码、无需提取 Cookie，直接输入您要升级 Gemini Advanced 的 Google 邮箱地址即可
          </p>
        </li>
        <li className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
          <p className="text-[11px] font-medium text-slate-200">② 系统自动分配 Google One AI Premium (2TB) 家庭组车位</p>
          <p className="mt-1 text-[10px] leading-5 text-slate-500">
            每个家庭主号最多共享 5 个独立席位，聊天记录与云盘文件 100% 隔离保密
          </p>
        </li>
        <li className="rounded-xl border border-brand-400/20 bg-brand-500/[0.07] px-3.5 py-3">
          <p className="text-[11px] font-medium text-slate-100">③ 查收 Gmail 邀请邮件或点击专属链接确认加入</p>
          <p className="mt-1 text-[10px] leading-5 text-slate-400">
            提交后打开 Gmail 邮箱点击 Google Family 官方邀请链接确认，即可直享 Gemini Advanced + 2TB 云空间
          </p>
        </li>
      </ol>
    );
  }

  if (kind === "claude") {
    return (
      <ol className="mt-5 space-y-2.5">
        <li className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
          <p className="text-[11px] font-medium text-slate-200">① 登录 Claude 网页版</p>
          <a
            href="https://claude.ai"
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-flex items-center gap-1 text-[10px] text-brand-300 hover:text-brand-200"
          >
            claude.ai
            <ExternalLink size={10} />
          </a>
        </li>
        <li className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
          <p className="text-[11px] font-medium text-slate-200">② 打开浏览器开发者工具</p>
          <p className="mt-1 text-[10px] leading-5 text-slate-500">
            按 F12（或 Cmd+Option+I），切到 Application / Storage → Cookies → claude.ai
          </p>
        </li>
        <li className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
          <p className="text-[11px] font-medium text-slate-200">③ 复制 sessionKey 的值并粘贴到下方</p>
          <p className="mt-1 text-[10px] leading-5 text-slate-500">
            只复制以 sk-ant- 开头的那一串，不要带 <span className="font-mono">sessionKey=</span> 以外的内容
          </p>
        </li>
      </ol>
    );
  }

  return (
    <ol className="mt-5 space-y-2.5">
      <li className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
        <a
          href={CHATGPT_LOGIN}
          target="_blank"
          rel="noreferrer"
          className="flex items-center justify-between gap-2 text-[11px] font-medium text-slate-200 hover:text-white"
        >
          <span>① 登录 ChatGPT 网页版</span>
          <span className="inline-flex items-center gap-1 text-[10px] text-brand-300">
            chatgpt.com
            <ExternalLink size={10} />
          </span>
        </a>
        <p className="mt-1 text-[10px] text-slate-500">确认已登录、且能正常打开对话页</p>
      </li>
      <li className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-3">
        <a
          href={CHATGPT_SESSION_API}
          target="_blank"
          rel="noreferrer"
          className="flex items-center justify-between gap-2 text-[11px] font-medium text-slate-200 hover:text-white"
        >
          <span>② 点击打开凭证接口页</span>
          <span className="inline-flex items-center gap-1 text-[10px] text-brand-300">
            /api/auth/session
            <ExternalLink size={10} />
          </span>
        </a>
        <p className="mt-1 text-[10px] text-slate-500">页面会显示一段纯 JSON，这是充值唯一需要的凭证</p>
      </li>
      <li className="rounded-xl border border-brand-400/20 bg-brand-500/[0.07] px-3.5 py-3">
        <p className="text-[11px] font-medium text-slate-100">③ 全选复制并粘贴到下方</p>
        <p className="mt-1 text-[10px] leading-5 text-slate-400">
          Ctrl+A / Cmd+A 全选 → Ctrl+C / Cmd+C 复制 → 粘贴到下面的输入框
        </p>
      </li>
    </ol>
  );
}
