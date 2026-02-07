import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip } from "recharts";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Wallet } from "../ui/icons";

interface LedgerStats {
  total: number;
  count: number;
}

interface LedgerStatsCardProps {
  stats: LedgerStats;
}

export function LedgerStatsCard({ stats }: LedgerStatsCardProps) {
  const chartData = [
    { name: "总额", value: stats.total },
    { name: "笔数", value: stats.count },
  ];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/10 text-accent">
            <Wallet size={20} />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-content">账单概览</h2>
            <p className="text-xs text-content-tertiary">近 30 天统计</p>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div className="rounded-xl bg-surface-secondary p-4">
            <p className="text-xs text-content-tertiary mb-1">总支出</p>
            <p className="text-xl font-bold text-content">¥{stats.total.toFixed(2)}</p>
          </div>
          <div className="rounded-xl bg-surface-secondary p-4">
            <p className="text-xs text-content-tertiary mb-1">记录笔数</p>
            <p className="text-xl font-bold text-content">{stats.count}</p>
          </div>
        </div>
        <div className="h-32 rounded-xl bg-surface-secondary p-3">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="rgb(var(--color-accent))" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="rgb(var(--color-accent))" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="name" hide />
              <YAxis hide />
              <Tooltip
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
                dataKey="value"
                stroke="rgb(var(--color-accent))"
                strokeWidth={2}
                fill="url(#colorValue)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
