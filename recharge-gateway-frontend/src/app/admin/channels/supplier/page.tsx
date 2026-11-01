"use client";

import Link from "next/link";
import { ArrowLeft, BadgeCheck, CircleDollarSign, KeyRound, Link2, Loader2, PlugZap, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";

type SupplierConfig = {
  supplier: string;
  configured: boolean;
  source: "database" | "environment" | "none";
  token_hint: string;
  api_base: string;
  sku_map: string;
  catalog_race: string;
  sku_options: string;
  enabled: boolean;
  fulfill_mode: string;
};

/** Store -> upstream-service link, saved in the store, not on the service. */
type LinkSettings = {
  values: Record<string, string>;
  secret_configured: Record<string, boolean>;
};

type Quote = { sku: string; code: string; price_cents?: number; priceText?: string; error?: string };

type ProbeResult = {
  shop: string | null;
  balance: string | null;
  quotes: Quote[];
};

const SOURCE_LABEL: Record<SupplierConfig["source"], string> = {
  database: "后台已保存",
  environment: "来自服务器环境变量",
  none: "尚未配置",
};

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="admin-label">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-[10px] leading-5 text-slate-600">{hint}</span>}
    </label>
  );
}

export default function SupplierChannelPage() {
  const [config, setConfig] = useState<SupplierConfig | null>(null);
  const [token, setToken] = useState("");
  const [form, setForm] = useState({ api_base: "", sku_map: "", catalog_race: "", sku_options: "", enabled: false });
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [link, setLink] = useState<LinkSettings | null>(null);
  const [linkValues, setLinkValues] = useState<Record<string, string>>({});
  const [linkSecrets, setLinkSecrets] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingLink, setSavingLink] = useState(false);
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [data, linkData] = await Promise.all([
        api<SupplierConfig>("/api/v1/admin/channels/supplier"),
        api<LinkSettings>("/api/v1/admin/settings"),
      ]);
      setConfig(data);
      setLink(linkData);
      setForm({
        api_base: data.api_base || "",
        sku_map: data.sku_map || "",
        catalog_race: data.catalog_race || "",
        sku_options: data.sku_options || "",
        enabled: data.enabled,
      });
      setLinkValues({
        upstream_mode: linkData.values.upstream_mode || "mock",
        upstream_api_base: linkData.values.upstream_api_base || "",
        upstream_allowed_hosts: linkData.values.upstream_allowed_hosts || "",
        upstream_max_wait_seconds: linkData.values.upstream_max_wait_seconds || "180",
        upstream_poll_interval_seconds: linkData.values.upstream_poll_interval_seconds || "5",
      });
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await api("/api/v1/admin/channels/supplier", {
        method: "POST",
        body: {
          token: token.trim() || null,
          api_base: form.api_base.trim(),
          sku_map: form.sku_map.trim(),
          catalog_race: form.catalog_race.trim(),
          sku_options: form.sku_options.trim(),
          enabled: form.enabled,
        },
      });
      setToken("");
      setNotice("已保存");
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setSaving(false);
    }
  }

  async function saveLink() {
    setSavingLink(true);
    setError(null);
    setNotice(null);
    try {
      const values: Record<string, string> = { ...linkValues };
      if (linkSecrets.upstream_api_key?.trim()) values.upstream_api_key = linkSecrets.upstream_api_key.trim();
      if (linkSecrets.upstream_api_secret?.trim()) values.upstream_api_secret = linkSecrets.upstream_api_secret.trim();
      const result = await api<LinkSettings>("/api/v1/admin/settings", { method: "PATCH", body: { values } });
      setLink(result);
      setLinkSecrets({});
      setNotice("通路参数已保存");
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setSavingLink(false);
    }
  }

  async function runProbe() {
    setProbing(true);
    setError(null);
    setNotice(null);
    setProbe(null);
    try {
      setProbe(await api<ProbeResult>("/api/v1/admin/channels/supplier/probe", { method: "POST" }));
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setProbing(false);
    }
  }

  const mappedCount = form.sku_map.split(",").filter((pair) => pair.includes("=") && pair.split("=")[1]?.trim()).length;

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <Link href="/admin/channels" className="inline-flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-300">
            <ArrowLeft size={12} />
            返回渠道状态
          </Link>
          <p className="eyebrow mt-3">SUPPLIER API</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">上游代充渠道</h1>
          <p className="mt-2 text-[11px] text-slate-500">
            密钥保存在上游服务进程内，不经过商城数据库。保存后立即生效，无需重启。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] text-slate-500 hover:text-slate-200"
          aria-label="刷新"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-[11px] leading-5 text-rose-200">
          {error}
        </div>
      )}
      {notice && (
        <div className="mt-4 rounded-xl border border-emerald-400/20 bg-emerald-400/[0.07] px-4 py-3 text-[11px] text-emerald-200">
          {notice}
        </div>
      )}

      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <p className="text-[9px] uppercase tracking-[0.18em] text-slate-600">密钥状态</p>
          <p className="mt-2 text-sm text-slate-200">{config ? SOURCE_LABEL[config.source] : "—"}</p>
          {config?.token_hint && <p className="mt-1 text-[10px] text-slate-600">{config.token_hint}...</p>}
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <p className="text-[9px] uppercase tracking-[0.18em] text-slate-600">已映射套餐</p>
          <p className="mt-2 text-sm text-slate-200">{mappedCount} 个</p>
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <p className="text-[9px] uppercase tracking-[0.18em] text-slate-600">履约模式</p>
          <p className="mt-2 text-sm text-slate-200">{config?.fulfill_mode || "—"}</p>
          <p className="mt-1 text-[10px] text-slate-600">
            {config?.fulfill_mode === "supplier" ? "自动承接订单" : "人工/库存模式，不会自动下单"}
          </p>
        </div>
      </div>

      <section className="admin-card mt-6">
        <div className="flex items-center gap-2.5">
          <KeyRound size={16} className="text-brand-300" />
          <h2 className="text-base font-semibold text-slate-100">接口凭据</h2>
        </div>

        <div className="mt-5 grid gap-4">
          <Field label="供应商密钥" hint="云枢控制台生成的统一密钥，以 ysu_ 开头。留空表示保持现有值。">
            <input
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder={config?.configured ? "已配置，留空保持不变" : "ysu_..."}
              className="admin-input w-full py-2.5"
              autoComplete="off"
            />
          </Field>

          <Field label="API Base" hint="默认 https://yunshutoken.com/partner/v1，一般不用改。">
            <input
              value={form.api_base}
              onChange={(event) => setForm({ ...form, api_base: event.target.value })}
              placeholder="https://yunshutoken.com/partner/v1"
              className="admin-input w-full py-2.5"
            />
          </Field>

          <Field
            label="套餐映射"
            hint="格式：本地 SKU=上游商品码，逗号分隔。上游商品码取自商品页，服务端会校验是否匹配。"
          >
            <textarea
              value={form.sku_map}
              onChange={(event) => setForm({ ...form, sku_map: event.target.value })}
              placeholder="CHATGPT_PLUS_1M=WZYP-PT1WSX,CLAUDE_PRO_1M=WZYP-NSYX8P"
              rows={3}
              className="admin-input w-full py-2.5 font-mono text-[11px]"
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="区服参数（可选）">
              <input
                value={form.catalog_race}
                onChange={(event) => setForm({ ...form, catalog_race: event.target.value })}
                className="admin-input w-full py-2.5"
              />
            </Field>
            <Field label="规格参数（可选）">
              <input
                value={form.sku_options}
                onChange={(event) => setForm({ ...form, sku_options: event.target.value })}
                className="admin-input w-full py-2.5"
              />
            </Field>
          </div>

          <label className="flex items-center gap-2.5 text-[11px] text-slate-400">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
              className="h-3.5 w-3.5 accent-brand-500"
            />
            启用该渠道
          </label>
        </div>

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-[11px] font-medium text-white disabled:opacity-50"
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <BadgeCheck size={13} />}
            保存配置
          </button>
          <button
            type="button"
            onClick={() => void runProbe()}
            disabled={probing || !config?.configured}
            className="inline-flex items-center gap-2 rounded-lg border border-white/[0.1] px-4 py-2 text-[11px] text-slate-300 disabled:opacity-40"
          >
            {probing ? <Loader2 size={13} className="animate-spin" /> : <PlugZap size={13} />}
            测试连接与取价
          </button>
        </div>
        <p className="mt-3 text-[10px] leading-5 text-slate-600">
          测试只调用余额与报价接口，不扣款、不下单。
        </p>
      </section>

      <section className="admin-card mt-4">
        <div className="flex items-center gap-2.5">
          <Link2 size={16} className="text-slate-300" />
          <h2 className="text-base font-semibold text-slate-100">商城 → 上游服务通路</h2>
        </div>
        <p className="mt-2 text-[11px] leading-6 text-slate-500">
          商城调用自建上游服务时使用的地址与签名密钥。服务部署时已自动生成，通常只需确认 API Base。
        </p>

        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field label="通道模式" hint="mock 只用于演示，接真实客户必须选真实 HTTP。">
            <select
              value={linkValues.upstream_mode || "mock"}
              onChange={(event) => setLinkValues({ ...linkValues, upstream_mode: event.target.value })}
              className="admin-input w-full py-2.5"
            >
              <option value="mock">模拟</option>
              <option value="http">真实 HTTP</option>
            </select>
          </Field>
          <Field label="API Base" hint="生产环境必须为 HTTPS。">
            <input
              value={linkValues.upstream_api_base || ""}
              onChange={(event) => setLinkValues({ ...linkValues, upstream_api_base: event.target.value })}
              placeholder="https://moliply.shop/upstream"
              className="admin-input w-full py-2.5"
            />
          </Field>
          <Field label="允许的上游域名（可选）" hint="逗号分隔；留空表示任意 HTTPS 域名。">
            <input
              value={linkValues.upstream_allowed_hosts || ""}
              onChange={(event) => setLinkValues({ ...linkValues, upstream_allowed_hosts: event.target.value })}
              placeholder="moliply.shop"
              className="admin-input w-full py-2.5"
            />
          </Field>
          <Field label="最长等待秒数">
            <input
              type="number"
              value={linkValues.upstream_max_wait_seconds || "180"}
              onChange={(event) => setLinkValues({ ...linkValues, upstream_max_wait_seconds: event.target.value })}
              className="admin-input w-full py-2.5"
            />
          </Field>
          <Field label="轮询间隔秒数">
            <input
              type="number"
              step="0.5"
              value={linkValues.upstream_poll_interval_seconds || "5"}
              onChange={(event) => setLinkValues({ ...linkValues, upstream_poll_interval_seconds: event.target.value })}
              className="admin-input w-full py-2.5"
            />
          </Field>
          <Field label="API Key" hint="与上游服务 .env 的 UPSTREAM_KEY 一致。">
            <input
              type="password"
              value={linkSecrets.upstream_api_key || ""}
              onChange={(event) => setLinkSecrets({ ...linkSecrets, upstream_api_key: event.target.value })}
              placeholder={link?.secret_configured.upstream_api_key ? "已配置，留空保持不变" : "尚未配置"}
              className="admin-input w-full py-2.5"
              autoComplete="off"
            />
          </Field>
          <Field label="API Secret" hint="与上游服务 .env 的 UPSTREAM_SECRET 一致。">
            <input
              type="password"
              value={linkSecrets.upstream_api_secret || ""}
              onChange={(event) => setLinkSecrets({ ...linkSecrets, upstream_api_secret: event.target.value })}
              placeholder={link?.secret_configured.upstream_api_secret ? "已配置，留空保持不变" : "尚未配置"}
              className="admin-input w-full py-2.5"
              autoComplete="off"
            />
          </Field>
        </div>

        <button
          type="button"
          onClick={() => void saveLink()}
          disabled={savingLink}
          className="mt-5 inline-flex items-center gap-2 rounded-lg border border-white/[0.1] px-4 py-2 text-[11px] text-slate-300 disabled:opacity-50"
        >
          {savingLink ? <Loader2 size={13} className="animate-spin" /> : <BadgeCheck size={13} />}
          保存通路参数
        </button>
      </section>

      {probe && (
        <section className="admin-card mt-4">
          <div className="flex items-center gap-2.5">
            <CircleDollarSign size={16} className="text-emerald-300" />
            <h2 className="text-base font-semibold text-slate-100">实时取价</h2>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <p className="text-[9px] text-slate-600">店铺</p>
              <p className="mt-1 text-[11px] text-slate-300">{probe.shop || "—"}</p>
            </div>
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <p className="text-[9px] text-slate-600">余额</p>
              <p className="mt-1 text-[11px] text-slate-300">{probe.balance ?? "—"}</p>
            </div>
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-left text-[9px] uppercase tracking-[0.16em] text-slate-600">
                  <th className="pb-2">本地 SKU</th>
                  <th className="pb-2">上游商品码</th>
                  <th className="pb-2 text-right">进货价</th>
                </tr>
              </thead>
              <tbody>
                {probe.quotes.map((quote) => (
                  <tr key={quote.sku} className="border-t border-white/[0.05]">
                    <td className="py-2 text-slate-300">{quote.sku}</td>
                    <td className="py-2 font-mono text-[10px] text-slate-500">{quote.code}</td>
                    <td className="py-2 text-right">
                      {quote.error ? (
                        <span className="text-rose-300">{quote.error}</span>
                      ) : (
                        <span className="text-slate-200">
                          {quote.price_cents != null ? `¥${(quote.price_cents / 100).toFixed(2)}` : "—"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                {probe.quotes.length === 0 && (
                  <tr>
                    <td colSpan={3} className="py-3 text-slate-600">
                      尚未配置套餐映射，先在上面填写后再测试。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {loading && (
        <div className="mt-4 flex items-center gap-2 text-[10px] text-slate-600">
          <Loader2 size={12} className="animate-spin" /> 正在读取配置...
        </div>
      )}
    </div>
  );
}
