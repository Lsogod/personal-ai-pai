import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchCalendar, type CalendarDay, type CalendarResponse } from "../../lib/api";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { ChevronLeft, ChevronRight, Calendar } from "../ui/icons";

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
  return `${date.getFullYear()} 年 ${date.getMonth() + 1} 月`;
}

function firstDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function nextMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth() + 1, 1);
}

function buildCalendarCells(cursor: Date): string[] {
  const start = firstDay(cursor);
  const firstWeekday = start.getDay();
  const daysInMonth = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate();
  const cells: string[] = [];
  for (let i = 0; i < firstWeekday; i++) cells.push("");
  for (let day = 1; day <= daysInMonth; day++) {
    cells.push(formatDate(new Date(cursor.getFullYear(), cursor.getMonth(), day)));
  }
  while (cells.length % 7 !== 0) cells.push("");
  return cells;
}

function dayNumber(value: string): string {
  if (!value) return "";
  return String(Number(value.slice(-2)));
}

export function CalendarPanel({ token }: CalendarPanelProps) {
  const [cursor, setCursor] = useState<Date>(() => firstDay(new Date()));
  const startDate = formatDate(firstDay(cursor));
  const endDate = formatDate(nextMonth(cursor));
  const [selectedDate, setSelectedDate] = useState<string>(formatDate(new Date()));

  const { data } = useQuery<CalendarResponse>({
    queryKey: ["calendar", startDate, endDate],
    queryFn: () => fetchCalendar(token, startDate, endDate),
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
  const today = formatDate(new Date());

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
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/10 text-accent">
                <Calendar size={20} />
              </div>
              <h2 className="text-sm font-semibold text-content">账单与日程日历</h2>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setCursor((prev) => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))}
                className="p-2 rounded-lg text-content-secondary hover:text-content hover:bg-surface-hover transition-colors"
              >
                <ChevronLeft size={18} />
              </button>
              <p className="text-sm font-semibold text-content min-w-[100px] text-center">
                {monthTitle(cursor)}
              </p>
              <button
                onClick={() => setCursor((prev) => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))}
                className="p-2 rounded-lg text-content-secondary hover:text-content hover:bg-surface-hover transition-colors"
              >
                <ChevronRight size={18} />
              </button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="mb-3 grid grid-cols-7 gap-1 text-center text-xs font-medium text-content-tertiary">
            {["日", "一", "二", "三", "四", "五", "六"].map((name) => (
              <div key={name} className="py-2">{name}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {cells.map((cell, index) => {
              if (!cell) {
                return <div key={`blank-${index}`} className="aspect-square" />;
              }
              const day = dayMap.get(cell);
              const active = selectedDate === cell;
              const isToday = cell === today;
              const hasData = day && (day.ledger_count > 0 || day.schedule_count > 0);
              return (
                <button
                  key={cell}
                  type="button"
                  onClick={() => setSelectedDate(cell)}
                  className={`
                    aspect-square rounded-xl p-1 text-left flex flex-col items-center justify-center
                    transition-all duration-200 relative
                    ${active
                      ? "bg-accent text-white shadow-subtle"
                      : isToday
                        ? "bg-accent/10 text-accent"
                        : "hover:bg-surface-hover text-content"
                    }
                  `}
                >
                  <span className={`text-sm font-medium ${active ? "text-white" : ""}`}>
                    {dayNumber(cell)}
                  </span>
                  {hasData && !active && (
                    <span className="w-1 h-1 rounded-full bg-accent mt-0.5" />
                  )}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <div className="space-y-4">
        {/* Monthly overview */}
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold text-content">当月概览</h2>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-surface-secondary p-3">
                <p className="text-xs text-content-tertiary">总支出</p>
                <p className="text-lg font-bold text-content mt-1">¥{totalMonthSpend.toFixed(0)}</p>
              </div>
              <div className="rounded-xl bg-surface-secondary p-3">
                <p className="text-xs text-content-tertiary">日程数</p>
                <p className="text-lg font-bold text-content mt-1">{totalMonthSchedules}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Day detail */}
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold text-content">{selectedDate} 详情</h2>
          </CardHeader>
          <CardContent>
            {selected ? (
              <div className="space-y-4">
                <div>
                  <p className="text-xs font-semibold text-content-secondary mb-2">📝 账单</p>
                  {selected.ledgers.length === 0 ? (
                    <p className="text-xs text-content-tertiary">无账单</p>
                  ) : (
                    <div className="space-y-1.5">
                      {selected.ledgers.map((item) => (
                        <div key={item.id} className="flex justify-between text-xs">
                          <span className="text-content-secondary">{item.item}</span>
                          <span className="text-content font-medium">¥{item.amount.toFixed(2)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div>
                  <p className="text-xs font-semibold text-content-secondary mb-2">📅 日程</p>
                  {selected.schedules.length === 0 ? (
                    <p className="text-xs text-content-tertiary">无日程</p>
                  ) : (
                    <div className="space-y-1.5">
                      {selected.schedules.map((item) => (
                        <div key={item.id} className="flex justify-between text-xs">
                          <span className="text-content-secondary">{item.content}</span>
                          <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                            item.status === "done"
                              ? "bg-success/10 text-success"
                              : "bg-accent/10 text-accent"
                          }`}>
                            {item.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-xs text-content-tertiary text-center py-4">当天无记录</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
