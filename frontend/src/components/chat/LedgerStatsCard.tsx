import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { fetchLedgers, type LedgerItem } from "../../lib/api";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Wallet } from "../ui/icons";

interface LedgerStats {
  total: number;
  count: number;
}

interface LedgerStatsCardProps {
  stats: LedgerStats;
  token: string | null;
}

interface DailyPoint {
  day: number;
  label: string;
  amount: number;
}

interface CategoryPoint {
  category: string;
  amount: number;
  ratio: number;
}

const CURRENCY = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatAmount(value: number): string {
  return CURRENCY.format(Number.isFinite(value) ? value : 0);
}

function parseRowDate(row: LedgerItem): Date | null {
  const raw = String(row.transaction_date || row.created_at || "").trim();
  if (!raw) return null;
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return null;
  return dt;
}

function buildInsight(total: number, trendPct: number, top?: CategoryPoint): string {
  if (total <= 0) return "本月暂无账单，记一笔后会自动生成趋势与占比。";
  if (top && top.ratio >= 45) {
    return `本月“${top.category}”支出占比 ${top.ratio.toFixed(1)}%，建议优先关注该类目。`;
  }
  if (trendPct >= 20) return "近 7 天支出较上一个 7 天明显上升，可关注近期新增消费。";
  if (trendPct <= -20) return "近 7 天支出较上一个 7 天明显下降，当前消费节奏较稳。";
  return "近两周消费波动较小，可继续保持当前支出节奏。";
}

