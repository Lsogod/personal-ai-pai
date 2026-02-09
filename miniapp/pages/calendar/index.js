const { getToken } = require("../../utils/auth");
const { fetchCalendar } = require("../../utils/http");

function toISODate(dt) {
  const y = dt.getFullYear();
  const m = `${dt.getMonth() + 1}`.padStart(2, "0");
  const d = `${dt.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function monthRange(anchor) {
  const start = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const end = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 1);
  return { start: toISODate(start), end: toISODate(end) };
}

function fmtTime(isoText) {
  if (!isoText) return "";
  const dt = new Date(isoText);
  if (Number.isNaN(dt.getTime())) return "";
  const hh = `${dt.getHours()}`.padStart(2, "0");
  const mm = `${dt.getMinutes()}`.padStart(2, "0");
  return `${hh}:${mm}`;
}

function scheduleStatusLabel(value) {
  const key = String(value || "").toUpperCase();
  if (key === "EXECUTED") return "已完成";
  if (key === "PENDING") return "未完成";
  if (key === "CANCELLED") return "已取消";
  if (key === "FAILED") return "失败";
  return value || "未知";
}

function buildCalendarGrid(days, year, month) {
  if (!Array.isArray(days) || days.length === 0) return [];
  const first = new Date(year, month - 1, 1);
  const lead = first.getDay(); // 0=Sun
  const cells = [];

  for (let i = 0; i < lead; i += 1) {
    cells.push({ key: `e-head-${i}`, empty: true });
  }

  const today = toISODate(new Date());
  for (const day of days) {
    const d = Number(String(day.date || "").slice(-2));
    cells.push({
      key: `d-${day.date}`,
      empty: false,
      date: day.date,
      day: Number.isNaN(d) ? "" : String(d),
      ledger_count: day.ledger_count || 0,
      schedule_count: day.schedule_count || 0,
      isToday: day.date === today
    });
  }

  const tail = (7 - (cells.length % 7)) % 7;
  for (let i = 0; i < tail; i += 1) {
    cells.push({ key: `e-tail-${i}`, empty: true });
  }
  return cells;
}

Page({
  data: {
    authed: false,
    loading: false,
    monthLabel: "",
    days: [],
    weekHeaders: ["日", "一", "二", "三", "四", "五", "六"],
    calendarGrid: [],
    activeDate: "",
    activeDay: null
  },

  onLoad() {
    this._cursor = new Date();
  },

  onShow() {
    const token = getToken();
    const authed = !!token;
    const year = this._cursor.getFullYear();
    const month = this._cursor.getMonth() + 1;
    const monthLabel = `${year}年${month}月`;
    this.setData({ authed, monthLabel });
    if (!authed) {
      this.setData({ days: [], activeDate: "", activeDay: null });
      return;
    }
    this.loadMonth();
  },

  async loadMonth() {
    if (!this.data.authed) return;
    this.setData({ loading: true });
    try {
      const range = monthRange(this._cursor);
      const data = await fetchCalendar(range.start, range.end);
      const year = this._cursor.getFullYear();
      const month = this._cursor.getMonth() + 1;
      const monthLabel = `${year}年${month}月`;
      const days = (Array.isArray(data.days) ? data.days : []).map((day) => {
        const ledgers = (day.ledgers || []).map((item) => ({
          ...item,
          _time: fmtTime(item.transaction_date)
        }));
        const schedules = (day.schedules || []).map((item) => ({
          ...item,
          _time: fmtTime(item.trigger_time),
          _status_label: scheduleStatusLabel(item.status),
        }));
        return {
          ...day,
          ledgers,
          schedules,
          executed_count: schedules.filter((s) => s.status === "EXECUTED").length,
          pending_count: schedules.filter((s) => s.status === "PENDING").length
        };
      });
      const today = toISODate(new Date());
      const defaultDay = days.find((x) => x.date === today) || days[0] || null;
      const activeDate = defaultDay ? defaultDay.date : "";
      const activeDay = defaultDay || null;
      const calendarGrid = buildCalendarGrid(days, year, month);
      this.setData({ monthLabel, days, calendarGrid, activeDate, activeDay });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onPrevMonth() {
    this._cursor = new Date(this._cursor.getFullYear(), this._cursor.getMonth() - 1, 1);
    const year = this._cursor.getFullYear();
    const month = this._cursor.getMonth() + 1;
    const monthLabel = `${year}年${month}月`;
    this.setData({ monthLabel });
    if (this.data.authed) this.loadMonth();
  },

  onNextMonth() {
    this._cursor = new Date(this._cursor.getFullYear(), this._cursor.getMonth() + 1, 1);
    const year = this._cursor.getFullYear();
    const month = this._cursor.getMonth() + 1;
    const monthLabel = `${year}年${month}月`;
    this.setData({ monthLabel });
    if (this.data.authed) this.loadMonth();
  },

  onPickDay(e) {
    const date = e.currentTarget.dataset.date;
    if (!date) return;
    const day = this.data.days.find((item) => item.date === date) || null;
    this.setData({ activeDate: date, activeDay: day });
  },

  onGoLogin() {
    const redirect = encodeURIComponent("/pages/calendar/index");
    wx.navigateTo({ url: `/pages/login/index?redirect=${redirect}` });
  }
});
