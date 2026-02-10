const { getToken } = require("../../utils/auth");
const { fetchCalendar, createLedger, updateLedger, deleteLedger, createSchedule, updateSchedule, deleteSchedule } = require("../../utils/http");

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

function fmtSafe(iso) {
  return String(iso || "").replace(/-/g, "/").replace("T", " ").replace("Z", " +00:00");
}
function fmtTime(isoText) {
  if (!isoText) return "";
  const dt = new Date(fmtSafe(isoText));
  if (Number.isNaN(dt.getTime())) return "";
  return `${dt.getHours()}`.padStart(2,"0") + ":" + `${dt.getMinutes()}`.padStart(2,"0");
}
function nowISO() {
  const d = new Date(), pad = n => (n+"").padStart(2,"0");
  return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate())+"T"+pad(d.getHours())+":"+pad(d.getMinutes());
}

const CATS = ["餐饮","交通","购物","居家","娱乐","医疗","教育","其他"];

function scheduleStatusLabel(value) {
  const key = String(value || "").toUpperCase();
  if (key === "EXECUTED") return "已完成";
  if (key === "PENDING") return "未完成";
  if (key === "CANCELLED") return "已取消";
  if (key === "FAILED") return "失败";
  return value || "未知";
}

function buildMonthStats(days) {
  let totalSpend = 0, billCount = 0;
  let scheduleTotal = 0, scheduleDone = 0, schedulePending = 0;
  const dailySpend = [];
  for (const day of days) {
    let daySpend = 0;
    for (const l of (day.ledgers || [])) { daySpend += parseFloat(l.amount) || 0; billCount++; }
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
  return {
    totalSpend: Math.round(totalSpend * 100) / 100, billCount,
    scheduleTotal, scheduleDone, schedulePending,
    doneRate: scheduleTotal > 0 ? Math.round(scheduleDone / scheduleTotal * 100) : 0,
    dailySpend,
  };
}

function buildCalendarGrid(days, year, month) {
  if (!Array.isArray(days) || days.length === 0) return [];
  const first = new Date(year, month - 1, 1);
  const lead = first.getDay();
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push({ key: `e-head-${i}`, empty: true });
  const today = toISODate(new Date());
  for (const day of days) {
    const d = Number(String(day.date || "").slice(-2));
    cells.push({
      key: `d-${day.date}`, empty: false, date: day.date,
      day: Number.isNaN(d) ? "" : String(d),
      ledger_count: day.ledger_count || 0,
      schedule_count: day.schedule_count || 0,
      isToday: day.date === today
    });
  }
  const tail = (7 - (cells.length % 7)) % 7;
  for (let i = 0; i < tail; i++) cells.push({ key: `e-tail-${i}`, empty: true });
  return cells;
}

Page({
  data: {
    authed: false, loading: false,
    monthLabel: "", days: [],
    weekHeaders: ["日","一","二","三","四","五","六"],
    calendarGrid: [], activeDate: "", activeDay: null,
    monthStats: { totalSpend:0,billCount:0,scheduleTotal:0,scheduleDone:0,schedulePending:0,doneRate:0,dailySpend:[] },
    cats: CATS,
    expandedId: null,
    // Ledger form
    showLedgerForm: false, ledgerFormMode: "add", ledgerFormId: null,
    lf_amount: "", lf_item: "", lf_category: "其他", lf_date: "",
    // Schedule form
    showScheduleForm: false, scheduleFormMode: "add", scheduleFormId: null,
    sf_content: "", sf_date: "", sf_time: "12:00",
  },

  onLoad() {
    this._cursor = new Date();
    this._dpr = wx.getWindowInfo().pixelRatio || 2;
  },

  onShow() {
    const authed = !!getToken();
    const year = this._cursor.getFullYear(), month = this._cursor.getMonth() + 1;
    this.setData({ authed, monthLabel: `${year}年${month}月` });
    if (!authed) { this.setData({ days: [], activeDate: "", activeDay: null }); return; }
    this.loadMonth();
  },

  async loadMonth() {
    if (!this.data.authed) return;
    this.setData({ loading: true });
    try {
      const range = monthRange(this._cursor);
      const data = await fetchCalendar(range.start, range.end);
      const year = this._cursor.getFullYear(), month = this._cursor.getMonth() + 1;
      const days = (Array.isArray(data.days) ? data.days : []).map((day) => {
        const ledgers = (day.ledgers || []).map(item => ({ ...item, _time: fmtTime(item.transaction_date) }));
        const schedules = (day.schedules || []).map(item => ({
          ...item, _time: fmtTime(item.trigger_time), _status_label: scheduleStatusLabel(item.status),
        }));
        return { ...day, ledgers, schedules,
          executed_count: schedules.filter(s => s.status === "EXECUTED").length,
          pending_count: schedules.filter(s => s.status === "PENDING").length };
      });
      const today = toISODate(new Date());
      const defaultDay = days.find(x => x.date === today) || days[0] || null;
      const calendarGrid = buildCalendarGrid(days, year, month);
      const monthStats = buildMonthStats(days);
      this.setData({
        monthLabel: `${year}年${month}月`, days, calendarGrid,
        activeDate: defaultDay ? defaultDay.date : "", activeDay: defaultDay,
        monthStats,
      }, () => { this.drawSpendBar(); this.drawRateRing(); });
    } catch (err) { wx.showToast({ title: err.message || "加载失败", icon: "none" }); }
    finally { this.setData({ loading: false }); }
  },

  onPrevMonth() {
    this._cursor = new Date(this._cursor.getFullYear(), this._cursor.getMonth() - 1, 1);
    const y = this._cursor.getFullYear(), m = this._cursor.getMonth() + 1;
    this.setData({ monthLabel: `${y}年${m}月` });
    if (this.data.authed) this.loadMonth();
  },
  onNextMonth() {
    this._cursor = new Date(this._cursor.getFullYear(), this._cursor.getMonth() + 1, 1);
    const y = this._cursor.getFullYear(), m = this._cursor.getMonth() + 1;
    this.setData({ monthLabel: `${y}年${m}月` });
    if (this.data.authed) this.loadMonth();
  },
  onPickDay(e) {
    const date = e.currentTarget.dataset.date;
    if (!date) return;
    this.setData({ activeDate: date, activeDay: this.data.days.find(d => d.date === date) || null, expandedId: null });
  },
  onToggleMore(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ expandedId: this.data.expandedId === id ? null : id });
  },

  /* ── Ledger CRUD ── */
  onShowAddLedger() {
    const base = this.data.activeDate || toISODate(new Date());
    this.setData({ showLedgerForm: true, ledgerFormMode: "add", ledgerFormId: null,
      lf_amount: "", lf_item: "", lf_category: "其他", lf_date: base + "T12:00" });
  },
  onShowEditLedger(e) {
    const item = e.currentTarget.dataset.item; if (!item) return;
    const raw = (item.transaction_date || "").replace("Z","").slice(0,16);
    this.setData({ expandedId: null, showLedgerForm: true, ledgerFormMode: "edit", ledgerFormId: item.id,
      lf_amount: String(item.amount||""), lf_item: item.item||"", lf_category: item.category||"其他", lf_date: raw });
  },
  onCloseLedgerForm() { this.setData({ showLedgerForm: false }, () => { setTimeout(() => { this.drawSpendBar(); this.drawRateRing(); }, 60); }); },
  onLfAmount(e) { this.setData({ lf_amount: e.detail.value }); },
  onLfItem(e) { this.setData({ lf_item: e.detail.value }); },
  onLfCat(e) { this.setData({ lf_category: CATS[e.detail.value] || "其他" }); },
  onLfDate(e) { const t = this.data.lf_date.slice(11,16)||"12:00"; this.setData({ lf_date: e.detail.value+"T"+t }); },
  onLfTime(e) { const d = this.data.lf_date.slice(0,10)||toISODate(new Date()); this.setData({ lf_date: d+"T"+e.detail.value }); },

  async onLedgerSubmit() {
    const amt = parseFloat(this.data.lf_amount);
    if (!amt || amt <= 0) { wx.showToast({ title: "请输入金额", icon: "none" }); return; }
    const payload = { amount: amt, item: this.data.lf_item||"手动记录", category: this.data.lf_category||"其他" };
    if (this.data.lf_date) payload.transaction_date = this.data.lf_date + ":00Z";
    try {
      if (this.data.ledgerFormMode === "add") { await createLedger(payload); wx.showToast({ title: "添加成功", icon: "success" }); }
      else { await updateLedger(this.data.ledgerFormId, payload); wx.showToast({ title: "修改成功", icon: "success" }); }
      this.setData({ showLedgerForm: false }); this.loadMonth();
    } catch (err) { wx.showToast({ title: err.message || "操作失败", icon: "none" }); }
  },
  onDeleteLedger(e) {
    const item = e.currentTarget.dataset.item; if (!item) return;
    this.setData({ expandedId: null });
    wx.showModal({ title: "确认删除", content: `删除「${item.item||"账单"}」¥${item.amount}？`, confirmColor: "#EF4444",
      success: async (res) => { if (!res.confirm) return;
        try { await deleteLedger(item.id); wx.showToast({ title: "已删除", icon: "success" }); this.loadMonth(); }
        catch (err) { wx.showToast({ title: err.message || "删除失败", icon: "none" }); }
      }
    });
  },

  /* ── Schedule CRUD ── */
  onShowAddSchedule() {
    const base = this.data.activeDate || toISODate(new Date());
    this.setData({ showScheduleForm: true, scheduleFormMode: "add", scheduleFormId: null,
      sf_content: "", sf_date: base, sf_time: "12:00" });
  },
  onShowEditSchedule(e) {
    const item = e.currentTarget.dataset.item; if (!item) return;
    const raw = (item.trigger_time || "").replace("Z","");
    this.setData({ expandedId: null, showScheduleForm: true, scheduleFormMode: "edit", scheduleFormId: item.id,
      sf_content: item.content||"", sf_date: raw.slice(0,10), sf_time: raw.slice(11,16)||"12:00" });
  },
  onCloseScheduleForm() { this.setData({ showScheduleForm: false }, () => { setTimeout(() => { this.drawSpendBar(); this.drawRateRing(); }, 60); }); },
  onSfContent(e) { this.setData({ sf_content: e.detail.value }); },
  onSfDate(e) { this.setData({ sf_date: e.detail.value }); },
  onSfTime(e) { this.setData({ sf_time: e.detail.value }); },

  async onScheduleSubmit() {
    if (!this.data.sf_content.trim()) { wx.showToast({ title: "请输入日程内容", icon: "none" }); return; }
    const trigger = this.data.sf_date + "T" + this.data.sf_time + ":00Z";
    const payload = { content: this.data.sf_content.trim(), trigger_time: trigger };
    try {
      if (this.data.scheduleFormMode === "add") { await createSchedule(payload); wx.showToast({ title: "添加成功", icon: "success" }); }
      else { await updateSchedule(this.data.scheduleFormId, payload); wx.showToast({ title: "修改成功", icon: "success" }); }
      this.setData({ showScheduleForm: false }); this.loadMonth();
    } catch (err) { wx.showToast({ title: err.message || "操作失败", icon: "none" }); }
  },
  onDeleteSchedule(e) {
    const item = e.currentTarget.dataset.item; if (!item) return;
    this.setData({ expandedId: null });
    wx.showModal({ title: "确认删除", content: `删除日程「${item.content||""}」？`, confirmColor: "#EF4444",
      success: async (res) => { if (!res.confirm) return;
        try { await deleteSchedule(item.id); wx.showToast({ title: "已删除", icon: "success" }); this.loadMonth(); }
        catch (err) { wx.showToast({ title: err.message || "删除失败", icon: "none" }); }
      }
    });
  },
  onToggleScheduleStatus(e) {
    const item = e.currentTarget.dataset.item; if (!item) return;
    const newStatus = item.status === "EXECUTED" ? "PENDING" : "EXECUTED";
    updateSchedule(item.id, { status: newStatus }).then(() => {
      wx.showToast({ title: newStatus === "EXECUTED" ? "已标记完成" : "已标记未完成", icon: "success" });
      this.loadMonth();
    }).catch(err => wx.showToast({ title: err.message || "操作失败", icon: "none" }));
  },

  onGoLogin() {
    wx.navigateTo({ url: `/pages/login/index?redirect=${encodeURIComponent("/pages/calendar/index")}` });
  },

  /* ── Charts ── */
  drawSpendBar() {
    this.createSelectorQuery().select("#spendBarCanvas").fields({ node: true, size: true }).exec(res => {
      if (!res || !res[0] || !res[0].node) return;
      const c = res[0].node, ctx = c.getContext("2d"), dpr = this._dpr, w = res[0].width, h = res[0].height;
      c.width = w * dpr; c.height = h * dpr; ctx.scale(dpr, dpr); this._drawBars(ctx, w, h);
    });
  },
  _drawBars(ctx, w, h) {
    const data = this.data.monthStats.dailySpend;
    ctx.clearRect(0, 0, w, h); if (!data.length) return;
    const pL=34,pR=8,pT=10,pB=22, cW=w-pL-pR, cH=h-pT-pB;
    const vals=data.map(d=>d.value), mx=Math.max(...vals,1);
    ctx.strokeStyle="#f0f0f5";ctx.lineWidth=0.5;
    for(let i=0;i<=3;i++){const y=pT+cH/3*i;ctx.beginPath();ctx.moveTo(pL,y);ctx.lineTo(w-pR,y);ctx.stroke();}
    ctx.fillStyle="#9ca3af";ctx.font="9px -apple-system,sans-serif";ctx.textAlign="right";ctx.textBaseline="middle";
    for(let i=0;i<=3;i++){const y=pT+cH/3*i;ctx.fillText(Math.round(mx*(1-i/3))+"",pL-4,y);}
    const gap=2, barW=Math.max((cW-gap*data.length)/data.length,2);
    for(let i=0;i<data.length;i++){
      const x=pL+i*(barW+gap)+gap/2, bH=data[i].value/mx*cH, y=pT+cH-bH;
      const grd=ctx.createLinearGradient(x,y,x,pT+cH);
      grd.addColorStop(0,"#4F6EF7");grd.addColorStop(1,"rgba(79,110,247,0.3)");
      ctx.fillStyle=grd;const r=Math.min(barW/2,3);
      ctx.beginPath();ctx.moveTo(x+r,y);ctx.lineTo(x+barW-r,y);ctx.quadraticCurveTo(x+barW,y,x+barW,y+r);
      ctx.lineTo(x+barW,pT+cH);ctx.lineTo(x,pT+cH);ctx.lineTo(x,y+r);ctx.quadraticCurveTo(x,y,x+r,y);ctx.fill();
      if(data[i].day%5===1||data[i].day===data.length){
        ctx.fillStyle="#9ca3af";ctx.font="8px -apple-system,sans-serif";ctx.textAlign="center";ctx.textBaseline="top";
        ctx.fillText(data[i].day+"",x+barW/2,pT+cH+5);
      }
    }
  },
  drawRateRing() {
    this.createSelectorQuery().select("#rateRingCanvas").fields({ node: true, size: true }).exec(res => {
      if (!res || !res[0] || !res[0].node) return;
      const c = res[0].node, ctx = c.getContext("2d"), dpr = this._dpr, w = res[0].width, h = res[0].height;
      c.width = w * dpr; c.height = h * dpr; ctx.scale(dpr, dpr); this._drawRing(ctx, w, h);
    });
  },
  _drawRing(ctx, w, h) {
    const { doneRate, scheduleTotal } = this.data.monthStats;
    ctx.clearRect(0, 0, w, h);
    const cx=w/2,cy=h/2,r=Math.min(cx,cy)-6,lw=10;
    ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.strokeStyle="#f0f0f5";ctx.lineWidth=lw;ctx.lineCap="round";ctx.stroke();
    if(scheduleTotal===0){ctx.fillStyle="#9ca3af";ctx.font="12px -apple-system,sans-serif";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("暂无",cx,cy);return;}
    const angle=(doneRate/100)*Math.PI*2;
    ctx.beginPath();ctx.arc(cx,cy,r,-Math.PI/2,-Math.PI/2+angle);ctx.strokeStyle="#10B981";ctx.lineWidth=lw;ctx.lineCap="round";ctx.stroke();
    ctx.fillStyle="#111827";ctx.font="bold 18px -apple-system,sans-serif";ctx.textAlign="center";ctx.textBaseline="middle";
    ctx.fillText(doneRate+"%",cx,cy-4);
    ctx.fillStyle="#6b7280";ctx.font="10px -apple-system,sans-serif";ctx.fillText("完成率",cx,cy+14);
  },
});