export function LedgerStatsCard({ stats, token }: LedgerStatsCardProps) {
  const query = useQuery<LedgerItem[]>({
    queryKey: ["ledgers", "stats-card", token],
    enabled: !!token,
    queryFn: () => fetchLedgers(token, 200),
    refetchInterval: token ? 15000 : false,
  });

  const now = new Date();
  const currentYear = now.getFullYear();
  const currentMonth = now.getMonth();
  const currentDay = now.getDate();

  const monthlyRows = useMemo(() => {
    const rows = Array.isArray(query.data) ? query.data : [];
    return rows.filter((row) => {
      const dt = parseRowDate(row);
      if (!dt) return false;
      return dt.getFullYear() === currentYear && dt.getMonth() === currentMonth;
    });
  }, [query.data, currentYear, currentMonth]);

  const model = useMemo(() => {
    const daySpend = new Map<number, number>();
    const categorySpend = new Map<string, number>();
    for (let d = 1; d <= currentDay; d += 1) daySpend.set(d, 0);

    let monthTotal = 0;
    let maxSingle = 0;
    let peakDayAmount = 0;
    let peakDay = "-";

    monthlyRows.forEach((row) => {
      const dt = parseRowDate(row);
      if (!dt) return;
      const amount = Number(row.amount) || 0;
      if (!Number.isFinite(amount)) return;

      monthTotal += amount;
      if (amount > maxSingle) maxSingle = amount;

      const day = dt.getDate();
      daySpend.set(day, (daySpend.get(day) || 0) + amount);

      const category = String(row.category || "其他").trim() || "其他";
      categorySpend.set(category, (categorySpend.get(category) || 0) + amount);
    });

    const daily: DailyPoint[] = [];
    for (let d = 1; d <= currentDay; d += 1) {
      const amount = Number((daySpend.get(d) || 0).toFixed(2));
      daily.push({ day: d, label: `${d}日`, amount });
      if (amount > peakDayAmount) {
        peakDayAmount = amount;
        peakDay = `${currentMonth + 1}/${d}`;
      }
    }

    const categories: CategoryPoint[] = Array.from(categorySpend.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([category, amount]) => ({
        category,
        amount: Number(amount.toFixed(2)),
        ratio: monthTotal > 0 ? Number(((amount / monthTotal) * 100).toFixed(1)) : 0,
      }));

    const last7 = daily.slice(Math.max(0, daily.length - 7));
    const prev7 = daily.slice(Math.max(0, daily.length - 14), Math.max(0, daily.length - 7));
    const last7Total = last7.reduce((sum, item) => sum + item.amount, 0);
    const prev7Total = prev7.reduce((sum, item) => sum + item.amount, 0);
    const trendPct =
      prev7Total <= 0 ? (last7Total > 0 ? 100 : 0) : ((last7Total - prev7Total) / prev7Total) * 100;

    return {
      daily,
      categories,
      monthTotal,
      recordCount: monthlyRows.length,
      maxSingle,
      avgPerDay: currentDay > 0 ? monthTotal / currentDay : 0,
      trendPct,
      peakDay,
      peakDayAmount,
      insight: buildInsight(monthTotal, trendPct, categories[0]),
    };
  }, [monthlyRows, currentDay, currentMonth]);

  const hasMonthlyData = model.recordCount > 0;
  const shownTotal = hasMonthlyData ? model.monthTotal : stats.total;
  const shownCount = hasMonthlyData ? model.recordCount : stats.count;
  const hasTrend = model.daily.some((item) => item.amount > 0);

  if (query.isLoading) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-surface-hover animate-pulse" />
            <div className="space-y-1.5">
              <div className="h-4 w-20 rounded bg-surface-hover animate-pulse" />
              <div className="h-3 w-28 rounded bg-surface-hover animate-pulse" />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-surface-secondary p-4 h-20 animate-pulse" />
            <div className="rounded-xl bg-surface-secondary p-4 h-20 animate-pulse" />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg bg-surface-secondary p-3 h-14 animate-pulse" />
            <div className="rounded-lg bg-surface-secondary p-3 h-14 animate-pulse" />
            <div className="rounded-lg bg-surface-secondary p-3 h-14 animate-pulse" />
          </div>
          <div className="rounded-xl bg-surface-secondary p-3 h-48 animate-pulse" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface-hover text-accent">
            <Wallet size={20} />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-content">账单概览</h2>
            <p className="text-xs text-content-tertiary">本月统计（1号至今）</p>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-xl bg-surface-secondary p-4">
            <p className="text-xs text-content-tertiary">总支出</p>
            <p className="mt-1 text-2xl font-bold text-content">{formatAmount(shownTotal)}</p>
          </div>
          <div className="rounded-xl bg-surface-secondary p-4">
            <p className="text-xs text-content-tertiary">记录笔数</p>
            <p className="mt-1 text-2xl font-bold text-content">{shownCount}</p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3 text-xs">
          <div className="rounded-lg bg-surface-secondary p-3">
            <p className="text-content-tertiary">日均支出</p>
            <p className="mt-1 font-semibold text-content">{formatAmount(model.avgPerDay)}</p>
          </div>
          <div className="rounded-lg bg-surface-secondary p-3">
            <p className="text-content-tertiary">最大单笔</p>
            <p className="mt-1 font-semibold text-content">{formatAmount(model.maxSingle)}</p>
          </div>
          <div className="rounded-lg bg-surface-secondary p-3">
            <p className="text-content-tertiary">近7天环比</p>
            <p className={`mt-1 font-semibold ${model.trendPct >= 0 ? "text-danger" : "text-success"}`}>
              {model.trendPct >= 0 ? "+" : ""}
              {model.trendPct.toFixed(1)}%
            </p>
          </div>
        </div>

        <div className="rounded-xl bg-surface-secondary p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs text-content-tertiary">月内每日支出趋势</p>
            <p className="text-xs text-content-secondary">
              峰值 {model.peakDay} · {formatAmount(model.peakDayAmount)}
            </p>
          </div>
          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={model.daily}>
                <defs>
                  <linearGradient id="ledger-area-fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="rgb(var(--color-accent))" stopOpacity={0.36} />
                    <stop offset="95%" stopColor="rgb(var(--color-accent))" stopOpacity={0.04} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="rgb(var(--color-border))" strokeDasharray="3 3" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} tickMargin={8} />
                <YAxis tick={{ fontSize: 11 }} width={48} />
                <Tooltip
                  formatter={(value: number) => [formatAmount(Number(value) || 0), "支出"]}
                  labelFormatter={(label) => `日期：${label}`}
                  contentStyle={{
                    background: "rgb(var(--color-bg-card))",
                    border: "1px solid rgb(var(--color-border))",
                    borderRadius: "12px",
                    fontSize: "12px",
                    color: "rgb(var(--color-text-primary))",
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="amount"
                  stroke="rgb(var(--color-accent))"
                  strokeWidth={2.2}
                  fill="url(#ledger-area-fill)"
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          {!hasTrend && <p className="mt-2 text-xs text-content-tertiary">本月暂无趋势数据。</p>}
        </div>

        <div className="rounded-xl bg-surface-secondary p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs text-content-tertiary">分类占比 Top 5</p>
            <p className="text-xs text-content-secondary">按金额</p>
          </div>
          {model.categories.length === 0 ? (
            <p className="text-xs text-content-tertiary">暂无分类数据</p>
          ) : (
            <div className="space-y-2">
              {model.categories.map((row) => (
                <div key={row.category}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="text-content">{row.category}</span>
                    <span className="text-content-secondary">
                      {formatAmount(row.amount)} · {row.ratio.toFixed(1)}%
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-surface-card">
                    <div
                      className="h-2 rounded-full bg-accent"
                      style={{ width: `${Math.max(4, Math.min(100, row.ratio))}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-border bg-surface p-3">
          <p className="text-xs text-content-tertiary">消费洞察</p>
          <p className="mt-1 text-sm text-content">{model.insight}</p>
        </div>
      </CardContent>
    </Card>
  );
}
