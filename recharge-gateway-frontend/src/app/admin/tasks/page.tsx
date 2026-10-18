"use client";

import { Loader2, RefreshCw, RotateCcw, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { api, queryString } from "@/lib/api";
import { handleAdminError } from "@/lib/admin";
import { formatDateTime } from "@/lib/format";
import type { AdminTaskList } from "@/lib/types";

const STATE_TABS = [
  { key: "ALL", label: "全部" },
  { key: "PENDING", label: "排队中" },
  { key: "PAYING", label: "支付中" },
  { key: "AWAITING_3DS", label: "等待 3DS" },
  { key: "SUCCESS", label: "成功" },
  { key: "FAILED", label: "失败" },
];

export default function AdminTasksPage() {
  const [data, setData] = useState<AdminTaskList | null>(null);
  const [state, setState] = useState("ALL");
  const [queryInput, setQueryInput] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api<AdminTaskList>(
        `/api/v1/admin/tasks${queryString({
          state,
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
  }, [state, submittedQuery, page]);

  useEffect(() => {
    void load();
  }, [load]);

  async function retry(taskId: string) {
    if (!window.confirm("确认重新执行该充值任务？系统将使用已保存的 Session 凭证重试。")) return;
    setRetrying(taskId);
    setError(null);
    try {
      await api(`/api/v1/admin/tasks/${taskId}/retry`, { method: "POST" });
      await load();
    } catch (err) {
      setError(handleAdminError(err));
    } finally {
      setRetrying(null);
    }
  }

  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / 20));

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">RECHARGE TASKS</p>
          <h1 className="mt-2 font-display text-[28px] font-bold tracking-tight text-white">充值任务</h1>
          <p className="mt-2 text-[11px] text-slate-500">监控双通道充值状态，处理失败任务与人工复核。</p>
        </div>
        <div className="flex items-center gap-2">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setPage(1);
              setSubmittedQuery(queryInput.trim());
            }}
            className="relative"
          >
            <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
            <input
              value={queryInput}
              onChange={(event) => setQueryInput(event.target.value)}
              placeholder="任务编号 / 邮箱"
              className="admin-input h-9 w-[210px] pl-8"
              aria-label="搜索任务"
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

      <div className="mt-6 flex flex-wrap gap-2">
        {STATE_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => {
              setState(tab.key);
              setPage(1);
            }}
            className={`rounded-lg border px-3 py-2 text-[10px] transition ${
              state === tab.key
                ? "border-brand-400/30 bg-brand-500/15 text-brand-200"
                : "border-white/[0.07] bg-white/[0.02] text-slate-500 hover:text-slate-200"
            }`}
          >
            {tab.label}
          </button>
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
                <th>任务编号</th>
                <th>目标账号</th>
                <th>商品</th>
                <th>状态</th>
                <th>错误码</th>
                <th>尝试</th>
                <th>创建时间</th>
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
                data.items.map((task) => (
                  <tr key={task.task_id}>
                    <td className="font-mono text-[9px] text-slate-500">{task.task_id.slice(0, 8)}…</td>
                    <td className="max-w-[180px] truncate text-[10px] text-slate-400">{task.target_email_full}</td>
                    <td className="max-w-[170px] truncate text-[10px] text-slate-400">{task.sku_label}</td>
                    <td><StatusBadge status={task.state} label={task.state_label} /></td>
                    <td className="font-mono text-[9px] text-rose-300/80">{task.error_code || "—"}</td>
                    <td className="text-[10px] text-slate-500">{task.attempts}</td>
                    <td className="text-[10px] text-slate-600">{formatDateTime(task.created_at)}</td>
                    <td>
                      {task.state === "FAILED" ? (
                        <button
                          type="button"
                          disabled={retrying === task.task_id}
                          onClick={() => void retry(task.task_id)}
                          className="inline-flex items-center gap-1 text-[10px] text-brand-300 hover:text-brand-200 disabled:opacity-40"
                        >
                          {retrying === task.task_id ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />}
                          重试
                        </button>
                      ) : (
                        <span className="text-[10px] text-slate-700">—</span>
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={8} className="py-16 text-center text-[11px] text-slate-600">暂无符合条件的充值任务</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-white/[0.055] px-5 py-3 text-[10px] text-slate-600">
          <span>共 {data?.total || 0} 条任务</span>
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

      <div className="mt-4 rounded-xl border border-white/[0.055] bg-white/[0.015] px-4 py-3 text-[10px] leading-5 text-slate-600">
        失败任务在可恢复场景下会自动释放激活码；若任务提示「需人工确认」或卡密仍为执行中，请核对上游订单后再决定重试。
        Session 凭证在任务结束 5 分钟后自动清除，届时无法重试，需让用户重新提交。
      </div>
    </div>
  );
}
