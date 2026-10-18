"use client";

import {
  BellRing,
  Bitcoin,
  CheckCircle2,
  Coins,
  Copy,
  CreditCard,
  Loader2,
  MessageCircle,
  Package,
  Save,
  ServerCog,
  Settings2,
  ShieldCheck,
  WalletCards,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CopyButton } from "@/components/CopyButton";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatDateTime } from "@/lib/format";
import type { AdminSettings, FeishuEventOption } from "@/lib/types";

type FormValue = string | number | boolean;

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="admin-label">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-[9px] leading-4 text-slate-600">{hint}</span>}
    </label>
  );
}

function Toggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-4 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-left transition hover:border-white/15"
    >
      <span>
        <span className="block text-[11px] text-slate-300">{label}</span>
        {description && <span className="mt-1 block text-[9px] leading-4 text-slate-600">{description}</span>}
      </span>
      <span className={`relative h-5 w-9 shrink-0 rounded-full transition ${checked ? "bg-brand-500" : "bg-slate-700"}`}>
        <span
          className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition ${checked ? "left-[18px]" : "left-0.5"}`}
        />
      </span>
    </button>
  );
}

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] ${
        ok
          ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
          : "border-amber-400/20 bg-amber-400/10 text-amber-300"
      }`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}

function Section({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: typeof Settings2;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="admin-card">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-brand-400/15 bg-brand-500/[0.08] text-brand-300">
          <Icon size={16} />
        </span>
        <div>
          <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
          <p className="mt-1 text-[10px] leading-5 text-slate-600">{description}</p>
        </div>
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}

export default function AdminSettingsPage() {
  const [data, setData] = useState<AdminSettings | null>(null);
  const [values, setValues] = useState<Record<string, FormValue>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [clearSecrets, setClearSecrets] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api<AdminSettings>("/api/v1/admin/settings");
      setData(result);
      setValues(result.values);
      setSecrets({});
      setClearSecrets([]);
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const text = useCallback(
    (key: string, fallback = "") => {
      const value = values[key];
      return value === undefined || value === null ? fallback : String(value);
    },
    [values],
  );
  const bool = useCallback(
    (key: string, fallback = false) => {
      const value = values[key];
      return value === undefined || value === null ? fallback : Boolean(value);
    },
    [values],
  );

  function setValue(key: string, value: FormValue) {
    setSaved(false);
    setValues((current) => ({ ...current, [key]: value }));
  }

  const selectedEvents = useMemo(
    () => new Set(text("feishu_events").split(",").filter(Boolean)),
    [text],
  );

  function toggleEvent(option: FeishuEventOption) {
    const next = new Set(selectedEvents);
    if (next.has(option.key)) next.delete(option.key);
    else next.add(option.key);
    setValue("feishu_events", Array.from(next).join(","));
  }

  async function save(showToast = true): Promise<boolean> {
    setSaving(true);
    setError(null);
    setTestResult(null);
    try {
      const body: Record<string, FormValue> = { ...values };
      Object.entries(secrets).forEach(([key, value]) => {
        if (value.trim()) body[key] = value.trim();
      });
      const result = await api<AdminSettings>("/api/v1/admin/settings", {
        method: "PATCH",
        body: {
          values: body,
          clear_secrets: clearSecrets,
        },
      });
      setData(result);
      setValues(result.values);
      setSecrets({});
      setClearSecrets([]);
      if (showToast) {
        setSaved(true);
        window.setTimeout(() => setSaved(false), 2500);
      }
      return true;
    } catch (err) {
      setError(handleAdminError(err));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function testFeishu() {
    setTesting(true);
    setError(null);
    setTestResult(null);
    try {
      const ok = await save(false);
      if (!ok) return;
      const result = await api<{ result: { status: string; error?: string }; recent_notifications: AdminSettings["recent_notifications"] }>(
        "/api/v1/admin/feishu/test",
        { method: "POST" },
      );
      setTestResult(
        result.result.status === "sent"
          ? "测试消息已发送，请查看飞书群。"
          : `发送失败：${result.result.error || result.result.status}`,
      );
      setData((current) => (current ? { ...current, recent_notifications: result.recent_notifications } : current));
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setTesting(false);
    }
  }

  function secretProps(key: string, label: string, configured: boolean, hint?: string) {
    return (
      <Field label={label} hint={hint}>
        <div className="flex gap-2">
          <input
            type="password"
            value={secrets[key] || ""}
            onChange={(event) => {
              setSaved(false);
              setSecrets((current) => ({ ...current, [key]: event.target.value }));
              setClearSecrets((current) => current.filter((item) => item !== key));
            }}
            placeholder={configured ? "已配置，留空保持不变" : "尚未配置"}
            className="admin-input min-w-0 flex-1 py-2.5"
            autoComplete="new-password"
          />
          {configured && (
            <button
              type="button"
              onClick={() => {
                setSecrets((current) => ({ ...current, [key]: "" }));
                setClearSecrets((current) => (current.includes(key) ? current : [...current, key]));
              }}
              className={`shrink-0 rounded-lg border px-2.5 text-[10px] ${
                clearSecrets.includes(key)
                  ? "border-rose-400/30 bg-rose-400/10 text-rose-300"
                  : "border-white/10 text-slate-500 hover:text-slate-300"
              }`}
            >
              {clearSecrets.includes(key) ? "将清除" : "清除"}
            </button>
          )}
        </div>
      </Field>
    );
  }

  if (loading && !data) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-48" />
        <div className="admin-card h-40" />
        <div className="admin-card h-64" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="admin-card flex flex-col items-center gap-3 py-20 text-center">
        <p className="text-sm text-slate-400">{error || "无法加载设置"}</p>
        <button type="button" onClick={() => void load()} className="btn-soft">重新加载</button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">PLATFORM SETTINGS</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">系统设置</h1>
          <p className="mt-2 text-[11px] text-slate-500">
            支付、加密货币、上游、VCC、客服与飞书通知统一配置；密钥加密保存，页面不回显。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {saved && (
            <span className="inline-flex items-center gap-1.5 text-[10px] text-emerald-300">
              <CheckCircle2 size={13} /> 已保存
            </span>
          )}
          <button type="button" onClick={() => void save()} disabled={saving} className="btn-primary py-2.5">
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            保存配置
          </button>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        <StatusChip ok={data.payment_ready} label={data.payment_ready ? "支付密钥已配置" : "支付密钥待配置"} />
        <StatusChip ok={data.crypto_ready} label={data.crypto_ready ? "加密支付已配置" : "加密支付待配置"} />
        <StatusChip ok={data.upstream_ready} label={data.upstream_ready ? "上游通道已配置" : "上游通道待配置"} />
        <StatusChip ok={data.support_ready} label={data.support_ready ? "客服已配置" : "客服待配置"} />
        <StatusChip ok={data.feishu_ready} label={data.feishu_ready ? "飞书已配置" : "飞书待配置"} />
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Section icon={CreditCard} title="支付通道" description="微信/支付宝收款、模拟支付开关与回调地址。切换 live 前请先用小额真实支付测试。">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="支付模式">
              <select value={text("payment_mode", "mock")} onChange={(event) => setValue("payment_mode", event.target.value)} className="admin-input w-full py-2.5">
                <option value="mock">模拟支付</option>
                <option value="live">真实支付</option>
              </select>
            </Field>
            <Field label="主支付网关">
              <select value={text("payment_gateway", "ltzf")} onChange={(event) => setValue("payment_gateway", event.target.value)} className="admin-input w-full py-2.5">
                <option value="ltzf">蓝兔支付</option>
                <option value="xunhupay">虎皮椒</option>
              </select>
            </Field>
            <Field label="模拟支付按钮">
              <select value={bool("mock_payment_enabled", true) ? "true" : "false"} onChange={(event) => setValue("mock_payment_enabled", event.target.value === "true")} className="admin-input w-full py-2.5">
                <option value="true">允许（仅演示）</option>
                <option value="false">关闭</option>
              </select>
            </Field>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Field label="蓝兔商户号">
              <input value={text("ltzf_mch_id")} onChange={(event) => setValue("ltzf_mch_id", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="蓝兔 AppID">
              <input value={text("ltzf_app_id")} onChange={(event) => setValue("ltzf_app_id", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="蓝兔 API 地址">
              <input value={text("ltzf_api_base")} onChange={(event) => setValue("ltzf_api_base", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            {secretProps("ltzf_key", "蓝兔签名密钥", data.secret_configured.ltzf_key)}
            <Field label="蓝兔回调地址" hint="在蓝兔商户后台填写">
              <div className="flex gap-2">
                <input readOnly value={data.callbacks.ltzf} className="admin-input min-w-0 flex-1 py-2.5 text-slate-500" />
                <CopyButton value={data.callbacks.ltzf} compact />
              </div>
            </Field>
            <Field label="虎皮椒 AppID">
              <input value={text("xunhupay_app_id")} onChange={(event) => setValue("xunhupay_app_id", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            {secretProps("xunhupay_app_secret", "虎皮椒 AppSecret", data.secret_configured.xunhupay_app_secret)}
            <Field label="虎皮椒回调地址" hint="在虎皮椒商户后台填写">
              <div className="flex gap-2">
                <input readOnly value={data.callbacks.xunhupay} className="admin-input min-w-0 flex-1 py-2.5 text-slate-500" />
                <CopyButton value={data.callbacks.xunhupay} compact />
              </div>
            </Field>
            <Field label="虎皮椒 API 地址">
              <input value={text("xunhupay_api_base")} onChange={(event) => setValue("xunhupay_api_base", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="虎皮椒返回地址">
              <input value={text("xunhupay_return_url")} onChange={(event) => setValue("xunhupay_return_url", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
          </div>
        </Section>

        <Section icon={Bitcoin} title="加密货币支付" description="NOWPayments 托管收银台，支持 USDT / USDC / BTC / ETH。商户后台需开启对应币种。">
          <Toggle
            label="启用加密货币支付"
            description="保存后前台结算页会自动显示该选项"
            checked={bool("crypto_enabled")}
            onChange={(checked) => setValue("crypto_enabled", checked)}
          />
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {secretProps("nowpayments_api_key", "NOWPayments API Key", data.secret_configured.nowpayments_api_key)}
            {secretProps("nowpayments_ipn_secret", "NOWPayments IPN Secret", data.secret_configured.nowpayments_ipn_secret)}
            <Field label="CNY / USD 汇率" hint="用于把人民币订单金额折算成美元发票">
              <input type="number" step="0.01" value={text("crypto_cny_per_usd", "7.20")} onChange={(event) => setValue("crypto_cny_per_usd", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="支持的币种">
              <input value={text("crypto_supported_coins")} onChange={(event) => setValue("crypto_supported_coins", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="API 地址">
              <input value={text("nowpayments_api_base")} onChange={(event) => setValue("nowpayments_api_base", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="IPN 回调地址" hint="填写到 NOWPayments 后台">
              <div className="flex gap-2">
                <input readOnly value={data.callbacks.nowpayments} className="admin-input min-w-0 flex-1 py-2.5 text-slate-500" />
                <CopyButton value={data.callbacks.nowpayments} compact />
              </div>
            </Field>
          </div>
        </Section>

        <Section icon={ServerCog} title="上游代充通道" description="接入供应商自动代充 API；mock 模式只用于演示，不能接真实客户。">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="通道模式">
              <select value={text("upstream_mode", "mock")} onChange={(event) => setValue("upstream_mode", event.target.value)} className="admin-input w-full py-2.5">
                <option value="mock">模拟</option>
                <option value="http">真实 HTTP</option>
              </select>
            </Field>
            <Field label="API Base">
              <input value={text("upstream_api_base")} onChange={(event) => setValue("upstream_api_base", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            {secretProps("upstream_api_key", "API Key", data.secret_configured.upstream_api_key)}
            {secretProps("upstream_api_secret", "API Secret", data.secret_configured.upstream_api_secret)}
            <Field label="最长等待秒数">
              <input type="number" value={text("upstream_max_wait_seconds", "180")} onChange={(event) => setValue("upstream_max_wait_seconds", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="轮询间隔秒数">
              <input type="number" step="0.5" value={text("upstream_poll_interval_seconds", "5")} onChange={(event) => setValue("upstream_poll_interval_seconds", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
          </div>
        </Section>

        <Section icon={WalletCards} title="自营 VCC 通道" description="RDVCC 卡池、美国住宅代理与 IMAP 3DS 邮箱。首次启用请保持演练模式。">
          <div className="grid gap-3 sm:grid-cols-2">
            <Toggle label="启用自营通道" checked={bool("self_vcc_enabled")} onChange={(checked) => setValue("self_vcc_enabled", checked)} />
            <Toggle label="演练模式" description="开启时停在提交支付前" checked={bool("self_vcc_dry_run", true)} onChange={(checked) => setValue("self_vcc_dry_run", checked)} />
            <Field label="全局路由策略" hint="UPSTREAM_ONLY 可作为维护期总开关">
              <select value={text("routing_policy", "HYBRID_SELF_FIRST")} onChange={(event) => setValue("routing_policy", event.target.value)} className="admin-input w-full py-2.5">
                <option value="HYBRID_SELF_FIRST">自营优先，失败降级上游</option>
                <option value="SELF_VCC_ONLY">仅自营</option>
                <option value="UPSTREAM_ONLY">仅上游</option>
              </select>
            </Field>
            <Toggle label="支付成功后自动取消续订" description="尽力而为；失败会提示人工确认" checked={bool("self_vcc_cancel_autorenew", true)} onChange={(checked) => setValue("self_vcc_cancel_autorenew", checked)} />
            <Field label="菲律宾区最低余额（USD）">
              <input type="number" step="0.01" value={text("self_vcc_ph_min_balance", "18.5")} onChange={(event) => setValue("self_vcc_ph_min_balance", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="美区最低余额（USD）">
              <input type="number" step="0.01" value={text("self_vcc_us_min_balance", "22")} onChange={(event) => setValue("self_vcc_us_min_balance", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            {secretProps("ph_residential_proxy_url", "菲律宾住宅代理 URL", data.secret_configured.ph_residential_proxy_url, "优先用于 ₱999 结账")}
            {secretProps("us_residential_proxy_url", "美国住宅代理 URL", data.secret_configured.us_residential_proxy_url, "用于美区 $20 兜底")}
            <Field label="美国 BIN 白名单">
              <input value={text("us_bin_allowlist")} onChange={(event) => setValue("us_bin_allowlist", event.target.value)} className="admin-input w-full py-2.5" placeholder="逗号分隔，可留空" />
            </Field>
            <Field label="结账 URL 模板（可选）" hint="支持 {access_token} {user_id} {region} {currency} {amount}">
              <input value={text("rdvcc_checkout_url_template")} onChange={(event) => setValue("rdvcc_checkout_url_template", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="代理健康检查地址">
              <input value={text("proxy_health_url")} onChange={(event) => setValue("proxy_health_url", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="IMAP 主机">
              <input value={text("rdvcc_imap_host")} onChange={(event) => setValue("rdvcc_imap_host", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="IMAP 端口">
              <input type="number" value={text("rdvcc_imap_port", "993")} onChange={(event) => setValue("rdvcc_imap_port", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="IMAP 用户">
              <input value={text("rdvcc_imap_user")} onChange={(event) => setValue("rdvcc_imap_user", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            {secretProps("rdvcc_imap_password", "IMAP 密码/授权码", data.secret_configured.rdvcc_imap_password)}
            <Field label="3DS 发件人关键词">
              <input value={text("rdvcc_3ds_sender")} onChange={(event) => setValue("rdvcc_3ds_sender", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="验证码正则">
              <input value={text("rdvcc_3ds_code_regex")} onChange={(event) => setValue("rdvcc_3ds_code_regex", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
          </div>
        </Section>

        <Section icon={MessageCircle} title="客服设置" description="客服微信号与在线时间会直接显示在帮助中心，无需重新构建前端。">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="客服微信">
              <input value={text("support_wechat")} onChange={(event) => setValue("support_wechat", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="在线时间">
              <input value={text("support_hours")} onChange={(event) => setValue("support_hours", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
          </div>
        </Section>

        <Section icon={BellRing} title="飞书管理通知" description="配置飞书自定义机器人 Webhook，并勾选需要通知的生产事件。">
          <div className="grid gap-3 sm:grid-cols-2">
            {secretProps("feishu_webhook_url", "飞书 Webhook URL", data.secret_configured.feishu_webhook_url, "群机器人 → 添加自定义机器人 → 复制 Webhook")}
            {secretProps("feishu_secret", "签名校验 Secret", data.secret_configured.feishu_secret, "可选；启用签名校验时填写")}
            <Field label="库存预警阈值">
              <input type="number" value={text("feishu_low_stock_threshold", "5")} onChange={(event) => setValue("feishu_low_stock_threshold", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Toggle label="严重事件 @所有人" description="仅警告/错误级别通知生效" checked={bool("feishu_mention_all")} onChange={(checked) => setValue("feishu_mention_all", checked)} />
          </div>
          <div className="mt-3">
            <Toggle
              label="启用飞书通知"
              description="保存后在后台产生勾选的事件时发送"
              checked={bool("feishu_enabled")}
              onChange={(checked) => setValue("feishu_enabled", checked)}
            />
          </div>
          <div className="mt-4">
            <p className="admin-label">通知事件</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {data.feishu_event_options.map((option) => {
                const checked = selectedEvents.has(option.key);
                return (
                  <button
                    type="button"
                    key={option.key}
                    onClick={() => toggleEvent(option)}
                    className={`flex items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                      checked ? "border-brand-400/25 bg-brand-500/[0.07]" : "border-white/[0.06] bg-white/[0.015] hover:border-white/15"
                    }`}
                  >
                    <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${checked ? "border-brand-400 bg-brand-500 text-white" : "border-slate-600"}`}>
                      {checked && <CheckCircle2 size={11} />}
                    </span>
                    <span>
                      <span className="block text-[10px] text-slate-300">{option.label}</span>
                      <span className="mt-0.5 block text-[9px] leading-4 text-slate-600">{option.description}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => void testFeishu()} disabled={testing || saving} className="btn-soft py-2.5">
              {testing ? <Loader2 size={13} className="animate-spin" /> : <BellRing size={13} />}
              {testing ? "正在发送..." : "保存并发送测试消息"}
            </button>
            {testResult && (
              <span className={`text-[10px] ${testResult.startsWith("测试消息") ? "text-emerald-300" : "text-rose-300"}`}>
                {testResult}
              </span>
            )}
          </div>
          <div className="mt-4">
            <p className="admin-label">最近通知</p>
            {data.recent_notifications.length ? (
              <div className="overflow-hidden rounded-xl border border-white/[0.06]">
                <table className="admin-table text-[10px]">
                  <thead>
                    <tr><th>事件</th><th>状态</th><th>时间</th><th>说明</th></tr>
                  </thead>
                  <tbody>
                    {data.recent_notifications.map((item) => (
                      <tr key={item.id}>
                        <td className="text-slate-400">{item.title}</td>
                        <td className={item.status === "sent" ? "text-emerald-300" : "text-rose-300"}>{item.status === "sent" ? "已发送" : "失败"}</td>
                        <td className="text-slate-600">{formatDateTime(item.created_at)}</td>
                        <td className="max-w-[180px] truncate text-slate-600">{item.error || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-[10px] text-slate-600">还没有通知记录。</p>
            )}
          </div>
        </Section>

        <Section icon={Package} title="订单与库存" description="控制下单后的支付有效期与库存拦截。">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="订单支付有效期（分钟）">
              <input type="number" value={text("order_expire_minutes", "15")} onChange={(event) => setValue("order_expire_minutes", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Toggle
              label="缺货时拦截下单"
              description="关闭后允许缺货下单，订单会进入备货状态"
              checked={bool("enforce_stock", true)}
              onChange={(checked) => setValue("enforce_stock", checked)}
            />
          </div>
        </Section>

        <Section icon={MessageCircle} title="SMTP 发货邮件（可选）" description="系统不依赖邮件；配置后支付成功可额外发送交付通知。">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="SMTP 主机">
              <input value={text("smtp_host")} onChange={(event) => setValue("smtp_host", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="端口">
              <input type="number" value={text("smtp_port", "465")} onChange={(event) => setValue("smtp_port", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="SMTP 用户">
              <input value={text("smtp_user")} onChange={(event) => setValue("smtp_user", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            {secretProps("smtp_password", "SMTP 密码/授权码", data.secret_configured.smtp_password)}
            <Field label="发件人">
              <input value={text("smtp_from")} onChange={(event) => setValue("smtp_from", event.target.value)} className="admin-input w-full py-2.5" />
            </Field>
            <Field label="SSL">
              <select value={bool("smtp_use_ssl", true) ? "true" : "false"} onChange={(event) => setValue("smtp_use_ssl", event.target.value === "true")} className="admin-input w-full py-2.5">
                <option value="true">SSL（465）</option>
                <option value="false">STARTTLS</option>
              </select>
            </Field>
          </div>
        </Section>
      </div>

      <div className="mt-5 flex items-center justify-between gap-3 rounded-2xl border border-white/[0.06] bg-white/[0.015] px-4 py-3">
        <p className="text-[10px] leading-5 text-slate-600">
          密钥保存后不会回显；设置通过数据库覆盖环境变量，约 2 秒内生效。管理员密码、AES 主密钥和域名仍由 `.env` 管理。
        </p>
        <button type="button" onClick={() => void save()} disabled={saving} className="btn-primary shrink-0 py-2.5">
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          保存配置
        </button>
      </div>
    </div>
  );
}
