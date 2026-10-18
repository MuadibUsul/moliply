"use client";

import { CreditCard, Loader2, Plus, RefreshCw, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Modal } from "@/components/Modal";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatMoney } from "@/lib/format";
import type { AdminCard, AdminCardList } from "@/lib/types";

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
  available_balance: "25",
  max_bind_count: "1",
};

export default function AdminCardsPage() {
  const [data, setData] = useState<AdminCardList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<CardForm>(EMPTY_FORM);
  const [actionId, setActionId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api<AdminCardList>("/api/v1/admin/cards"));
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

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">SELF-OWNED VCC POOL</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">VCC 卡池</h1>
          <p className="mt-2 text-[11px] text-slate-500">管理 RDVCC 自营虚拟卡，执行单卡绑定上限与失败熔断。</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void load()}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] text-slate-500 hover:text-slate-200"
            aria-label="刷新"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
          <button type="button" onClick={() => { setForm(EMPTY_FORM); setAddOpen(true); }} className="btn-primary py-2.5">
            <Plus size={14} />
            录入卡片
          </button>
        </div>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: "卡片总数", value: data?.summary.total || 0, tone: "text-slate-100" },
          { label: "可用卡片", value: data?.summary.active || 0, tone: "text-emerald-300" },
          { label: "当前可调度", value: data?.summary.available || 0, tone: "text-brand-300" },
          { label: "风控挂起", value: data?.summary.suspended || 0, tone: "text-rose-300" },
        ].map((card) => (
          <div key={card.label} className="admin-card">
            <p className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{card.label}</p>
            <p className={`mt-3 font-display text-[28px] font-semibold leading-none ${card.tone}`}>{card.value}</p>
          </div>
        ))}
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0a111e]/90">
        <div className="overflow-x-auto">
          <table className="admin-table min-w-[960px]">
            <thead>
              <tr>
                <th>卡片</th>
                <th>BIN</th>
                <th>有效期</th>
                <th>持卡人</th>
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
                    <td className="font-mono text-[10px] text-slate-300">{card.card_masked}</td>
                    <td className="font-mono text-[10px] text-slate-500">{card.card_bin}******</td>
                    <td className="text-[10px] text-slate-500">{card.exp_month}/{card.exp_year}</td>
                    <td className="text-[10px] text-slate-400">{card.holder_name}</td>
                    <td className="text-[11px] text-slate-300">{formatMoney(card.available_balance)}</td>
                    <td className="text-[10px] text-slate-500">{card.current_bind_count} / {card.max_bind_count}</td>
                    <td className={`text-[10px] ${card.failed_count ? "text-rose-300" : "text-slate-600"}`}>{card.failed_count}</td>
                    <td><StatusBadge status={card.status} label={card.status_label} /></td>
                    <td>
                      <div className="flex items-center gap-2">
                        {card.status === "SUSPENDED" || card.failed_count > 0 ? (
                          <button
                            type="button"
                            disabled={actionId === card.id}
                            onClick={() => void updateStatus(card, "reset")}
                            className="text-[10px] text-emerald-300 hover:text-emerald-200 disabled:opacity-40"
                          >
                            {actionId === card.id ? "处理中" : "恢复并重置"}
                          </button>
                        ) : card.status === "ACTIVE" ? (
                          <button
                            type="button"
                            disabled={actionId === card.id}
                            onClick={() => void updateStatus(card, "suspend")}
                            className="text-[10px] text-rose-300/80 hover:text-rose-200 disabled:opacity-40"
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
                  <td colSpan={9} className="py-16 text-center text-[11px] text-slate-600">卡池为空，请先录入 RDVCC 卡片</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-400/15 bg-amber-400/[0.045] px-4 py-3 text-[10px] leading-5 text-amber-200/65">
        <ShieldAlert size={14} className="mt-0.5 shrink-0" />
        单卡最多绑定 2 个账号（默认 1 个）；任一卡出现拒付即自动挂起并停止调度，避免产生失败交易费与账号风控。
        卡号与 CVV 使用 AES-256-GCM 加密存储，页面仅展示掩码。自营通道默认处于演练模式，确认代理与邮箱配置后再启用实盘。
      </div>

      <Modal open={addOpen} onClose={() => setAddOpen(false)} title="录入 RDVCC 虚拟卡" maxWidth="max-w-2xl">
        <div className="space-y-4">
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-[10px] leading-5 text-slate-500">
            请逐字填写卡片详情页提供的美国账单地址，确保 AVS 校验与 BIN 归属一致。录入前建议卡内余额 ≥ $25。
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
              <label className="admin-label">账单地址 Line 1</label>
              <input value={form.line1} onChange={(event) => setField("line1", event.target.value)} className="admin-input w-full py-2.5" placeholder="1 Main St" />
            </div>
            <div>
              <label className="admin-label">城市</label>
              <input value={form.city} onChange={(event) => setField("city", event.target.value)} className="admin-input w-full py-2.5" placeholder="New York" />
            </div>
            <div>
              <label className="admin-label">州</label>
              <input value={form.state} onChange={(event) => setField("state", event.target.value)} className="admin-input w-full py-2.5" placeholder="NY" />
            </div>
            <div>
              <label className="admin-label">邮编</label>
              <input value={form.postal_code} onChange={(event) => setField("postal_code", event.target.value)} className="admin-input w-full py-2.5" placeholder="10001" />
            </div>
            <div>
              <label className="admin-label">可用余额（USD）</label>
              <input value={form.available_balance} onChange={(event) => setField("available_balance", event.target.value)} className="admin-input w-full py-2.5" placeholder="25" />
            </div>
            <div>
              <label className="admin-label">最大绑定账号数</label>
              <select value={form.max_bind_count} onChange={(event) => setField("max_bind_count", event.target.value)} className="admin-input w-full py-2.5">
                <option value="1">1 个（推荐）</option>
                <option value="2">2 个（上限）</option>
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setAddOpen(false)} className="btn-secondary py-2.5">取消</button>
            <button
              type="button"
              disabled={saving}
              onClick={() => void addCard()}
              className="btn-primary py-2.5"
            >
              {saving ? <Loader2 size={13} className="animate-spin" /> : <CreditCard size={13} />}
              保存卡片
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
