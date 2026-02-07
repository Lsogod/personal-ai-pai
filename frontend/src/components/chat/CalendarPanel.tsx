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

function formatTimeValue(value: string): string {
  const raw = String(value || "").trim();
  if (!raw) return "--:--";
  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    // Fallback: keep useful substring, avoid "Invalid Date" on UI.
    return raw.length >= 16 ? raw.slice(11, 16) : raw;
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function CalendarPanel({ token }: CalendarPanelProps) {
  const [cursor, setCursor] = useState<Date>(() => firstDay(new Date()));
  const startDate = formatDate(firstDay(cursor));
  const endDate = formatDate(nextMonth(cursor));
  const [selectedDate, setSelectedDate] = useState<string>(formatDate(new Date()));

  const { data } = useQuery<CalendarResponse>({
    queryKey: ["calendar", startDate, endDate],
    queryFn: () => fetchCalendar(token, startDate, endDate),
    enabled: !!token,
    refetchInterval: token ? 15000 : false,
  });

  const dayMap = useMemo(() => {
    const map = new Map<string, CalendarDay>();
    (data?.days || []).forEach((day) => map.set(day.date, day));
    return map;
  }, [data]);

  const cells = useMemo(() => buildCalendarCells(cursor), [cursor]);
  const selected = dayMap.get(selectedDate);
  const today = formatDate(new Date());

  // Auto-select first day of month if cursor changes and selection is out of range
  useEffect(() => {
    const currentMonth = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}`;
    if (!selectedDate.startsWith(currentMonth)) {
      // Don't auto-select here to allow user to see text "No date selected" or keep previous selection logic
      // But for better UX, maybe select today if in current month, or 1st day
      if (currentMonth === formatDate(new Date()).slice(0, 7)) {
         setSelectedDate(formatDate(new Date()));
      } else {
         setSelectedDate(`${currentMonth}-01`);
      }
    }
  }, [cursor]);

  return (
    <div className="flex flex-col h-full space-y-4 p-1">
      {/* Calendar Header */}
      <div className="flex items-center justify-between px-2 py-1">
        <h2 className="text-sm font-semibold text-content">{monthTitle(cursor)}</h2>
        <div className="flex gap-1">
          <button
            onClick={() => setCursor((prev) => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))}
            className="p-1.5 rounded-lg text-content-secondary hover:text-content hover:bg-surface-hover transition-colors"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            onClick={() => setCursor((prev) => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))}
            className="p-1.5 rounded-lg text-content-secondary hover:text-content hover:bg-surface-hover transition-colors"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>

      {/* Calendar Grid */}
      <div className="bg-surface-card rounded-2xl border border-border p-3 shadow-sm">
        <div className="grid grid-cols-7 gap-1 mb-2">
          {["日", "一", "二", "三", "四", "五", "六"].map((name) => (
            <div key={name} className="text-center text-xs text-content-tertiary py-1">
              {name}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1 text-sm">
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
                onClick={() => setSelectedDate(cell)}
                className={`
                  aspect-square rounded-lg flex flex-col items-center justify-center relative transition-all
                  ${active 
                    ? "bg-content text-surface shadow-md scale-105 z-10" 
                    : isToday 
                      ? "bg-accent-subtle text-accent font-semibold"
                      : "text-content hover:bg-surface-hover"
                  }
                `}
              >
                <span>{dayNumber(cell)}</span>
                {/* Dots indicators */}
                <div className="flex gap-0.5 mt-0.5 h-1">
                  {day && day.ledger_count > 0 && (
                    <span className={`w-1 h-1 rounded-full ${active ? "bg-white/80" : "bg-warning/80"}`} title="有账单" />
                  )}
                  {day && day.schedule_count > 0 && (
                     <span className={`w-1 h-1 rounded-full ${active ? "bg-white/80" : "bg-success/80"}`} title="有日程" />
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Details Section */}
      <div className="flex-1 min-h-0 flex flex-col border-t border-border pt-4 mt-2">
        <h3 className="text-sm font-semibold text-content px-2 mb-3 flex items-center justify-between">
          <span>{selectedDate} 详情</span>
          {selected && (
             <span className="text-xs font-normal text-content-tertiary">
               支出 ¥{selected.ledger_total.toFixed(0)} · {selected.schedule_count} 日程
             </span>
          )}
        </h3>

        <div className="flex-1 overflow-y-auto space-y-4 px-2 custom-scrollbar">
          {!selected || (selected.ledgers.length === 0 && selected.schedules.length === 0) ? (
            <div className="flex flex-col items-center justify-center h-32 text-content-tertiary space-y-2">
              <Calendar size={24} className="opacity-20" />
              <p className="text-xs">暂无记录</p>
            </div>
          ) : (
            <>
              {/* Ledgers */}
              {selected.ledgers.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-content-secondary flex items-center gap-1">
                    <span className="w-1 h-3 rounded-full bg-warning/60"></span>
                    账单记录
                  </p>
                  <div className="space-y-2">
                    {selected.ledgers.map((item) => (
                      <div key={item.id} className="group flex items-center justify-between p-2.5 rounded-xl bg-surface-secondary/50 border border-border/50 hover:border-border transition-colors">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-content truncate">{item.item}</p>
                          {item.notes && <p className="text-xs text-content-tertiary truncate">{item.notes}</p>}
                        </div>
                        <span className="text-sm font-bold text-content font-mono">
                          -¥{item.amount.toFixed(2)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Schedules */}
              {selected.schedules.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-content-secondary flex items-center gap-1">
                    <span className="w-1 h-3 rounded-full bg-success/60"></span>
                    日程安排
                  </p>
                  <div className="space-y-2">
                    {selected.schedules.map((item) => (
                      <div key={item.id} className="group flex items-start gap-2.5 p-2.5 rounded-xl bg-surface-secondary/50 border border-border/50 hover:border-border transition-colors">
                         <div className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${item.status === 'done' ? 'bg-success' : 'bg-accent'}`} />
                         <div className="flex-1 min-w-0">
                           <p className={`text-sm text-content leading-relaxed ${item.status === 'done' ? 'line-through text-content-tertiary' : ''}`}>
                             {item.content}
                           </p>
                           <p className="text-xs text-content-tertiary mt-0.5">
                             {formatTimeValue(item.trigger_time)}
                           </p>
                         </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
