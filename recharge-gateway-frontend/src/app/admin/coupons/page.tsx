"use client";

import { Plus, RefreshCw, TicketPercent, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatDateTime, formatMoney } from "@/lib/format";

type CouponItem = {
  id: number;
  code: string;
  amount: number;
  min_amount: number;
  active: boolean;
  max_uses: number | null;
  used_count: number;
  expires_at: string | null;
  created_at: string;
};

export default function AdminCouponsPage() {
  const [items, setItems] = useState<CouponItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [amount, setAmount] = useState("10");
  const [minAmount, setMinAmount] = useState("100");
  const [maxUses, setMaxUses] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api<{ items: CouponItem[] }>("/api/v1/admin/coupons");
      setItems(data.items);
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function createCoupon(event: React.FormEvent) {
    event.preventDefault();
    if (!code.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await api("/api/v1/admin/coupons", {
        method: "POST",
        body: {
          code: code.trim().toUpperCase(),
          amount: Number(amount),
          min_amount: Number(minAmount || 0),
          max_uses: maxUses.trim() ? Number(maxUses) : null,
          active: true,
        },
      });
      setCode("");
      setMaxUses("");
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(item: CouponItem) {
    try {
      await api(`/api/v1/admin/coupons/${item.id}`, {
        method: "PATCH",
        body: { active: !item.active },
      });
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    }
  }

  async function removeCoupon(item: CouponItem) {
    if (!window.confirm(`确认删除优惠码 ${item.code}？`)) return;
    try {
      await api(`/api/v1/admin/coupons/${item.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    }
  }

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">PROMOTIONS</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">优惠码管理</h1>
          <p className="mt-2 text-[11px] text-slate-500">创建满减优惠码、限制使用次数并实时启停商业促销活动。</p>
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
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-xs text-rose-200">
          {error}
        </div>
      )}

      <form onSubmit={createCoupon} className="admin-card mt-6 grid gap-3 p-5 sm:grid-cols-5">
        <label className="block">
          <span className="admin-label">优惠码</span>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="例如 VIP20"
            className="admin-input w-full py-2"
            required
          />
        </label>
        <label className="block">
          <span className="admin-label">立减金额（¥）</span>
          <input
            type="number"
            step="0.01"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="admin-input w-full py-2"
            required
          />
        </label>
        <label className="block">
          <span className="admin-label">满减门槛（¥）</span>
          <input
            type="number"
            step="0.01"
            value={minAmount}
            onChange={(e) => setMinAmount(e.target.value)}
            className="admin-input w-full py-2"
          />
        </label>
        <label className="block">
          <span className="admin-label">次数上限（留空不限）</span>
          <input
            type="number"
            min="1"
            value={maxUses}
            onChange={(e) => setMaxUses(e.target.value)}
            placeholder="不限"
            className="admin-input w-full py-2"
          />
        </label>
        <div className="flex items-end">
          <button type="submit" disabled={saving} className="btn-primary w-full py-2.5 text-xs">
            <Plus size={14} />
            {saving ? "创建中..." : "新建优惠码"}
          </button>
        </div>
      </form>

      <div className="admin-card mt-5 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="admin-table">
            <thead>
              <tr>
                <th>优惠码</th>
                <th>立减 / 门槛</th>
                <th>已用 / 上限</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td className="font-mono text-xs text-slate-200">
                    <span className="inline-flex items-center gap-2">
                      <TicketPercent size={13} className="text-brand-400" />
                      {item.code}
                    </span>
                  </td>
                  <td className="text-xs text-slate-300">
                    减 {formatMoney(item.amount)} · 满 {formatMoney(item.min_amount)}
                  </td>
                  <td className="text-xs text-slate-400">
                    {item.used_count} / {item.max_uses ?? "∞"}
                  </td>
                  <td>
                    <span className={`text-[10px] ${item.active ? "text-emerald-300" : "text-slate-500"}`}>
                      {item.active ? "启用中" : "已停用"}
                    </span>
                  </td>
                  <td className="text-[11px] text-slate-500">{formatDateTime(item.created_at)}</td>
                  <td>
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        onClick={() => void toggleActive(item)}
                        className="text-[11px] text-sky-300 hover:text-sky-200"
                      >
                        {item.active ? "停用" : "启用"}
                      </button>
                      <button
                        type="button"
                        onClick={() => void removeCoupon(item)}
                        className="text-slate-500 hover:text-rose-300"
                        aria-label="删除优惠码"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {!loading && !items.length && (
                <tr>
                  <td colSpan={6} className="py-10 text-center text-xs text-slate-600">
                    暂无优惠码，请在上方创建。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
