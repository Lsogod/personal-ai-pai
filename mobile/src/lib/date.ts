type CalendarLikeDay = {
  date: string;
  ledger_total?: number;
  ledger_count?: number;
  schedule_count?: number;
};

export function pad2(value: number) {
  return `${value}`.padStart(2, "0");
}

export function parseServerDate(value: string) {
  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) return date;
  return new Date(String(value || "").replace(" ", "T"));
}

export function formatHmLocal(value: string) {
  const date = parseServerDate(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

export function formatMdHmLocal(value: string) {
  const date = parseServerDate(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${formatHmLocal(value)}`;
}

export function formatYmd(value: Date | string) {
  const date = value instanceof Date ? value : parseServerDate(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

export function formatDateLabel(value: string) {
  const date = parseServerDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

export function formatMonthLabel(value: Date) {
  return `${value.getFullYear()}年${value.getMonth() + 1}月`;
}

export function startOfMonth(value: Date) {
  return new Date(value.getFullYear(), value.getMonth(), 1);
}

export function addMonths(value: Date, months: number) {
  return new Date(value.getFullYear(), value.getMonth() + months, 1);
}

export function getMonthRange(value: Date) {
  const start = startOfMonth(value);
  const end = addMonths(start, 1);
  return {
    start,
    end,
    startText: formatYmd(start),
    endText: formatYmd(end),
  };
}

function getMondayFirstWeekday(value: Date) {
  return (value.getDay() + 6) % 7;
}

function isToday(value: Date) {
  const now = new Date();
  return (
    value.getFullYear() === now.getFullYear() &&
    value.getMonth() === now.getMonth() &&
    value.getDate() === now.getDate()
  );
}

export function buildCalendarGrid(days: CalendarLikeDay[], anchorDate: Date) {
  const map = new Map(days.map((day) => [day.date, day]));
  const start = startOfMonth(anchorDate);
  const offset = getMondayFirstWeekday(start);
  const daysInMonth = new Date(anchorDate.getFullYear(), anchorDate.getMonth() + 1, 0).getDate();
  const cells: Array<
    | { key: string; empty: true }
    | {
        key: string;
        empty: false;
        date: string;
        day: number;
        ledger_count: number;
        schedule_count: number;
        ledger_total: number;
        isToday: boolean;
      }
  > = [];

  for (let index = 0; index < offset; index += 1) {
    cells.push({ key: `empty-head-${index}`, empty: true });
  }

  for (let day = 1; day <= daysInMonth; day += 1) {
    const current = new Date(anchorDate.getFullYear(), anchorDate.getMonth(), day);
    const dateText = formatYmd(current);
    const detail = map.get(dateText);
    cells.push({
      key: dateText,
      empty: false,
      date: dateText,
      day,
      ledger_count: Number(detail?.ledger_count || 0),
      schedule_count: Number(detail?.schedule_count || 0),
      ledger_total: Number(detail?.ledger_total || 0),
      isToday: isToday(current),
    });
  }

  while (cells.length % 7 !== 0) {
    cells.push({ key: `empty-tail-${cells.length}`, empty: true });
  }

  return cells;
}

export function getScheduleStatusLabel(status: string) {
  if (status === "EXECUTED") return "已完成";
  if (status === "CANCELLED") return "已取消";
  return "待执行";
}
