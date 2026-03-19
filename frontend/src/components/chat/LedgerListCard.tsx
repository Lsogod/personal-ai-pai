import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { createLedger, deleteLedger, fetchLedgers, updateLedger, type LedgerItem } from "../../lib/api";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Input } from "../ui/input";
import { Pencil, Plus, Trash2 } from "../ui/icons";

interface LedgerListCardProps {
  token: string | null;
}

type RangeKey = "all" | "today" | "7d" | "30d" | "month";
type SortKey = "time_desc" | "time_asc" | "amount_desc" | "amount_asc";

const PAGE_SIZE = 30;
const BUILTIN_CATEGORIES = ["餐饮", "交通", "购物", "居家", "娱乐", "医疗", "教育", "其他"];

function nowLocalInputValue(): string {
  const dt = new Date();
  const y = dt.getFullYear();
  const m = `${dt.getMonth() + 1}`.padStart(2, "0");
  const d = `${dt.getDate()}`.padStart(2, "0");
  const hh = `${dt.getHours()}`.padStart(2, "0");
  const mm = `${dt.getMinutes()}`.padStart(2, "0");
  return `${y}-${m}-${d}T${hh}:${mm}`;
}

function parseDateTime(value: string): Date {
  const raw = String(value || "").trim();
  if (!raw) return new Date("");

  let dt = new Date(raw);
  if (!Number.isNaN(dt.getTime())) return dt;

  dt = new Date(raw.replace("T", " "));
  if (!Number.isNaN(dt.getTime())) return dt;

  return new Date(raw.replace(/-/g, "/").replace("T", " ").replace(/Z$/, ""));
}

function toUtcIsoFromLocalInput(value: string): string | undefined {
  const dt = parseDateTime(value);
  if (Number.isNaN(dt.getTime())) return undefined;
  return dt.toISOString();
}

