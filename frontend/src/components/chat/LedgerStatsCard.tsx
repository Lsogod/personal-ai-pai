import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip } from "recharts";

import { Card, CardContent, CardHeader } from "../ui/card";

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
    { name: "笔数", value: stats.count }
  ];

  return (
    <Card>
      <CardHeader>
        <h2 className="text-sm font-semibold text-slate-900">账单概览</h2>
      </CardHeader>
      <CardContent className="space-y-3 pt-3">
        <div className="flex justify-between text-sm">
          <span className="text-slate-500">30 天总额</span>
          <strong className="text-slate-900">{stats.total}</strong>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-slate-500">记录笔数</span>
          <strong className="text-slate-900">{stats.count}</strong>
        </div>
        <div className="h-28 rounded-lg bg-slate-50 p-2">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <XAxis dataKey="name" hide />
              <YAxis hide />
              <Tooltip />
              <Line type="monotone" dataKey="value" stroke="#0f172a" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
