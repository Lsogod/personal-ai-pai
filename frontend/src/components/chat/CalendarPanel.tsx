import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchCalendar, type CalendarDay, type CalendarResponse } from "../../lib/api";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";

interface CalendarPanelProps {
  token: string | null;
}

function formatDate(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function monthTitle(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function firstDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function nextMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth() + 1, 1);
}

function buildCalendarCells(cursor: Date): string[] {
  const start = firstDay(cursor);
  const firstWeekday = start.getDay(); // 0 = Sun
  const daysInMonth = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate();
  const cells: string[] = [];
  for (let i = 0; i < firstWeekday; i++) {
    cells.push("");
  }
  for (let day = 1; day <= daysInMonth; day++) {
    cells.push(formatDate(new Date(cursor.getFullYear(), cursor.getMonth(), day)));
  }
  while (cells.length % 7 !== 0) {
    cells.push("");
  }
  return cells;
}

function dayNumber(value: string): string {
  if (!value) {
    return "";
  }
  return String(Number(value.slice(-2)));
}

export function CalendarPanel({ token }: CalendarPanelProps) {
  const [cursor, setCursor] = useState<Date>(() => firstDay(new Date()));
  const startDate = formatDate(firstDay(cursor));
  const endDate = formatDate(nextMonth(cursor));
  const [selectedDate, setSelectedDate] = useState<string>(formatDate(new Date()));

  const { data } = useQuery<CalendarResponse>({
    queryKey: ["calendar", startDate, endDate],
    queryFn: () => fetchCalendar(token, startDate, endDate)
  });

  const dayMap = useMemo(() => {
    const map = new Map<string, CalendarDay>();
    (data?.days || []).forEach((day) => map.set(day.date, day));
    return map;
  }, [data]);

  const cells = useMemo(() => buildCalendarCells(cursor), [cursor]);
  const selected = dayMap.get(selectedDate);
  const totalMonthSpend = (data?.days || []).reduce((acc, day) => acc + day.ledger_total, 0);
  const totalMonthSchedules = (data?.days || []).reduce((acc, day) => acc + day.schedule_count, 0);

  useEffect(() => {
    const currentMonth = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}`;
    if (!selectedDate.startsWith(currentMonth)) {
      setSelectedDate(`${currentMonth}-01`);
      return;
    }
    if (data?.days && !dayMap.has(selectedDate)) {
      setSelectedDate(data.days[0]?.date || `${currentMonth}-01`);
    }
  }, [cursor, data, dayMap, selectedDate]);

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-900">账单与日程日历</h2>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setCursor((prev) => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))}
              >
                上月
              </Button>
              <p className="text-sm font-semibold text-slate-700">{monthTitle(cursor)}</p>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setCursor((prev) => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))}
              >
                下月
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="pt-3">
          <div className="mb-3 grid grid-cols-7 gap-2 text-center text-xs text-slate-500">
            {["日", "一", "二", "三", "四", "五", "六"].map((name) => (
              <div key={name}>{name}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-2">
            {cells.map((cell, index) => {
              if (!cell) {
                return <div key={`blank-${index}`} className="h-24 rounded-lg border border-transparent bg-transparent" />;
              }
              const day = dayMap.get(cell);
              const active = selectedDate === cell;
              return (
                <button
                  key={cell}
                  type="button"
                  onClick={() => setSelectedDate(cell)}
                  className={[
                    "h-24 rounded-lg border px-2 py-2 text-left",
                    active ? "border-slate-900 bg-slate-100" : "border-slate-200 bg-white hover:bg-slate-50"
                  ].join(" ")}
                >
                  <p className="text-xs font-semibold text-slate-800">{dayNumber(cell)}</p>
                  <p className="mt-2 text-[11px] text-slate-600">账单 {day?.ledger_count || 0}</p>
                  <p className="text-[11px] text-slate-600">日程 {day?.schedule_count || 0}</p>
                  {day && day.ledger_total > 0 ? (
                    <p className="mt-1 text-[11px] font-semibold text-slate-900">¥{day.ledger_total.toFixed(0)}</p>
                  ) : null}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-slate-900">当月概览</h2>
        </CardHeader>
        <CardContent className="space-y-3 pt-3 text-sm text-slate-700">
          <p>总支出：¥{totalMonthSpend.toFixed(2)}</p>
          <p>总日程：{totalMonthSchedules} 条</p>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <p className="text-xs font-semibold text-slate-600">{selectedDate} 详情</p>
            {selected ? (
              <div className="mt-2 space-y-2">
                <div>
                  <p className="text-xs font-semibold text-slate-600">账单</p>
                  {selected.ledgers.length === 0 ? (
                    <p className="text-xs text-slate-500">无账单</p>
                  ) : (
                    selected.ledgers.map((item) => (
                      <p key={item.id} className="text-xs text-slate-700">
                        #{item.id} {item.item} ¥{item.amount.toFixed(2)} ({item.category})
                      </p>
                    ))
                  )}
                </div>
                <div>
                  <p className="text-xs font-semibold text-slate-600">日程</p>
                  {selected.schedules.length === 0 ? (
                    <p className="text-xs text-slate-500">无日程</p>
                  ) : (
                    selected.schedules.map((item) => (
                      <p key={item.id} className="text-xs text-slate-700">
                        #{item.id} {item.content} [{item.status}]
                      </p>
                    ))
                  )}
                </div>
              </div>
            ) : (
              <p className="mt-2 text-xs text-slate-500">当天无记录。</p>
            )}
          </div>
          <p className="text-xs text-slate-500">
            Telegram/飞书可用命令：`/calendar today`、`/calendar week`、`/calendar month`。
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
