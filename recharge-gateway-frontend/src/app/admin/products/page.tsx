"use client";

import { Boxes, Loader2, RefreshCw, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { BrandTile } from "@/components/BrandTile";
import { Modal } from "@/components/Modal";
import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatMoney, formatNumber } from "@/lib/format";
import type { AdminProductList } from "@/lib/types";

export default function AdminProductsPage() {
  const [data, setData] = useState<AdminProductList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importProduct, setImportProduct] = useState("");
  const [importText, setImportText] = useState("");
  const [saving, setSaving] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api<AdminProductList>("/api/v1/admin/products"));
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const accountProducts = (data?.items || []).filter((product) => product.delivery_type === "ACCOUNT_CREDENTIALS");

  useEffect(() => {
    if (!importProduct && accountProducts[0]) setImportProduct(String(accountProducts[0].id));
  }, [accountProducts, importProduct]);

  async function toggleProduct(id: number, active: boolean) {
    if (!window.confirm(active ? "确认上架该商品？" : "确认下架该商品？")) return;
    setActionId(id);
    setError(null);
    try {
      await api(`/api/v1/admin/products/${id}`, {
        method: "PATCH",
        body: { active },
      });
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setActionId(null);
    }
  }

  async function importAccounts() {
    setSaving(true);
    setError(null);
    try {
      await api("/api/v1/admin/accounts/import", {
        method: "POST",
        body: { product_id: Number(importProduct), text: importText },
      });
      setImportOpen(false);
      setImportText("");
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">PRODUCT CATALOGUE</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">商品管理</h1>
          <p className="mt-2 text-[11px] text-slate-500">查看商品库存、交付方式与上架状态。</p>
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
          <button type="button" onClick={() => setImportOpen(true)} className="btn-primary py-2.5">
            <Upload size={14} />
            导入成品号
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      <div className="mt-6 overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0a111e]/90">
        <div className="overflow-x-auto">
          <table className="admin-table min-w-[960px]">
            <thead>
              <tr>
                <th>商品</th>
                <th>分类</th>
                <th>交付方式</th>
                <th>售价</th>
                <th>库存</th>
                <th>已售</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {loading && !data ? (
                <tr>
                  <td colSpan={8} className="py-16 text-center text-slate-600">
                    <Loader2 size={18} className="mx-auto animate-spin" />
                  </td>
                </tr>
              ) : data?.items.length ? (
                data.items.map((product) => (
                  <tr key={product.id}>
                    <td>
                      <div className="flex items-center gap-3">
                        <BrandTile logoKey={product.logo_key} size={34} />
                        <div>
                          <p className="text-[11px] text-slate-200">{product.name}</p>
                          <p className="mt-0.5 max-w-[180px] truncate text-[9px] text-slate-600">{product.subtitle}</p>
                        </div>
                      </div>
                    </td>
                    <td className="text-[10px] text-slate-500">{product.category_label}</td>
                    <td className="text-[10px] text-slate-500">{product.delivery_label}</td>
                    <td className="text-[11px] text-slate-300">{formatMoney(product.price)}</td>
                    <td>
                      <span className={`text-[11px] ${(product.stock || 0) > 0 ? "text-slate-300" : "text-rose-300"}`}>
                        {product.delivery_type === "ACCOUNT_CREDENTIALS" ? product.account_stock : product.stock}
                      </span>
                    </td>
                    <td className="text-[10px] text-slate-500">{formatNumber(product.sold_count)}</td>
                    <td>
                      <StatusBadge status={product.active ? "ACTIVE" : "DISABLED"} label={product.active ? "已上架" : "已下架"} />
                    </td>
                    <td>
                      <button
                        type="button"
                        disabled={actionId === product.id}
                        onClick={() => void toggleProduct(product.id, !product.active)}
                        className="text-[10px] text-slate-500 hover:text-brand-300 disabled:opacity-40"
                      >
                        {product.active ? "下架" : "上架"}
                      </button>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={8} className="py-16 text-center text-[11px] text-slate-600">暂无商品</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-white/[0.055] px-5 py-3 text-[10px] text-slate-600">
          <span>共 {data?.items.length || 0} 个商品</span>
          <span className="inline-flex items-center gap-1.5"><Boxes size={11} /> 库存随卡密与账密库存实时计算</span>
        </div>
      </div>

      <div className="mt-4 rounded-xl border border-white/[0.055] bg-white/[0.015] px-4 py-3 text-[10px] leading-5 text-slate-600">
        激活码类商品的库存来自「卡密管理」中未使用的激活码；成品号类商品的库存来自已导入的账号凭证。
        当库存为 0 时商城会拦截下单，避免支付后无法交付。
      </div>

      <Modal open={importOpen} onClose={() => setImportOpen(false)} title="导入成品号账密" maxWidth="max-w-2xl">
        <div className="space-y-4">
          <div>
            <label className="admin-label">目标商品</label>
            <select value={importProduct} onChange={(event) => setImportProduct(event.target.value)} className="admin-input w-full py-2.5">
              {accountProducts.map((product) => (
                <option key={product.id} value={product.id}>{product.name} · {product.subtitle}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="admin-label">账密内容（每行一条）</label>
            <textarea
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              className="admin-input min-h-[180px] w-full resize-y py-3 font-mono text-[11px] leading-6"
              placeholder={"邮箱----邮箱密码----辅助邮箱----质保到期日\nuser@example.com----Passw0rd!----backup@example.com----2026-12-01"}
            />
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 text-[10px] leading-5 text-slate-600">
            导入内容将使用 AES-256-GCM 加密存储，仅在订单交付时解密展示。请确保账号来源合规、未在任何公共渠道泄露。
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setImportOpen(false)} className="btn-secondary py-2.5">取消</button>
            <button
              type="button"
              disabled={saving || !importText.trim() || !importProduct}
              onClick={() => void importAccounts()}
              className="btn-primary py-2.5"
            >
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
              开始导入
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