function formatDateTime(value: string): string {
  const dt = parseDateTime(value);
  if (Number.isNaN(dt.getTime())) return "--";
  const mm = `${dt.getMonth() + 1}`.padStart(2, "0");
  const dd = `${dt.getDate()}`.padStart(2, "0");
  const hh = `${dt.getHours()}`.padStart(2, "0");
  const mi = `${dt.getMinutes()}`.padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function formatAmount(n: number): string {
  return n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function normalizeCategory(value: string | undefined): string {
  const text = String(value || "").trim();
  return text || "其他";
}

function rowTimeMs(row: LedgerItem): number {
  const dt = parseDateTime(row.transaction_date || row.created_at || "");
  return dt.getTime();
}

function inRange(row: LedgerItem, range: RangeKey): boolean {
  if (range === "all") return true;
  const ms = rowTimeMs(row);
  if (!Number.isFinite(ms)) return false;

  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const todayEnd = todayStart + 24 * 60 * 60 * 1000;
  if (range === "today") return ms >= todayStart && ms < todayEnd;
  if (range === "7d") return ms >= todayStart - 6 * 24 * 60 * 60 * 1000 && ms < todayEnd;
  if (range === "30d") return ms >= todayStart - 29 * 24 * 60 * 60 * 1000 && ms < todayEnd;

  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).getTime();
  const nextMonthStart = new Date(now.getFullYear(), now.getMonth() + 1, 1).getTime();
  return ms >= monthStart && ms < nextMonthStart;
}

function sortRows(rows: LedgerItem[], sort: SortKey): LedgerItem[] {
  const copied = [...rows];
  copied.sort((a, b) => {
    const ta = rowTimeMs(a);
    const tb = rowTimeMs(b);
    const aa = Number(a.amount || 0);
    const ab = Number(b.amount || 0);
    if (sort === "time_asc") return ta - tb;
    if (sort === "amount_desc") return ab - aa || tb - ta;
    if (sort === "amount_asc") return aa - ab || tb - ta;
    return tb - ta;
  });
  return copied;
}

function dedupeRows(pages: LedgerItem[][]): LedgerItem[] {
  const seen = new Set<number>();
  const out: LedgerItem[] = [];
  for (const page of pages) {
    for (const row of page || []) {
      const id = Number(row.id);
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push({ ...row, category: normalizeCategory(row.category) });
    }
  }
  return out;
}

export function LedgerListCard({ token }: LedgerListCardProps) {
  const queryClient = useQueryClient();

  const [showCreate, setShowCreate] = useState(false);
  const [createAmount, setCreateAmount] = useState("");
  const [createItem, setCreateItem] = useState("");
  const [createCategory, setCreateCategory] = useState("其他");
  const [createCustomCategory, setCreateCustomCategory] = useState("");
  const [createDateTime, setCreateDateTime] = useState(nowLocalInputValue());

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editAmount, setEditAmount] = useState("");
  const [editCategory, setEditCategory] = useState("其他");
  const [editCustomCategory, setEditCustomCategory] = useState("");
  const [editItem, setEditItem] = useState("");

  const [confirmDelete, setConfirmDelete] = useState<{ id: number } | null>(null);

  const [keyword, setKeyword] = useState("");
  const [filterCategory, setFilterCategory] = useState("all");
  const [filterRange, setFilterRange] = useState<RangeKey>("all");
  const [sortMode, setSortMode] = useState<SortKey>("time_desc");

  const ledgerQuery = useInfiniteQuery({
    queryKey: ["ledgers", "infinite", token],
    enabled: !!token,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => fetchLedgers(token, PAGE_SIZE, pageParam),
    getNextPageParam: (lastPage) => {
      if (!Array.isArray(lastPage) || lastPage.length < PAGE_SIZE) return undefined;
      const tail = lastPage[lastPage.length - 1];
      const id = Number(tail?.id);
      return Number.isFinite(id) && id > 0 ? id : undefined;
    },
    refetchInterval: token ? 20000 : false,
  });

  const allRows = useMemo(
    () => dedupeRows(ledgerQuery.data?.pages || []),
    [ledgerQuery.data?.pages]
  );

  const categoryOptions = useMemo(() => {
    const count = new Map<string, number>();
    for (const row of allRows) {
      const c = normalizeCategory(row.category);
      count.set(c, (count.get(c) || 0) + 1);
    }
    return [...count.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([name]) => name);
  }, [allRows]);

  const formCategories = useMemo(
    () => [...new Set([...BUILTIN_CATEGORIES, ...categoryOptions])],
    [categoryOptions]
  );

  const filteredRows = useMemo(() => {
    const search = keyword.trim().toLowerCase();
    const rows = allRows.filter((row) => {
      if (filterCategory !== "all" && normalizeCategory(row.category) !== filterCategory) return false;
      if (!inRange(row, filterRange)) return false;
      if (!search) return true;
      const hay = `${row.item || ""} ${row.category || ""} ${row.amount || ""}`.toLowerCase();
      return hay.includes(search);
    });
    return sortRows(rows, sortMode);
  }, [allRows, filterCategory, filterRange, keyword, sortMode]);

  const createMutation = useMutation({
    mutationFn: (payload: { amount: number; category: string; item: string; transaction_date?: string }) =>
      createLedger(payload, token),
    onSuccess: async () => {
      setShowCreate(false);
      setCreateAmount("");
      setCreateItem("");
      setCreateCategory("其他");
      setCreateCustomCategory("");
      setCreateDateTime(nowLocalInputValue());
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] }),
        queryClient.invalidateQueries({ queryKey: ["calendar"] }),
      ]);
    },
  });

  const updateMutation = useMutation({
    mutationFn: (payload: { id: number; amount: number; category: string; item: string }) =>
      updateLedger(payload.id, { amount: payload.amount, category: payload.category, item: payload.item }, token),
    onSuccess: async () => {
      setEditingId(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] }),
        queryClient.invalidateQueries({ queryKey: ["calendar"] }),
      ]);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (ledgerId: number) => deleteLedger(ledgerId, token),
    onSuccess: async () => {
      if (editingId) setEditingId(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] }),
        queryClient.invalidateQueries({ queryKey: ["calendar"] }),
      ]);
    },
  });

  function beginEdit(row: LedgerItem) {
    setEditingId(row.id);
    setEditAmount(String(row.amount));
    const c = normalizeCategory(row.category);
    if (formCategories.includes(c)) {
      setEditCategory(c);
      setEditCustomCategory("");
    } else {
      setEditCategory("其他");
      setEditCustomCategory(c);
    }
    setEditItem(row.item || "");
  }

  function onSubmitCreate() {
    const amount = Number(createAmount);
    if (!Number.isFinite(amount) || amount <= 0) return;
    const custom = createCustomCategory.trim();
    const category = custom || createCategory || "其他";
    createMutation.mutate({
      amount,
      category,
      item: createItem.trim() || "手动记录",
      transaction_date: toUtcIsoFromLocalInput(createDateTime),
    });
  }

  function onSubmitEdit(rowId: number) {
    const amount = Number(editAmount);
    if (!Number.isFinite(amount) || amount <= 0) return;
    const custom = editCustomCategory.trim();
    const category = custom || editCategory || "其他";
    updateMutation.mutate({
      id: rowId,
      amount,
      category,
      item: editItem.trim() || "手动记录",
    });
  }

  const loading = ledgerQuery.isLoading;
  const loadingMore = ledgerQuery.isFetchingNextPage;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-content">账单明细</h2>
          <Button size="sm" variant={showCreate ? "ghost" : "default"} onClick={() => setShowCreate((v) => !v)}>
            <Plus size={14} />
            {showCreate ? "收起" : "记一笔"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {showCreate && (
          <div className="rounded-xl border border-border bg-surface-secondary p-3 space-y-2">
            <Input
              type="number"
              step="0.01"
              min="0"
              placeholder="金额"
              value={createAmount}
              onChange={(e) => setCreateAmount(e.target.value)}
            />
            <Input
              placeholder="摘要（如 午饭、地铁）"
              value={createItem}
              onChange={(e) => setCreateItem(e.target.value)}
            />
            <div className="grid grid-cols-2 gap-2">
              <select
                className="h-10 rounded-xl border border-border bg-surface-input px-3 text-sm text-content"
                value={createCategory}
                onChange={(e) => setCreateCategory(e.target.value)}
              >
                {formCategories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <Input
                placeholder="自定义分类（可选）"
                value={createCustomCategory}
                maxLength={20}
                onChange={(e) => setCreateCustomCategory(e.target.value)}
              />
            </div>
            <Input
              type="datetime-local"
              value={createDateTime}
              onChange={(e) => setCreateDateTime(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>
                取消
              </Button>
              <Button
                size="sm"
                onClick={onSubmitCreate}
                disabled={createMutation.isPending || !createAmount.trim()}
              >
                {createMutation.isPending ? "提交中..." : "提交"}
              </Button>
            </div>
          </div>
        )}

        <div className="rounded-xl border border-border p-3 space-y-2">
          <Input
            placeholder="搜索摘要、分类、金额"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <select
              className="h-10 rounded-xl border border-border bg-surface-input px-3 text-sm text-content"
              value={filterCategory}
              onChange={(e) => setFilterCategory(e.target.value)}
            >
              <option value="all">分类: 全部</option>
              {categoryOptions.map((c) => (
                <option key={c} value={c}>
                  分类: {c}
                </option>
              ))}
            </select>
            <select
              className="h-10 rounded-xl border border-border bg-surface-input px-3 text-sm text-content"
              value={filterRange}
              onChange={(e) => setFilterRange(e.target.value as RangeKey)}
            >
              <option value="all">时间: 全部</option>
              <option value="today">时间: 今天</option>
              <option value="7d">时间: 7天</option>
              <option value="30d">时间: 30天</option>
              <option value="month">时间: 本月</option>
            </select>
            <select
              className="h-10 rounded-xl border border-border bg-surface-input px-3 text-sm text-content"
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as SortKey)}
            >
              <option value="time_desc">排序: 最新优先</option>
              <option value="time_asc">排序: 最早优先</option>
              <option value="amount_desc">排序: 金额从高到低</option>
              <option value="amount_asc">排序: 金额从低到高</option>
            </select>
          </div>
          <div className="text-xs text-content-tertiary">共 {filteredRows.length} 条</div>
        </div>

        <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
          {loading && <p className="text-sm text-content-tertiary text-center py-6">加载中...</p>}
          {!loading && filteredRows.length === 0 && (
            <p className="text-sm text-content-tertiary text-center py-6">暂无符合条件的账单</p>
          )}

          {filteredRows.map((row) => (
            <div key={row.id} className="rounded-xl border border-border p-3 transition-colors hover:bg-surface-hover">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-content-tertiary">
                  #{row.id} · {formatDateTime(row.transaction_date || row.created_at)}
                </span>
                {editingId !== row.id && (
                  <div className="flex gap-1">
                    <button
                      onClick={() => beginEdit(row)}
                      className="p-1 rounded-md text-content-tertiary hover:text-accent hover:bg-surface-active transition-colors"
                      aria-label="编辑"
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      onClick={() => setConfirmDelete({ id: row.id })}
                      className="p-1 rounded-md text-content-tertiary hover:text-danger hover:bg-surface-active transition-colors"
                      aria-label="删除"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                )}
              </div>

              {editingId === row.id ? (
                <div className="mt-2 space-y-2">
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    value={editAmount}
                    onChange={(e) => setEditAmount(e.target.value)}
                    placeholder="金额"
                  />
                  <div className="grid grid-cols-2 gap-2">
                    <select
                      className="h-10 rounded-xl border border-border bg-surface-input px-3 text-sm text-content"
                      value={editCategory}
                      onChange={(e) => setEditCategory(e.target.value)}
                    >
                      {formCategories.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                    <Input
                      placeholder="自定义分类（可选）"
                      value={editCustomCategory}
                      maxLength={20}
                      onChange={(e) => setEditCustomCategory(e.target.value)}
                    />
                  </div>
                  <Input value={editItem} onChange={(e) => setEditItem(e.target.value)} placeholder="摘要" />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={() => onSubmitEdit(row.id)}
                      disabled={updateMutation.isPending || !editAmount.trim()}
                    >
                      保存
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                      取消
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-between">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-content truncate">{row.item}</p>
                    <p className="text-xs text-content-tertiary mt-0.5">{normalizeCategory(row.category)}</p>
                  </div>
                  <p className="text-sm font-semibold text-content">
                    ¥{formatAmount(Number(row.amount || 0))}{" "}
                    <span className="text-xs text-content-tertiary font-normal">{row.currency}</span>
                  </p>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="flex justify-center">
          <Button
            size="sm"
            variant="ghost"
            disabled={!ledgerQuery.hasNextPage || loadingMore}
            onClick={() => ledgerQuery.fetchNextPage()}
          >
            {loadingMore ? "加载中..." : ledgerQuery.hasNextPage ? "加载更多" : "没有更多了"}
          </Button>
        </div>
      </CardContent>

      <ConfirmDialog
        open={!!confirmDelete}
        title="删除账单"
        message={confirmDelete ? `确认删除账单 #${confirmDelete.id} 吗？` : ""}
        variant="danger"
        confirmText="删除"
        onConfirm={() => {
          if (confirmDelete) deleteMutation.mutate(confirmDelete.id);
          setConfirmDelete(null);
        }}
        onCancel={() => setConfirmDelete(null)}
      />
    </Card>
  );
}
