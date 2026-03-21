import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSchedule,
  deleteSchedule,
  fetchCalendar,
  updateSchedule,
  type CalendarDay,
  type CalendarResponse,
  type CalendarScheduleItem,
} from "../../lib/api";
import { Button } from "../ui/button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Input } from "../ui/input";
import { Calendar, ChevronLeft, ChevronRight, Pencil, Plus, Trash2 } from "../ui/icons";

interface CalendarPanelProps {
  token: string | null;
}

type ScheduleFormMode = "add" | "edit";

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

function parseDateTime(value: string): Date {
  const raw = String(value || "").trim();
  if (!raw) return new Date("");

  let dt = new Date(raw);
  if (!Number.isNaN(dt.getTime())) return dt;

  dt = new Date(raw.replace("T", " "));
  if (!Number.isNaN(dt.getTime())) return dt;

  return new Date(raw.replace(/-/g, "/").replace("T", " ").replace(/Z$/, ""));
}

function dayNumber(value: string): string {
  if (!value) return "";
  return String(Number(value.slice(-2)));
}

function formatTimeValue(value: string): string {
  const dt = parseDateTime(value);
  if (Number.isNaN(dt.getTime())) return "--:--";
  const hh = `${dt.getHours()}`.padStart(2, "0");
  const mm = `${dt.getMinutes()}`.padStart(2, "0");
  return `${hh}:${mm}`;
}

function toInputDateTimeParts(value: string): { date: string; time: string } {
  const dt = parseDateTime(value);
  if (Number.isNaN(dt.getTime())) {
    return { date: formatDate(new Date()), time: "12:00" };
  }
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, "0");
  const d = String(dt.getDate()).padStart(2, "0");
  const hh = String(dt.getHours()).padStart(2, "0");
  const mm = String(dt.getMinutes()).padStart(2, "0");
  return { date: `${y}-${m}-${d}`, time: `${hh}:${mm}` };
}

function statusLabel(status: string): string {
  const key = String(status || "").toUpperCase();
  if (key === "EXECUTED") return "已完成";
  if (key === "PENDING") return "待执行";
  if (key === "FAILED") return "失败";
  if (key === "CANCELLED") return "已取消";
  return key || "未知";
}

function statusClass(status: string): string {
  const key = String(status || "").toUpperCase();
  if (key === "EXECUTED") return "bg-success/10 text-success";
  if (key === "FAILED") return "bg-danger/10 text-danger";
  if (key === "CANCELLED") return "bg-surface-hover text-content-tertiary";
  return "bg-accent/10 text-accent";
}

