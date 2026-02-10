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

/* Z‑suffix safe for WeChat JS engine */
function fmtSafe(iso) {
  return String(iso || "").replace(/-/g, "/").replace("T", " ").replace("Z", " +00:00");
}
function fmtTime(isoText) {
  if (!isoText) return "";
  const dt = new Date(fmtSafe(isoText));
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

/* ── Monthly Stats ── */
function buildMonthStats(days) {
  let totalSpend = 0, billCount = 0;
  let scheduleTotal = 0, scheduleDone = 0, schedulePending = 0;
  const dailySpend = [];

  for (const day of days) {
    let daySpend = 0;
    for (const l of (day.ledgers || [])) {
      daySpend += parseFloat(l.amount) || 0;
      billCount++;
    }
    totalSpend += daySpend;
    const d = Number(String(day.date || "").slice(-2));
    dailySpend.push({ day: d, value: Math.round(daySpend * 100) / 100 });

    for (const s of (day.schedules || [])) {
      scheduleTotal++;
      const st = String(s.status || "").toUpperCase();
      if (st === "EXECUTED") scheduleDone++;
      else if (st === "PENDING") schedulePending++;
    }
  }

  const doneRate = scheduleTotal > 0 ? Math.round(scheduleDone / scheduleTotal * 100) : 0;
  return {
    totalSpend: Math.round(totalSpend * 100) / 100,
    billCount,
    scheduleTotal,
    scheduleDone,
    schedulePending,
    doneRate,
    dailySpend,
  };
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
    activeDay: null,
    monthStats: { totalSpend: 0, billCount: 0, scheduleTotal: 0, scheduleDone: 0, schedulePending: 0, doneRate: 0, dailySpend: [] },
  },

  onLoad() {
    this._cursor = new Date();
    this._dpr = wx.getWindowInfo().pixelRatio || 2;
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
      const monthStats = buildMonthStats(days);
      this.setData({ monthLabel, days, calendarGrid, activeDate, activeDay, monthStats }, () => {
        this.drawSpendBar();
        this.drawRateRing();
      });
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
  },

  /* ── Daily spend bar chart ── */
  drawSpendBar() {
    this.createSelectorQuery().select("#spendBarCanvas").fields({ node: true, size: true }).exec(res => {
      if (!res || !res[0] || !res[0].node) return;
      const c = res[0].node, ctx = c.getContext("2d");
      const dpr = this._dpr, w = res[0].width, h = res[0].height;
      c.width = w * dpr; c.height = h * dpr; ctx.scale(dpr, dpr);
      this._drawBars(ctx, w, h);
    });
  },

  _drawBars(ctx, w, h) {
    const data = this.data.monthStats.dailySpend;
    ctx.clearRect(0, 0, w, h);
    if (!data.length) return;

    const pL = 34, pR = 8, pT = 10, pB = 22;
    const cW = w - pL - pR, cH = h - pT - pB;
    const vals = data.map(d => d.value);
    const mx = Math.max(...vals, 1);

    /* grid lines */
    ctx.strokeStyle = "#f0f0f5"; ctx.lineWidth = 0.5;
    for (let i = 0; i <= 3; i++) {
      const y = pT + cH / 3 * i;
      ctx.beginPath(); ctx.moveTo(pL, y); ctx.lineTo(w - pR, y); ctx.stroke();
    }
    /* Y labels */
    ctx.fillStyle = "#9ca3af"; ctx.font = "9px -apple-system,sans-serif";
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (let i = 0; i <= 3; i++) {
      const y = pT + cH / 3 * i;
      ctx.fillText(Math.round(mx * (1 - i / 3)) + "", pL - 4, y);
    }

    const gap = 2;
    const barW = Math.max((cW - gap * data.length) / data.length, 2);

    for (let i = 0; i < data.length; i++) {
      const x = pL + i * (barW + gap) + gap / 2;
      const bH = data[i].value / mx * cH;
      const y = pT + cH - bH;

      /* bar */
      const grd = ctx.createLinearGradient(x, y, x, pT + cH);
      grd.addColorStop(0, "#4F6EF7"); grd.addColorStop(1, "rgba(79,110,247,0.3)");
      ctx.fillStyle = grd;
      const r = Math.min(barW / 2, 3);
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.lineTo(x + barW - r, y);
      ctx.quadraticCurveTo(x + barW, y, x + barW, y + r);
      ctx.lineTo(x + barW, pT + cH);
      ctx.lineTo(x, pT + cH);
      ctx.lineTo(x, y + r);
      ctx.quadraticCurveTo(x, y, x + r, y);
      ctx.fill();

      /* X labels - show every 5 days */
      if (data[i].day % 5 === 1 || data[i].day === data.length) {
        ctx.fillStyle = "#9ca3af"; ctx.font = "8px -apple-system,sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "top";
        ctx.fillText(data[i].day + "", x + barW / 2, pT + cH + 5);
      }
    }
  },

  /* ── Schedule completion ring ── */
  drawRateRing() {
    this.createSelectorQuery().select("#rateRingCanvas").fields({ node: true, size: true }).exec(res => {
      if (!res || !res[0] || !res[0].node) return;
      const c = res[0].node, ctx = c.getContext("2d");
      const dpr = this._dpr, w = res[0].width, h = res[0].height;
      c.width = w * dpr; c.height = h * dpr; ctx.scale(dpr, dpr);
      this._drawRing(ctx, w, h);
    });
  },

  _drawRing(ctx, w, h) {
    const { doneRate, scheduleTotal } = this.data.monthStats;
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2, r = Math.min(cx, cy) - 6, lw = 10;

    /* background ring */
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = "#f0f0f5"; ctx.lineWidth = lw; ctx.lineCap = "round"; ctx.stroke();

    if (scheduleTotal === 0) {
      ctx.fillStyle = "#9ca3af"; ctx.font = "12px -apple-system,sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("暂无", cx, cy);
      return;
    }

    /* progress ring */
    const angle = (doneRate / 100) * Math.PI * 2;
    const grd = ctx.createConicGradient(-Math.PI / 2, cx, cy);
    grd.addColorStop(0, "#34D399"); grd.addColorStop(Math.min(doneRate / 100, 1), "#10B981");
    grd.addColorStop(1, "#34D399");

    ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + angle);
    ctx.strokeStyle = "#10B981"; ctx.lineWidth = lw; ctx.lineCap = "round"; ctx.stroke();

    /* center text */
    ctx.fillStyle = "#111827"; ctx.font = "bold 18px -apple-system,sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(doneRate + "%", cx, cy - 4);
    ctx.fillStyle = "#6b7280"; ctx.font = "10px -apple-system,sans-serif";
    ctx.fillText("完成率", cx, cy + 14);
  }
});
