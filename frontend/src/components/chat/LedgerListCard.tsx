import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteLedger, fetchLedgers, updateLedger, type LedgerItem } from "../../lib/api";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Input } from "../ui/input";
import { Pencil, Trash2 } from "../ui/icons";

interface LedgerListCardProps {
  token: string | null;
}

const CATEGORIES = ["餐饮", "交通", "购物", "居家", "娱乐", "医疗", "其他"];

export function LedgerListCard({ token }: LedgerListCardProps) {
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState("其他");
  const [item, setItem] = useState("");

  const { data: ledgers = [] } = useQuery<LedgerItem[]>({
    queryKey: ["ledgers"],
    enabled: !!token,
    queryFn: () => fetchLedgers(token, 20),
  });

  const updateMutation = useMutation({
    mutationFn: (payload: { id: number; amount: number; category: string; item: string }) =>
      updateLedger(payload.id, { amount: payload.amount, category: payload.category, item: payload.item }, token),
    onSuccess: async () => {
      setEditingId(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] }),
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
      ]);
    },
  });

  function beginEdit(row: LedgerItem) {
    setEditingId(row.id);
    setAmount(String(row.amount));
    setCategory(row.category || "其他");
    setItem(row.item || "");
  }

  return (
    <Card>
      <CardHeader>
        <h2 className="text-sm font-semibold text-content">最近账单</h2>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {ledgers.length === 0 ? (
            <p className="text-sm text-content-tertiary text-center py-6">暂无账单记录</p>
          ) : (
            ledgers.slice(0, 10).map((row) => (
              <div key={row.id} className="rounded-xl border border-border p-3 transition-colors hover:bg-surface-hover">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-content-tertiary">
                    #{row.id} · {new Date(row.created_at).toLocaleDateString()}
                  </span>
                  {editingId !== row.id && (
                    <div className="flex gap-1">
                      <button
                        onClick={() => beginEdit(row)}
                        className="p-1 rounded-md text-content-tertiary hover:text-accent hover:bg-accent/10 transition-colors"
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        onClick={() => {
                          if (window.confirm(`确认删除账单 #${row.id} 吗？`)) deleteMutation.mutate(row.id);
                        }}
                        className="p-1 rounded-md text-content-tertiary hover:text-danger hover:bg-danger/10 transition-colors"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  )}
                </div>
                {editingId === row.id ? (
                  <div className="mt-2 space-y-2 animate-fade-in">
                    <Input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="金额" />
                    <select
                      className="h-10 w-full rounded-xl border border-border bg-surface-input px-3 text-sm text-content"
                      value={category}
                      onChange={(e) => setCategory(e.target.value)}
                    >
                      {CATEGORIES.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                    <Input value={item} onChange={(e) => setItem(e.target.value)} placeholder="摘要" />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        onClick={() => updateMutation.mutate({ id: row.id, amount: Number(amount), category, item })}
                        disabled={updateMutation.isPending || !amount}
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
                    <div>
                      <p className="text-sm font-medium text-content">{row.item}</p>
                      <p className="text-xs text-content-tertiary mt-0.5">{row.category}</p>
                    </div>
                    <p className="text-sm font-semibold text-content">
                      ¥{row.amount} <span className="text-xs text-content-tertiary font-normal">{row.currency}</span>
                    </p>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}
