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

Page({
  data: {
    loading: false,
    monthLabel: "",
    days: [],
    activeDate: "",
    activeDay: null
  },

  onLoad() {
    this._cursor = new Date();
  },

  onShow() {
    const token = getToken();
    if (!token) {
      wx.reLaunch({ url: "/pages/login/index" });
      return;
    }
    this.loadMonth();
  },

  async loadMonth() {
    this.setData({ loading: true });
    try {
      const range = monthRange(this._cursor);
      const data = await fetchCalendar(range.start, range.end);
      const monthLabel = `${this._cursor.getFullYear()}-${`${this._cursor.getMonth() + 1}`.padStart(2, "0")}`;
      const days = Array.isArray(data.days) ? data.days : [];
      const activeDate = days.length > 0 ? days[0].date : "";
      const activeDay = days.length > 0 ? days[0] : null;
      this.setData({ monthLabel, days, activeDate, activeDay });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onPrevMonth() {
    this._cursor = new Date(this._cursor.getFullYear(), this._cursor.getMonth() - 1, 1);
    this.loadMonth();
  },

  onNextMonth() {
    this._cursor = new Date(this._cursor.getFullYear(), this._cursor.getMonth() + 1, 1);
    this.loadMonth();
  },

  onPickDay(e) {
    const date = e.currentTarget.dataset.date;
    const day = this.data.days.find((item) => item.date === date) || null;
    this.setData({ activeDate: date, activeDay: day });
  }
});
