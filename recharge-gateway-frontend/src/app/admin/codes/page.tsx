"use client";

import {
  Download,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { CopyButton } from "@/components/CopyButton";
import { Modal } from "@/components/Modal";
import { api, queryString } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatDateTime } from "@/lib/format";
import type { AdminCodeList } from "@/lib/types";

const STATUS_OPTIONS = [
  { key: "ALL", label: "全部状态" },
  { key: "UNUSED", label: "未使用" },
  { key: "LOCKED", label: "执行中" },
  { key: "USED", label: "已核销" },
  { key: "DISABLED", label: "已作废" },
];

const CHANNELS = [
  { key: "UPSTREAM", label: "上游 API" },
  { key: "SELF_VCC", label: "自营 VCC" },
];

type GenerateResult = { created: number; codes: string[]; sku_type: string };
type ImportResult = { created: number; skipped: number; codes: string[] };

export default function AdminCodesPage() {
  const [data, setData] = useState<AdminCodeList | null>(null);
  const [status, setStatus] = useState("ALL");
  const [sku, setSku] = useState("ALL");
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [generateOpen, setGenerateOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [generateResult, setGenerateResult] = useState<GenerateResult | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  const [genCount, setGenCount] = useState(10);
  const [genSku, setGenSku] = useState("CHATGPT_PLUS_1M");
  const [genChannel, setGenChannel] = useState("UPSTREAM");
  const [genNote, setGenNote] = useState("");

  const [importText, setImportText] = useState("");
  const [importSku, setImportSku] = useState("CHATGPT_PLUS_1M");
  const [importChannel, setImportChannel] = useState("UPSTREAM");
  const [importNote, setImportNote] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api<AdminCodeList>(
        `/api/v1/admin/codes${queryString({
          status,
          sku_type: sku,
          q: submittedQuery || undefined,
          page,
          page_size: 20,
        })}`,
      );
      setData(result);
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setLoading(false);
    }
  }, [status, sku, submittedQuery, page]);

  useEffect(() => {
    void load();
  }, [load]);

  async function generate() {
    setActionLoading(true);
    setError(null);
    try {
      const result = await api<GenerateResult>("/api/v1/admin/codes/generate", {
        method: "POST",
        body: { count: genCount, sku_type: genSku, channel_mode: genChannel, note: genNote },
      });
      setGenerateResult(result);
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function importCodes() {
    setActionLoading(true);
    setError(null);
    try {
      const result = await api<ImportResult>("/api/v1/admin/codes/import", {
        method: "POST",
        body: {
          text: importText,
          sku_type: importSku,
          channel_mode: importChannel,
          note: importNote,
        },
      });
      setImportResult(result);
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setActionLoading(false);
    }
  }

  async function toggleStatus(id: number, current: string) {
    const target = current === "DISABLED" ? "UNUSED" : "DISABLED";
    if (!window.confirm(target === "DISABLED" ? "确认作废该激活码？" : "确认恢复该激活码？")) return;
    try {
      await api(`/api/v1/admin/codes/${id}/status`, {
        method: "PATCH",
        body: { status: target },
      });
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    }
  }

  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / 20));

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">ACTIVATION CODES</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">卡密管理</h1>
          <p className="mt-2 text-[11px] text-slate-500">管理 MOLI 激活码、核销状态与商品库存。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => { setImportOpen(true); setImportResult(null); }} className="btn-secondary py-2.5">
            <Upload size={14} />
            批量导入
          </button>
          <button type="button" onClick={() => { setGenerateOpen(true); setGenerateResult(null); }} className="btn-primary py-2.5">
            <Plus size={14} />
            批量生成激活码
          </button>
        </div>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: "卡密总数", value: data?.stats.total || 0, hint: "全部 SKU", accent: "text-slate-100" },
          { label: "未使用卡密", value: data?.stats.unused || 0, hint: "当前可用", accent: "text-emerald-300" },
          { label: "执行中", value: data?.stats.locked || 0, hint: "任务已锁定", accent: "text-amber-300" },
          { label: "已核销", value: data?.stats.used || 0, hint: "历史累计", accent: "text-brand-300" },
        ].map((card) => (
          <div key={card.label} className="admin-card">
            <p className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{card.label}</p>
            <p className={`mt-3 font-display text-[28px] font-semibold leading-none ${card.accent}`}>{card.value}</p>
            <p className="mt-3 text-[10px] text-slate-600">{card.hint}</p>
          </div>
        ))}
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] px-4 py-3 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-2xl border border-white/[0.07] bg-[#0a111e]/90">
        <div className="flex flex-col gap-3 border-b border-white/[0.055] px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={sku}
              onChange={(event) => {
                setSku(event.target.value);
                setPage(1);
              }}
              className="admin-input h-9"
              aria-label="SKU 筛选"
            >
              <option value="ALL">全部商品</option>
              {data?.sku_options.map((option) => (
                <option key={option.key} value={option.key}>{option.label}</option>
              ))}
            </select>
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
              className="admin-input h-9"
              aria-label="状态筛选"
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.key} value={option.key}>{option.label}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setPage(1);
                setSubmittedQuery(query.trim());
              }}
              className="relative"
            >
              <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索卡密"
                className="admin-input h-9 w-[170px] pl-8"
                aria-label="搜索卡密"
              />
            </form>
            <button
              type="button"
              onClick={() => void load()}
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] text-slate-500 hover:text-slate-200"
              aria-label="刷新"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="admin-table min-w-[920px]">
            <thead>
              <tr>
                <th>激活码</th>
                <th>关联商品</th>
                <th>渠道</th>
                <th>状态</th>
                <th>绑定邮箱</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {loading && !data ? (
                <tr>
                  <td colSpan={7} className="py-16 text-center text-slate-600">
                    <Loader2 size={18} className="mx-auto animate-spin" />
                  </td>
                </tr>
              ) : data?.items.length ? (
                data.items.map((code) => (
                  <tr key={code.id}>
                    <td className="font-mono text-[10px] tracking-wider text-slate-300">{code.code}</td>
                    <td className="text-[11px] text-slate-400">{code.product_name || code.sku_label}</td>
                    <td className="text-[10px] text-slate-500">{code.channel_mode === "SELF_VCC" ? "自营 VCC" : "上游 API"}</td>
                    <td>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[9px] ${
                          code.status === "USED"
                            ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
                            : code.status === "UNUSED"
                              ? "border-brand-400/20 bg-brand-500/10 text-brand-300"
                              : code.status === "LOCKED"
                                ? "border-amber-400/20 bg-amber-400/10 text-amber-300"
                                : "border-rose-400/20 bg-rose-400/10 text-rose-300"
                        }`}
                      >
                        {code.status_label}
                      </span>
                    </td>
                    <td className="text-[10px] text-slate-500">{code.bound_email_masked || "—"}</td>
                    <td className="text-[10px] text-slate-600">{formatDateTime(code.used_at || code.created_at)}</td>
                    <td>
                      {(code.status === "UNUSED" || code.status === "DISABLED") ? (
                        <button
                          type="button"
                          onClick={() => void toggleStatus(code.id, code.status)}
                          className="text-[10px] text-slate-500 hover:text-brand-300"
                        >
                          {code.status === "DISABLED" ? "恢复" : "作废"}
                        </button>
                      ) : (
                        <span className="text-[10px] text-slate-700">—</span>
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7} className="py-16 text-center text-[11px] text-slate-600">暂无符合条件的激活码</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between border-t border-white/[0.055] px-5 py-3 text-[10px] text-slate-600">
          <span>显示 {data?.items.length || 0} / {data?.total || 0} 条</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              className="rounded border border-white/[0.08] px-2.5 py-1.5 disabled:opacity-30"
            >
              上一页
            </button>
            <span>{page} / {totalPages}</span>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage((value) => value + 1)}
              className="rounded border border-white/[0.08] px-2.5 py-1.5 disabled:opacity-30"
            >
              下一页
            </button>
          </div>
        </div>
      </div>

      <Modal open={generateOpen} onClose={() => setGenerateOpen(false)} title="批量生成激活码">
        {generateResult ? (
          <div>
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] p-4 text-[11px] text-emerald-200">
              已成功生成 {generateResult.created} 个激活码（{generateResult.sku_type}）。
            </div>
            <div className="mt-4 max-h-56 overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-950/50 p-3 font-mono text-[10px] leading-6 text-slate-400">
              {generateResult.codes.map((code) => <div key={code}>{code}</div>)}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <CopyButton value={generateResult.codes.join("\n")} label="复制全部" />
              <button type="button" onClick={() => setGenerateOpen(false)} className="btn-secondary py-2.5">完成</button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="admin-label">生成数量</label>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={genCount}
                  onChange={(event) => setGenCount(Number(event.target.value))}
                  className="admin-input w-full py-2.5"
                />
              </div>
              <div>
                <label className="admin-label">路由通道</label>
                <select value={genChannel} onChange={(event) => setGenChannel(event.target.value)} className="admin-input w-full py-2.5">
                  {CHANNELS.map((channel) => <option key={channel.key} value={channel.key}>{channel.label}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="admin-label">SKU</label>
              <select value={genSku} onChange={(event) => setGenSku(event.target.value)} className="admin-input w-full py-2.5">
                {data?.sku_options.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
              </select>
            </div>
            <div>
              <label className="admin-label">备注（可选）</label>
              <input value={genNote} onChange={(event) => setGenNote(event.target.value)} className="admin-input w-full py-2.5" placeholder="例如：8 月补货" />
            </div>
            <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 text-[10px] leading-5 text-slate-600">
              激活码格式为 MOLI-XXXX-XXXX-XXXX，自动排除易混淆字符 0/O/1/I。生成后即可导入商城库存。
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setGenerateOpen(false)} className="btn-secondary py-2.5">取消</button>
              <button type="button" disabled={actionLoading} onClick={() => void generate()} className="btn-primary py-2.5">
                {actionLoading && <Loader2 size={13} className="animate-spin" />}
                生成激活码
              </button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={importOpen} onClose={() => setImportOpen(false)} title="批量导入上游卡密" maxWidth="max-w-2xl">
        {importResult ? (
          <div>
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] p-4 text-[11px] text-emerald-200">
              导入完成：新增 {importResult.created} 个，跳过 {importResult.skipped} 个重复卡密。
            </div>
            <div className="mt-4 flex justify-end">
              <button type="button" onClick={() => setImportOpen(false)} className="btn-secondary py-2.5">完成</button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="admin-label">SKU</label>
                <select value={importSku} onChange={(event) => setImportSku(event.target.value)} className="admin-input w-full py-2.5">
                  {data?.sku_options.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
                </select>
              </div>
              <div>
                <label className="admin-label">路由通道</label>
                <select value={importChannel} onChange={(event) => setImportChannel(event.target.value)} className="admin-input w-full py-2.5">
                  {CHANNELS.map((channel) => <option key={channel.key} value={channel.key}>{channel.label}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="admin-label">卡密内容（每行一条）</label>
              <textarea
                value={importText}
                onChange={(event) => setImportText(event.target.value)}
                className="admin-input min-h-[180px] w-full resize-y py-3 font-mono text-[11px] leading-6"
                placeholder={"支持两种格式：\nMOLI-XXXX-XXXX-XXXX\nMOLI-XXXX-XXXX-XXXX,上游卡密\n或直接粘贴上游卡密（系统会自动生成 MOLI 前缀）"}
              />
            </div>
            <div>
              <label className="admin-label">备注（可选）</label>
              <input value={importNote} onChange={(event) => setImportNote(event.target.value)} className="admin-input w-full py-2.5" placeholder="例如：上游批次 2026-10" />
            </div>
            <div className="rounded-lg border border-amber-400/15 bg-amber-400/[0.05] p-3 text-[10px] leading-5 text-amber-200/70">
              上游卡密将使用 AES-256-GCM 加密保存，仅在核销时解密转发。请勿在公共设备操作。
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setImportOpen(false)} className="btn-secondary py-2.5">取消</button>
              <button
                type="button"
                disabled={actionLoading || !importText.trim()}
                onClick={() => void importCodes()}
                className="btn-primary py-2.5"
              >
                {actionLoading ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                开始导入
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