export function CalendarPanel({ token }: CalendarPanelProps) {
  const queryClient = useQueryClient();
  const [cursor, setCursor] = useState<Date>(() => firstDay(new Date()));
  const startDate = formatDate(firstDay(cursor));
  const endDate = formatDate(nextMonth(cursor));
  const [selectedDate, setSelectedDate] = useState<string>(formatDate(new Date()));

  const [confirmDelete, setConfirmDelete] = useState<{ id: number } | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [formMode, setFormMode] = useState<ScheduleFormMode>("add");
  const [formId, setFormId] = useState<number | null>(null);
  const [formContent, setFormContent] = useState("");
  const [formDate, setFormDate] = useState(formatDate(new Date()));
  const [formTime, setFormTime] = useState("12:00");

  const { data } = useQuery<CalendarResponse>({
    queryKey: ["calendar", startDate, endDate],
    queryFn: () => fetchCalendar(token, startDate, endDate),
    enabled: !!token,
    refetchInterval: token ? 20000 : false,
  });

  const dayMap = useMemo(() => {
    const map = new Map<string, CalendarDay>();
    (data?.days || []).forEach((day) => map.set(day.date, day));
    return map;
  }, [data]);

  const cells = useMemo(() => buildCalendarCells(cursor), [cursor]);
  const selected = dayMap.get(selectedDate);
  const today = formatDate(new Date());

  useEffect(() => {
    const currentMonth = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}`;
    if (!selectedDate.startsWith(currentMonth)) {
      setSelectedDate(`${currentMonth}-01`);
    }
  }, [cursor, selectedDate]);

  const createMutation = useMutation({
    mutationFn: (payload: { content: string; trigger_time: string }) => createSchedule(payload, token),
    onSuccess: async () => {
      setShowForm(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["calendar"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] }),
      ]);
    },
  });

  const updateMutation = useMutation({
    mutationFn: (payload: { id: number; content?: string; trigger_time?: string; status?: string }) =>
      updateSchedule(payload.id, { content: payload.content, trigger_time: payload.trigger_time, status: payload.status }, token),
    onSuccess: async () => {
      setShowForm(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["calendar"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] }),
      ]);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteSchedule(id, token),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });

  function openCreate() {
    setFormMode("add");
    setFormId(null);
    setFormContent("");
    setFormDate(selectedDate || formatDate(new Date()));
    setFormTime("12:00");
    setShowForm(true);
  }

  function openEdit(item: CalendarScheduleItem) {
    const parts = toInputDateTimeParts(item.trigger_time);
    setFormMode("edit");
    setFormId(item.id);
    setFormContent(item.content || "");
    setFormDate(parts.date);
    setFormTime(parts.time);
    setShowForm(true);
  }

  function submitForm() {
    const content = formContent.trim();
    if (!content) return;
    const triggerTime = new Date(`${formDate}T${formTime}:00`).toISOString();
    if (formMode === "add") {
      createMutation.mutate({ content, trigger_time: triggerTime });
      return;
    }
    if (!formId) return;
    updateMutation.mutate({ id: formId, content, trigger_time: triggerTime });
  }

  function toggleStatus(item: CalendarScheduleItem) {
    const current = String(item.status || "").toUpperCase();
    const status = current === "EXECUTED" ? "PENDING" : "EXECUTED";
    updateMutation.mutate({ id: item.id, status });
  }

  return (
    <div className="flex flex-col h-full space-y-4 p-1">
      <div className="flex items-center justify-between px-2 py-1">
        <h2 className="text-sm font-semibold text-content">{monthTitle(cursor)}</h2>
        <div className="flex gap-1">
          <button
            onClick={() => setCursor((prev) => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))}
            className="p-1.5 rounded-lg text-content-secondary hover:text-content hover:bg-surface-hover transition-colors"
            aria-label="上一月"
          >
            <ChevronLeft size={16} />
          </button>
          <button
            onClick={() => setCursor((prev) => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))}
            className="p-1.5 rounded-lg text-content-secondary hover:text-content hover:bg-surface-hover transition-colors"
            aria-label="下一月"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>

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
            if (!cell) return <div key={`blank-${index}`} className="aspect-square" />;
            const day = dayMap.get(cell);
            const active = selectedDate === cell;
            const isToday = cell === today;
            return (
              <button
                key={cell}
                onClick={() => setSelectedDate(cell)}
                className={[
                  "aspect-square rounded-lg flex flex-col items-center justify-center relative transition-all",
                  active
                    ? "bg-content text-surface shadow-md scale-105 z-10"
                    : isToday
                      ? "bg-accent-subtle text-accent font-semibold"
                      : "text-content hover:bg-surface-hover",
                ].join(" ")}
              >
                <span>{dayNumber(cell)}</span>
                <div className="flex gap-0.5 mt-0.5 h-1">
                  {day && day.ledger_count > 0 && (
                    <span className={`w-1 h-1 rounded-full ${active ? "bg-white/80" : "bg-warning/80"}`} />
                  )}
                  {day && day.schedule_count > 0 && (
                    <span className={`w-1 h-1 rounded-full ${active ? "bg-white/80" : "bg-success/80"}`} />
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 min-h-0 flex flex-col border-t border-border pt-4 mt-2">
        <div className="px-2 mb-3 flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-content">
            {(() => {
              const [y, m, d] = selectedDate.split("-");
              return `${Number(y)}年${Number(m)}月${Number(d)}日`;
            })()} 详情
          </h3>
          <Button size="sm" onClick={openCreate}>
            <Plus size={14} />
            新增日程
          </Button>
        </div>

        {showForm && (
          <div className="mx-2 mb-3 rounded-xl border border-border bg-surface-secondary p-3 space-y-2">
            <Input
              placeholder="日程内容"
              value={formContent}
              onChange={(e) => setFormContent(e.target.value)}
            />
            <div className="grid grid-cols-2 gap-2">
              <Input type="date" value={formDate} onChange={(e) => setFormDate(e.target.value)} />
              <Input type="time" value={formTime} onChange={(e) => setFormTime(e.target.value)} />
            </div>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setShowForm(false)}>
                取消
              </Button>
              <Button
                size="sm"
                onClick={submitForm}
                disabled={createMutation.isPending || updateMutation.isPending || !formContent.trim()}
              >
                {formMode === "add" ? "添加" : "保存"}
              </Button>
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto space-y-4 px-2">
          {!selected || (selected.ledgers.length === 0 && selected.schedules.length === 0) ? (
            <div className="flex flex-col items-center justify-center h-32 text-content-tertiary space-y-2">
              <Calendar size={24} className="opacity-20" />
              <p className="text-xs">当天暂无记录</p>
            </div>
          ) : (
            <>
              {selected.ledgers.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-content-secondary flex items-center gap-1">
                    <span className="w-1 h-3 rounded-full bg-warning/60" />
                    账单记录
                  </p>
                  <div className="space-y-2">
                    {selected.ledgers.map((item) => (
                      <div
                        key={item.id}
                        className="group flex items-center justify-between p-2.5 rounded-xl bg-surface-secondary/50 border border-border/50"
                      >
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-content truncate">{item.item}</p>
                          <p className="text-xs text-content-tertiary truncate">{item.category}</p>
                        </div>
                        <span className="text-sm font-bold text-content font-mono">
                          -¥{item.amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selected.schedules.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-content-secondary flex items-center gap-1">
                    <span className="w-1 h-3 rounded-full bg-success/60" />
                    日程安排
                  </p>
                  <div className="space-y-2">
                    {selected.schedules.map((item) => (
                      <div
                        key={item.id}
                        className="group p-2.5 rounded-xl bg-surface-secondary/50 border border-border/50 space-y-2"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1 min-w-0">
                            <p className="text-sm text-content leading-relaxed break-words">{item.content}</p>
                            <p className="text-xs text-content-tertiary mt-0.5">
                              {formatTimeValue(item.trigger_time)}
                            </p>
                          </div>
                          <span className={`px-2 py-0.5 rounded-full text-[11px] ${statusClass(item.status)}`}>
                            {statusLabel(item.status)}
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button size="sm" variant="ghost" onClick={() => toggleStatus(item)}>
                            {String(item.status || "").toUpperCase() === "EXECUTED" ? "标记待执行" : "标记完成"}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => openEdit(item)}>
                            <Pencil size={13} />
                            编辑
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => setConfirmDelete({ id: item.id })}
                          >
                            <Trash2 size={13} />
                            删除
                          </Button>
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

      <ConfirmDialog
        open={!!confirmDelete}
        title="删除日程"
        message="确认删除这条日程吗？"
        variant="danger"
        confirmText="删除"
        onConfirm={() => {
          if (confirmDelete) deleteMutation.mutate(confirmDelete.id);
          setConfirmDelete(null);
        }}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
