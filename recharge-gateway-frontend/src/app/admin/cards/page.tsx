"use client";

import { CreditCard, Loader2, Plus, RefreshCw, ShieldAlert, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Modal } from "@/components/Modal";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatMoney } from "@/lib/format";
import type { AdminCard, AdminCardList, AdminCardPoolSummary } from "@/lib/types";

type CardForm = {
  provider: string;
  card_number: string;
  cvv: string;
  exp_month: string;
  exp_year: string;
  holder_name: string;
  line1: string;
  city: string;
  state: string;
  postal_code: string;
  ph_line1: string;
  ph_city: string;
  ph_state: string;
  ph_postal_code: string;
  use_ph_billing: boolean;
  three_ds_email_account: string;
  available_balance: string;
  max_bind_count: string;
};

const EMPTY_FORM: CardForm = {
  provider: "RDVCC",
  card_number: "",
  cvv: "",
  exp_month: "",
  exp_year: "",
  holder_name: "",
  line1: "",
  city: "",
  state: "",
  postal_code: "",
  ph_line1: "",
  ph_city: "",
  ph_state: "",
  ph_postal_code: "",
  use_ph_billing: false,
  three_ds_email_account: "",
  available_balance: "25",
  max_bind_count: "2",
};

const ROUTING_OPTIONS = [
  { key: "HYBRID_SELF_FIRST", label: "混合：自营优先，失败回上游", hint: "先尝试自营卡池（菲区₱999 → 美区$20），无卡或失败时自动回退上游 API。" },
  { key: "SELF_VCC_ONLY", label: "仅自营：不回退", hint: "只走自营卡池，失败后转人工处理，不调用上游 API。" },
  { key: "UPSTREAM_ONLY", label: "仅上游：暂停自营", hint: "全部订单走上游 API，自营卡池暂停调度。" },
] as const;

const BATCH_EXAMPLE = `[
  {
    "provider": "RDVCC",
    "card_number": "4242424242424242",
    "cvv": "123",
    "exp_month": "12",
    "exp_year": "2029",
    "holder_name": "DEMO USER",
    "billing_address": {"line1": "1 Main St", "city": "New York", "state": "NY", "postal_code": "10001", "country": "US"},
    "ph_billing_address": {"line1": "1 Ayala Ave", "city": "Makati", "state": "Metro Manila", "postal_code": "1200", "country": "PH"},
    "three_ds_email_account": "3ds@example.com",
    "available_balance": 25,
    "max_bind_count": 2
  }
]`;

