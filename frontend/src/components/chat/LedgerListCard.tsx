import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteLedger, fetchLedgers, updateLedger, type LedgerItem } from "../../lib/api";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Input } from "../ui/input";

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
    queryFn: () => fetchLedgers(token, 20)
  });

  const updateMutation = useMutation({
    mutationFn: (payload: { id: number; amount: number; category: string; item: string }) =>
      updateLedger(
        payload.id,
        { amount: payload.amount, category: payload.category, item: payload.item },
        token
      ),
    onSuccess: async () => {
      setEditingId(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] })
      ]);
    }
  });

  const deleteMutation = useMutation({
    mutationFn: (ledgerId: number) => deleteLedger(ledgerId, token),
    onSuccess: async () => {
      if (editingId) {
        setEditingId(null);
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ledgers"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] })
      ]);
    }
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
        <h2 className="text-sm font-semibold text-slate-900">最近账单</h2>
      </CardHeader>
      <CardContent className="space-y-2 pt-3">
        {ledgers.length === 0 ? (
          <p className="text-sm text-slate-500">暂无账单</p>
        ) : (
          ledgers.slice(0, 10).map((row) => (
            <div key={row.id} className="rounded-lg border border-slate-200 p-2">
              <p className="text-xs text-slate-500">
                #{row.id} · {new Date(row.created_at).toLocaleString()}
              </p>
              {editingId === row.id ? (
                <div className="mt-2 space-y-2">
                  <Input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="金额" />
                  <select
                    className="h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900"
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                  >
                    {CATEGORIES.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                  <Input value={item} onChange={(e) => setItem(e.target.value)} placeholder="摘要" />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      onClick={() =>
                        updateMutation.mutate({
                          id: row.id,
                          amount: Number(amount),
                          category,
                          item
                        })
                      }
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
                  <div className="mt-1 flex items-center justify-between gap-2">
                    <div>
                      <p className="text-sm text-slate-900">
                        {row.item} · {row.amount} {row.currency}
                      </p>
                      <p className="text-xs text-slate-600">分类：{row.category}</p>
                    </div>
                    <div className="flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => beginEdit(row)}>
                        修改
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          if (window.confirm(`确认删除账单 #${row.id} 吗？`)) {
                            deleteMutation.mutate(row.id);
                          }
                        }}
                        disabled={deleteMutation.isPending}
                      >
                        删除
                      </Button>
                    </div>
                </div>
              )}
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
