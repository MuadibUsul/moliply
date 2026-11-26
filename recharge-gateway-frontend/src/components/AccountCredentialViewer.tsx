"use client";

import { KeyRound, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { CopyButton } from "@/components/CopyButton";

const BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function decodeBase32(secret: string): Uint8Array | null {
  const cleaned = secret.toUpperCase().replace(/[\s\-=]/g, "");
  if (cleaned.length < 12) return null;
  let bits = 0;
  let value = 0;
  const bytes: number[] = [];
  for (let i = 0; i < cleaned.length; i++) {
    const idx = BASE32_ALPHABET.indexOf(cleaned[i]);
    if (idx === -1) return null;
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return new Uint8Array(bytes);
}

async function generateTotp(secret: string, timestampMs = Date.now()): Promise<string | null> {
  if (typeof window === "undefined" || !window.crypto?.subtle) return null;
  const keyBytes = decodeBase32(secret);
  if (!keyBytes || keyBytes.length === 0) return null;
  const counter = Math.floor(timestampMs / 1000 / 30);
  const buffer = new ArrayBuffer(8);
  const view = new DataView(buffer);
  view.setUint32(0, Math.floor(counter / 0x100000000), false);
  view.setUint32(4, counter >>> 0, false);

  try {
    const cryptoKey = await window.crypto.subtle.importKey(
      "raw",
      keyBytes.buffer as ArrayBuffer,
      { name: "HMAC", hash: "SHA-1" },
      false,
      ["sign"],
    );
    const sig = new Uint8Array(await window.crypto.subtle.sign("HMAC", cryptoKey, buffer));
    const offset = sig[sig.length - 1] & 0x0f;
    const binary =
      ((sig[offset] & 0x7f) << 24) |
      ((sig[offset + 1] & 0xff) << 16) |
      ((sig[offset + 2] & 0xff) << 8) |
      (sig[offset + 3] & 0xff);
    return String(binary % 1_000_000).padStart(6, "0");
  } catch {
    return null;
  }
}

type ParsedCredentials = {
  email?: string;
  password?: string;
  recoveryEmail?: string;
  totpSecret?: string;
  extra?: string;
};

function looksLikeBase32Secret(token: string): boolean {
  const cleaned = token.trim().replace(/\s+/g, "");
  if (cleaned.length < 12 || cleaned.length > 64) return false;
  if (/^\d{4}-\d{2}-\d{2}$/.test(cleaned)) return false;
  return /^[A-Z2-7]+=*$/i.test(cleaned) && decodeBase32(cleaned) !== null;
}

function parseCredentialPayload(payload: string): ParsedCredentials | null {
  const firstLine = payload.trim().split(/\r?\n/)[0] || "";
  if (!firstLine.includes("----")) return null;
  const parts = firstLine.split("----").map((item) => item.trim());
  if (parts.length < 2 || !parts[0].includes("@")) return null;
  const result: ParsedCredentials = {
    email: parts[0],
    password: parts[1],
  };
  if (parts[2]) {
    if (parts[2].includes("@")) {
      result.recoveryEmail = parts[2];
    } else if (looksLikeBase32Secret(parts[2])) {
      result.totpSecret = parts[2].toUpperCase();
    } else {
      result.extra = parts[2];
    }
  }
  if (parts[3]) {
    if (looksLikeBase32Secret(parts[3])) {
      result.totpSecret = parts[3].toUpperCase();
    } else if (!result.extra) {
      result.extra = parts[3];
    }
  }
  return result;
}

export function AccountCredentialViewer({ payload }: { payload: string | null }) {
  const parsed = useMemo(() => (payload ? parseCredentialPayload(payload) : null), [payload]);
  const [totpCode, setTotpCode] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState<number>(30);

  useEffect(() => {
    const secret = parsed?.totpSecret;
    if (!secret) {
      setTotpCode(null);
      return;
    }
    let mounted = true;
    const tick = () => {
      const now = Date.now();
      const rem = 30 - (Math.floor(now / 1000) % 30);
      setSecondsLeft(rem);
      void generateTotp(secret, now).then((code) => {
        if (mounted) setTotpCode(code);
      });
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [parsed?.totpSecret]);

  if (!payload) return null;

  return (
    <div>
      <pre className="whitespace-pre-wrap break-all font-mono text-[11px] leading-6 text-slate-300">{payload}</pre>

      {parsed && (
        <div className="mt-3 space-y-2 border-t border-white/[0.06] pt-3">
          <div className="grid gap-2 sm:grid-cols-2">
            {parsed.email && (
              <div className="flex items-center justify-between gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2">
                <div className="min-w-0">
                  <p className="text-[10px] text-slate-500">登录账号</p>
                  <p className="truncate font-mono text-[11px] text-slate-200">{parsed.email}</p>
                </div>
                <CopyButton value={parsed.email} label="复制账号" />
              </div>
            )}
            {parsed.password && (
              <div className="flex items-center justify-between gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2">
                <div className="min-w-0">
                  <p className="text-[10px] text-slate-500">登录密码</p>
                  <p className="truncate font-mono text-[11px] text-slate-200">{parsed.password}</p>
                </div>
                <CopyButton value={parsed.password} label="复制密码" />
              </div>
            )}
          </div>

          {parsed.recoveryEmail && (
            <div className="flex items-center justify-between gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2">
              <div className="min-w-0">
                <p className="text-[10px] text-slate-500">辅助验证邮箱</p>
                <p className="truncate font-mono text-[11px] text-slate-300">{parsed.recoveryEmail}</p>
              </div>
              <CopyButton value={parsed.recoveryEmail} label="复制辅邮" />
            </div>
          )}

          {parsed.totpSecret && (
            <div className="rounded-xl border border-brand-400/25 bg-brand-500/[0.07] p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-500/20 text-brand-300">
                    <KeyRound size={14} />
                  </span>
                  <div>
                    <p className="text-[11px] font-medium text-slate-100">
                      2FA 两步验证动态码（每 30 秒自动刷新 · 剩余 {secondsLeft}s）
                    </p>
                    <p className="text-[10px] text-slate-400">登录提示输入 Google Authenticator 验证码时直接复制下方 6 位数字</p>
                  </div>
                </div>
                {totpCode && <CopyButton value={totpCode} label="复制 6 位验证码" />}
              </div>
              <div className="mt-2.5 flex items-center justify-between rounded-lg border border-white/[0.08] bg-ink-950/70 px-3 py-2">
                <span className="font-mono text-lg font-bold tracking-[0.25em] text-emerald-300">
                  {totpCode ? `${totpCode.slice(0, 3)} ${totpCode.slice(3)}` : "计算中..."}
                </span>
                <span className="inline-flex items-center gap-1 text-[10px] text-slate-500">
                  <ShieldCheck size={12} className="text-emerald-400" />
                  RFC 6238 实时计算
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