export default function AdminCardsPage() {
  const [data, setData] = useState<AdminCardList | null>(null);
  const [pool, setPool] = useState<AdminCardPoolSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<CardForm>(EMPTY_FORM);
  const [importText, setImportText] = useState("");
  const [importResult, setImportResult] = useState<{ created: number; failed: string[] } | null>(null);
  const [actionId, setActionId] = useState<number | null>(null);
  const [policy, setPolicy] = useState("HYBRID_SELF_FIRST");
  const [policySaving, setPolicySaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, summary] = await Promise.all([
        api<AdminCardList>("/api/v1/admin/cards"),
        api<AdminCardPoolSummary>("/api/v1/admin/cards/summary"),
      ]);
      setData(list);
      setPool(summary);
      setPolicy(summary.routing_policy || "HYBRID_SELF_FIRST");
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function setField<K extends keyof CardForm>(key: K, value: CardForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function addCard() {
    setSaving(true);
    setError(null);
    try {
      await api("/api/v1/admin/cards", {
        method: "POST",
        body: {
          provider: form.provider,
          card_number: form.card_number.replace(/\s+/g, ""),
          cvv: form.cvv,
          exp_month: form.exp_month,
          exp_year: form.exp_year,
          holder_name: form.holder_name,
          billing_address: {
            line1: form.line1,
            city: form.city,
            state: form.state,
            postal_code: form.postal_code,
            country: "US",
          },
          ph_billing_address: form.use_ph_billing
            ? {
                line1: form.ph_line1,
                city: form.ph_city,
                state: form.ph_state,
                postal_code: form.ph_postal_code,
                country: "PH",
              }
            : null,
          three_ds_email_account: form.three_ds_email_account.trim() || null,
          available_balance: Number(form.available_balance),
          max_bind_count: Number(form.max_bind_count),
        },
      });
      setAddOpen(false);
      setForm(EMPTY_FORM);
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setSaving(false);
    }
  }

  async function importCards() {
    setSaving(true);
    setError(null);
    setImportResult(null);
    try {
      let parsed: unknown;
      try {
        parsed = JSON.parse(importText);
      } catch {
        throw new Error("JSON 格式有误，请检查括号与引号");
      }
      const cards = Array.isArray(parsed)
        ? parsed
        : (parsed as { cards?: unknown[] } | null)?.cards;
      if (!Array.isArray(cards) || !cards.length) {
        throw new Error("请提供非空的 cards 数组");
      }
      const result = await api<{ created: number; failed: string[] }>("/api/v1/admin/cards/batch-import", {
        method: "POST",
        body: { cards },
      });
      setImportResult(result);
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setSaving(false);
    }
  }

  async function updateStatus(card: AdminCard, action: "suspend" | "restore" | "reset") {
    const label = action === "suspend" ? "挂起" : action === "restore" ? "恢复为可用" : "重置失败计数并恢复";
    if (!window.confirm(`确认${label}该卡片？`)) return;
    setActionId(card.id);
    setError(null);
    try {
      await api(`/api/v1/admin/cards/${card.id}`, {
        method: "PATCH",
        body:
          action === "suspend"
            ? { status: "SUSPENDED" }
            : { status: "ACTIVE", reset_failed_count: true },
      });
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setActionId(null);
    }
  }

  async function markClosed(card: AdminCard) {
    if (!window.confirm("确认已在外币卡台销卡并标记？标记后该卡不会再被调度。")) return;
    setActionId(card.id);
    setError(null);
    try {
      await api(`/api/v1/admin/cards/${card.id}/mark-closed`, { method: "POST" });
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setActionId(null);
    }
  }

  async function changePolicy(next: string) {
    setPolicySaving(true);
    setError(null);
    setNotice(null);
    const previous = policy;
    setPolicy(next);
    try {
      await api("/api/v1/admin/routing-policy", {
        method: "PATCH",
        body: { routing_policy: next },
      });
      setNotice("路由策略已更新，新任务立即生效");
      await load();
    } catch (err) {
      setPolicy(previous);
      setError(handleAdminError(err));
    } finally {
      setPolicySaving(false);
    }
  }

  const pendingClose = pool?.pending_close || [];
  const policyHint = ROUTING_OPTIONS.find((option) => option.key === policy)?.hint || "";

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">SELF-OWNED VCC POOL</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">VCC 卡池</h1>
          <p className="mt-2 text-[11px] text-slate-500">管理自营虚拟卡：菲区 ₱999 优先，美区 $20 兜底，失败自动回退上游。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void load()} className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] text-slate-500 hover:text-slate-200" aria-label="刷新">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
          <button type="button" onClick={() => { setImportOpen(true); setImportResult(null); }} className="btn-secondary py-2.5">
            <Upload size={14} />
            批量导入
          </button>
          <button type="button" onClick={() => { setForm(EMPTY_FORM); setAddOpen(true); }} className="btn-primary py-2.5">
            <Plus size={14} />
            录入卡片
          </button>
        </div>
      </div>

      <div className="mt-6 rounded-2xl border border-white/[0.07] bg-[#0a111e]/90 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-[10px] uppercase tracking-[0.14em] text-slate-500">ROUTING POLICY</p>
            <p className="mt-1 text-[11px] text-slate-400">{policyHint}</p>
          </div>
          <select
            value={policy}
            disabled={policySaving}
            onChange={(event) => void changePolicy(event.target.value)}
            className="admin-input w-full py-2.5 sm:w-[280px]"
            aria-label="路由策略"
          >
            {ROUTING_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: "卡片总数", value: data?.summary.total || 0, tone: "text-slate-100" },
          { label: "当前可调度", value: data?.summary.available || 0, tone: "text-brand-300" },
          { label: "今日自营", value: pool?.today_self || 0, tone: "text-emerald-300" },
          { label: "今日回退上游", value: pool?.today_fallback || 0, tone: "text-amber-300" },
        ].map((card) => (
          <div key={card.label} className="admin-card">
            <p className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{card.label}</p>
            <p className={`mt-3 font-display text-[28px] font-semibold leading-none ${card.tone}`}>{card.value}</p>
          </div>
        ))}
      </div>

      <div className="mt-4 grid gap-3 text-[10px] text-slate-500 sm:grid-cols-3">
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
          卡池余额合计 <span className="ml-2 font-mono text-[13px] text-slate-200">${(pool?.available_balance_usd || 0).toFixed(2)}</span>
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
          挂起 / 待销卡 / 已销卡
          <span className="ml-2 font-mono text-[13px] text-slate-200">
            {data?.summary.suspended || 0} / {data?.summary.exhausted || 0} / {data?.summary.closed || 0}
          </span>
        </div>
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3">
          今日上游 / 自营 / 兜底
          <span className="ml-2 font-mono text-[13px] text-slate-200">
            {pool?.today_upstream || 0} / {pool?.today_self || 0} / {pool?.today_fallback || 0}
          </span>
        </div>
      </div>

      {pendingClose.length > 0 && (
        <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-400/[0.05] px-4 py-3 text-[10px] leading-5 text-amber-200/80">
          有 {pendingClose.length} 张卡已达绑定上限，请到卡台销卡回血：{" "}
          {pendingClose.map((item) => `${item.card_masked}（剩余 $${Number(item.balance).toFixed(2)}，绑定 ${item.binds}）`).join("；")}
        </div>
      )}

      {notice && (
        <div className="mt-4 rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] px-4 py-3 text-[11px] text-emerald-200">
          {notice}
        </div>
      )}
      {error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0a111e]/90">
        <div className="overflow-x-auto">
          <table className="admin-table min-w-[1040px]">
            <thead>
              <tr>
                <th>卡片</th>
                <th>区域能力</th>
                <th>3DS 邮箱</th>
                <th>有效期</th>
                <th>余额</th>
                <th>绑定次数</th>
                <th>失败</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {loading && !data ? (
                <tr>
                  <td colSpan={9} className="py-16 text-center text-slate-600">
                    <Loader2 size={18} className="mx-auto animate-spin" />
                  </td>
                </tr>
              ) : data?.items.length ? (
                data.items.map((card) => (
                  <tr key={card.id}>
                    <td>
                      <div className="font-mono text-[10px] text-slate-300">{card.card_masked}</div>
                      <div className="mt-1 text-[9px] text-slate-600">{card.card_bin}****** · {card.holder_name}</div>
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        <span className="rounded border border-sky-400/20 bg-sky-400/10 px-1.5 py-0.5 text-[9px] text-sky-300">US</span>
                        {card.has_ph_billing ? (
                          <span className="rounded border border-emerald-400/20 bg-emerald-400/10 px-1.5 py-0.5 text-[9px] text-emerald-300">PH</span>
                        ) : (
                          <span className="rounded border border-white/[0.06] px-1.5 py-0.5 text-[9px] text-slate-600" title="未录入菲律宾账单地址，不参与 ₱999 调度">PH 未配置</span>
                        )}
                      </div>
                    </td>
                    <td className="text-[10px] text-slate-500">{card.three_ds_email_masked || "—"}</td>
                    <td className="text-[10px] text-slate-500">{card.exp_month}/{card.exp_year}</td>
                    <td className="text-[11px] text-slate-300">{formatMoney(card.available_balance)}</td>
                    <td className="text-[10px] text-slate-500">{card.current_bind_count} / {card.max_bind_count}</td>
                    <td className={`text-[10px] ${card.failed_count ? "text-rose-300" : "text-slate-600"}`}>{card.failed_count}</td>
                    <td><StatusBadge status={card.status} label={card.status_label} /></td>
                    <td>
                      <div className="flex flex-col gap-1.5">
                        {card.status === "EXHAUSTED" && (
                          <button
                            type="button"
                            disabled={actionId === card.id}
                            onClick={() => void markClosed(card)}
                            className="text-left text-[10px] text-amber-300 hover:text-amber-200 disabled:opacity-40"
                          >
                            {actionId === card.id ? "处理中" : "标记已销卡"}
                          </button>
                        )}
                        {card.status === "SUSPENDED" || card.failed_count > 0 ? (
                          <button
                            type="button"
                            disabled={actionId === card.id}
                            onClick={() => void updateStatus(card, "reset")}
                            className="text-left text-[10px] text-emerald-300 hover:text-emerald-200 disabled:opacity-40"
                          >
                            {actionId === card.id ? "处理中" : "恢复并重置"}
                          </button>
                        ) : card.status === "ACTIVE" ? (
                          <button
                            type="button"
                            disabled={actionId === card.id}
                            onClick={() => void updateStatus(card, "suspend")}
                            className="text-left text-[10px] text-rose-300/80 hover:text-rose-200 disabled:opacity-40"
                          >
                            手动挂起
                          </button>
                        ) : (
                          <span className="text-[10px] text-slate-700">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={9} className="py-16 text-center text-[11px] text-slate-600">卡池为空，请先录入或批量导入虚拟卡</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-400/15 bg-amber-400/[0.045] px-4 py-3 text-[10px] leading-5 text-amber-200/65">
        <ShieldAlert size={14} className="mt-0.5 shrink-0" />
        单卡最多绑定 2 个账号；任一卡出现拒付即自动挂起并停止调度，避免产生失败交易费与账号风控。
        卡号与 CVV 使用 AES-256-GCM 加密存储，页面仅展示掩码。菲律宾地址仅用于 ₱999 结账，美区 $20 使用美国地址。
      </div>

      <Modal open={addOpen} onClose={() => setAddOpen(false)} title="录入自营虚拟卡" maxWidth="max-w-2xl">
        <div className="space-y-4">
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-[10px] leading-5 text-slate-500">
            请逐字填写卡片详情页提供的美国账单地址；如需参与菲律宾 ₱999 结账，再补充卡台提供的菲律宾地址。
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="admin-label">卡台</label>
              <input value={form.provider} onChange={(event) => setField("provider", event.target.value)} className="admin-input w-full py-2.5" />
            </div>
            <div>
              <label className="admin-label">卡号</label>
              <input
                value={form.card_number}
                onChange={(event) => setField("card_number", event.target.value)}
                className="admin-input w-full py-2.5 font-mono"
                placeholder="4242 4242 4242 4242"
                autoComplete="off"
              />
            </div>
            <div>
              <label className="admin-label">有效期月</label>
              <input value={form.exp_month} onChange={(event) => setField("exp_month", event.target.value)} className="admin-input w-full py-2.5" placeholder="12" />
            </div>
            <div>
              <label className="admin-label">有效期年</label>
              <input value={form.exp_year} onChange={(event) => setField("exp_year", event.target.value)} className="admin-input w-full py-2.5" placeholder="2029" />
            </div>
            <div>
              <label className="admin-label">CVV</label>
              <input value={form.cvv} onChange={(event) => setField("cvv", event.target.value)} className="admin-input w-full py-2.5" placeholder="123" />
            </div>
            <div>
              <label className="admin-label">英文持卡人姓名</label>
              <input value={form.holder_name} onChange={(event) => setField("holder_name", event.target.value)} className="admin-input w-full py-2.5" placeholder="DEMO USER" />
            </div>
            <div className="sm:col-span-2">
              <p className="admin-label">美国账单地址（$20 兜底）</p>
              <input value={form.line1} onChange={(event) => setField("line1", event.target.value)} className="admin-input w-full py-2.5" placeholder="1 Main St" />
            </div>
            <div>
              <label className="admin-label">城市</label>
              <input value={form.city} onChange={(event) => setField("city", event.target.value)} className="admin-input w-full py-2.5" placeholder="New York" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="admin-label">州</label>
                <input value={form.state} onChange={(event) => setField("state", event.target.value)} className="admin-input w-full py-2.5" placeholder="NY" />
              </div>
              <div>
                <label className="admin-label">邮编</label>
                <input value={form.postal_code} onChange={(event) => setField("postal_code", event.target.value)} className="admin-input w-full py-2.5" placeholder="10001" />
              </div>
            </div>
            <div className="sm:col-span-2">
              <label className="flex items-center gap-2 text-[11px] text-slate-400">
                <input
                  type="checkbox"
                  checked={form.use_ph_billing}
                  onChange={(event) => setField("use_ph_billing", event.target.checked)}
                />
                录入菲律宾账单地址（参与 ₱999 优先调度）
              </label>
            </div>
            {form.use_ph_billing && (
              <>
                <div className="sm:col-span-2">
                  <label className="admin-label">菲律宾地址 Line 1</label>
                  <input value={form.ph_line1} onChange={(event) => setField("ph_line1", event.target.value)} className="admin-input w-full py-2.5" placeholder="1 Ayala Ave" />
                </div>
                <div>
                  <label className="admin-label">城市</label>
                  <input value={form.ph_city} onChange={(event) => setField("ph_city", event.target.value)} className="admin-input w-full py-2.5" placeholder="Makati" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="admin-label">省份</label>
                    <input value={form.ph_state} onChange={(event) => setField("ph_state", event.target.value)} className="admin-input w-full py-2.5" placeholder="Metro Manila" />
                  </div>
                  <div>
                    <label className="admin-label">邮编</label>
                    <input value={form.ph_postal_code} onChange={(event) => setField("ph_postal_code", event.target.value)} className="admin-input w-full py-2.5" placeholder="1200" />
                  </div>
                </div>
              </>
            )}
            <div>
              <label className="admin-label">3DS 邮箱账号（可选）</label>
              <input value={form.three_ds_email_account} onChange={(event) => setField("three_ds_email_account", event.target.value)} className="admin-input w-full py-2.5" placeholder="3ds@example.com" autoComplete="off" />
            </div>
            <div>
              <label className="admin-label">可用余额（USD）</label>
              <input value={form.available_balance} onChange={(event) => setField("available_balance", event.target.value)} className="admin-input w-full py-2.5" placeholder="25" />
            </div>
            <div>
              <label className="admin-label">最大绑定账号数</label>
              <select value={form.max_bind_count} onChange={(event) => setField("max_bind_count", event.target.value)} className="admin-input w-full py-2.5">
                <option value="1">1 个（保守）</option>
                <option value="2">2 个（上限）</option>
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setAddOpen(false)} className="btn-secondary py-2.5">取消</button>
            <button type="button" disabled={saving} onClick={() => void addCard()} className="btn-primary py-2.5">
              {saving ? <Loader2 size={13} className="animate-spin" /> : <CreditCard size={13} />}
              保存卡片
            </button>
          </div>
        </div>
      </Modal>

      <Modal open={importOpen} onClose={() => setImportOpen(false)} title="批量导入自营虚拟卡" maxWidth="max-w-3xl">
        {importResult ? (
          <div>
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] p-4 text-[11px] text-emerald-200">
              导入完成：新增 {importResult.created} 张，失败 {importResult.failed.length} 张。
            </div>
            {importResult.failed.length > 0 && (
              <div className="mt-3 max-h-40 overflow-y-auto rounded-xl border border-rose-400/20 bg-rose-400/[0.05] p-3 font-mono text-[10px] leading-5 text-rose-200">
                {importResult.failed.map((item, index) => <div key={`${index}-${item}`}>{item}</div>)}
              </div>
            )}
            <div className="mt-4 flex justify-end">
              <button type="button" onClick={() => setImportOpen(false)} className="btn-secondary py-2.5">完成</button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-[10px] leading-5 text-slate-500">
              粘贴 JSON 数组（最多 200 张）。字段与单卡录入一致：<span className="font-mono text-slate-400">billing_address</span> 为美国地址，
              <span className="font-mono text-slate-400">ph_billing_address</span> 可省略。
            </div>
            <textarea
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              className="admin-input min-h-[260px] w-full resize-y py-3 font-mono text-[10px] leading-5"
              placeholder={BATCH_EXAMPLE}
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setImportText(BATCH_EXAMPLE)}
                className="btn-secondary py-2.5"
              >
                填入示例
              </button>
              <button type="button" onClick={() => setImportOpen(false)} className="btn-secondary py-2.5">取消</button>
              <button
                type="button"
                disabled={saving || !importText.trim()}
                onClick={() => void importCards()}
                className="btn-primary py-2.5"
              >
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                开始导入
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
