const { getToken } = require("../../utils/auth");
const { fetchLedgers, createLedger, updateLedger, deleteLedger } = require("../../utils/http");
const LEDGER_PAGE_SIZE = 30;
const LEDGER_OVERVIEW_LIMIT = 200;

const DISPLAY_TZ_OFFSET_MINUTES = 8 * 60; // Asia/Shanghai
const CATS = ["餐饮", "交通", "购物", "居家", "娱乐", "医疗", "教育", "其他"];
const CAT_CLR = {
  "餐饮": "#4F6EF7",
  "交通": "#34D399",
  "购物": "#F59E0B",
  "居家": "#8B5CF6",
  "娱乐": "#EC4899",
  "医疗": "#EF4444",
  "教育": "#06B6D4",
  "其他": "#94A3B8",
};
const DEFAULT_CATEGORY = CATS[CATS.length - 1] || "Other";
const RANGE_KEYS = ["all", "today", "7d", "30d", "month"];
const SORT_KEYS = ["time_desc", "time_asc", "amount_desc", "amount_asc"];
const RANGE_LABEL = {
  all: "全部",
  today: "今天",
  "7d": "7天",
  "30d": "30天",
  month: "本月",
};
const SORT_LABEL = {
  time_desc: "最新优先",
  time_asc: "最早优先",
  amount_desc: "金额从高到低",
  amount_asc: "金额从低到高",
};
const STORAGE_INCOME_ENABLED = "ledger_income_enabled_v1";
const STORAGE_INCOME_VALUE = "ledger_income_value_v1";

function catClr(c) {
  return CAT_CLR[c] || "#94A3B8";
}

function parseDateTime(value) {
  const raw = String(value || "").trim();
  if (!raw) return new Date("");

  let dt = new Date(raw);
  if (!Number.isNaN(dt.getTime())) return dt;

  dt = new Date(raw.replace("T", " "));
  if (!Number.isNaN(dt.getTime())) return dt;

  dt = new Date(raw.replace(/-/g, "/").replace("T", " ").replace(/Z$/, ""));
  return dt;
}

function toDisplayDate(value) {
  const dt = parseDateTime(value);
  if (Number.isNaN(dt.getTime())) return null;
  return new Date(dt.getTime() + DISPLAY_TZ_OFFSET_MINUTES * 60 * 1000);
}

function getDisplayNow() {
  return new Date(Date.now() + DISPLAY_TZ_OFFSET_MINUTES * 60 * 1000);
}

function fmtDateTime(iso) {
  if (!iso) return "";
  const d = toDisplayDate(iso);
  if (!d) return "";
  const pad = (n) => `${n}`.padStart(2, "0");
  return `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function todayISO() {
  const d = new Date();
  const pad = (n) => `${n}`.padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function localDateTimeParts(value) {
  const raw = String(value || "").trim();
  const fallback = todayISO();
  const base = raw.includes("T") ? raw : fallback;
  const date = base.slice(0, 10) || fallback.slice(0, 10);
  const time = base.slice(11, 16) || fallback.slice(11, 16);
  return { date, time, value: `${date}T${time}` };
}

function pickerPartsFromIso(value) {
  const dt = toDisplayDate(value);
  if (!dt || Number.isNaN(dt.getTime())) return localDateTimeParts(todayISO());
  const pad = (n) => `${n}`.padStart(2, "0");
  const date = `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`;
  const time = `${pad(dt.getUTCHours())}:${pad(dt.getUTCMinutes())}`;
  return { date, time, value: `${date}T${time}` };
}

function dayStartMs(dt) {
  return Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), dt.getUTCDate(), 0, 0, 0, 0);
}

function monthStartMs(dt) {
  return Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), 1, 0, 0, 0, 0);
}

function monthEndMs(dt) {
  return Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth() + 1, 1, 0, 0, 0, 0);
}

function dayKey(dt) {
  const y = dt.getUTCFullYear();
  const m = `${dt.getUTCMonth() + 1}`.padStart(2, "0");
  const d = `${dt.getUTCDate()}`.padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function dayLabel(dt) {
  const now = getDisplayNow();
  const diff = Math.floor((dayStartMs(now) - dayStartMs(dt)) / 86400000);
  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  if (diff === 2) return "前天";
  return `${`${dt.getUTCMonth() + 1}`.padStart(2, "0")}-${`${dt.getUTCDate()}`.padStart(2, "0")}`;
}

function toNumber(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

function normalizeCategory(value) {
  const s = String(value || "").trim();
  return s || DEFAULT_CATEGORY;
}

function rowDisplayMs(row) {
  if (row && Number.isFinite(row._displayMs)) return row._displayMs;
  const dt = toDisplayDate(row && row.transaction_date);
  return dt ? dt.getTime() : NaN;
}

function buildCategoryOptions(rows) {
  const count = {};
  rows.forEach((r) => {
    const c = normalizeCategory(r.category);
    count[c] = (count[c] || 0) + 1;
  });
  return Object.keys(count).sort((a, b) => {
    const diff = (count[b] || 0) - (count[a] || 0);
    if (diff !== 0) return diff;
    return String(a).localeCompare(String(b));
  });
}

function buildCatStats(rows) {
  const m = {};
  let total = 0;
  for (const r of rows) {
    const c = normalizeCategory(r.category);
    const a = parseFloat(r.amount) || 0;
    m[c] = (m[c] || 0) + a;
    total += a;
  }
  const entries = Object.entries(m)
    .sort((a, b) => b[1] - a[1])
    .map(([name, value]) => ({
      name,
      value: Math.round(value * 100) / 100,
      percent: total > 0 ? Math.round((value / total) * 1000) / 10 : 0,
      color: catClr(name),
    }));
  return { total: Math.round(total * 100) / 100, entries };
}

function buildDailyTrend(rows) {
  const now = getDisplayNow();
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth();
  const today = now.getUTCDate();
  const daily = {};

  for (const r of rows) {
    const dt = toDisplayDate(r.transaction_date);
    if (!dt || Number.isNaN(dt.getTime())) continue;
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m) continue;
    const d = dt.getUTCDate();
    daily[d] = (daily[d] || 0) + (parseFloat(r.amount) || 0);
  }

  const days = [];
  for (let d = 1; d <= today; d++) {
    days.push({
      label: `${m + 1}/${d}`,
      value: Math.round((daily[d] || 0) * 100) / 100,
    });
  }
  return days;
}

function sanitizeIncomeInput(raw) {
  const s = String(raw || "");
  let out = "";
  let dotSeen = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch >= "0" && ch <= "9") {
      out += ch;
      continue;
    }
    if (ch === "." && !dotSeen) {
      dotSeen = true;
      out += ".";
    }
  }
  return out;
}

function decorateLedgerRows(rows) {
  return (Array.isArray(rows) ? rows : []).map((r) => {
    const category = normalizeCategory(r.category);
    const dt = toDisplayDate(r.transaction_date);
    return {
      ...r,
      category,
      _time: fmtDateTime(r.transaction_date),
      _catClr: catClr(category),
      _displayMs: dt ? dt.getTime() : NaN,
    };
  });
}

function mergeLedgerRows(existingRows, incomingRows) {
  const idSet = new Set();
  const merged = [];
  for (const row of existingRows || []) {
    const id = Number(row && row.id);
    if (id && !idSet.has(id)) {
      idSet.add(id);
      merged.push(row);
    }
  }
  for (const row of incomingRows || []) {
    const id = Number(row && row.id);
    if (id && !idSet.has(id)) {
      idSet.add(id);
      merged.push(row);
    }
  }
  return merged;
}

function buildCategoryState(rows, currentFormCategory, currentFilterCategory) {
  const filterCategories = buildCategoryOptions(rows);
  const formCategories = Array.from(
    new Set([...(Array.isArray(CATS) ? CATS : []), ...filterCategories])
  );
  const formCategory = formCategories.includes(currentFormCategory)
    ? currentFormCategory
    : (formCategories[0] || DEFAULT_CATEGORY);
  const filterCategory =
    currentFilterCategory === "all" || filterCategories.includes(currentFilterCategory)
      ? currentFilterCategory
      : "all";
  return {
    filterCategories,
    formCategories,
    formCategory,
    filterCategory,
  };
}

Page({
  data: {
    authed: false,
    loading: false,
    stats: { total: 0, count: 0 },
    ledgers: [],
    pageSize: LEDGER_PAGE_SIZE,
    nextBeforeId: null,
    hasMore: true,
    loadingMore: false,
    listGroups: [],
    filteredCount: 0,
    catStats: { total: 0, entries: [] },
    dailyTrend: [],
    avgDaily: 0,
    maxDay: { label: "-", value: 0 },
    activeTab: "overview",
    formCategories: CATS,
    filterCategories: [],
    filterKeyword: "",
    filterCategory: "all",
    filterRange: "all",
    sortMode: "time_desc",
    filterSummary: "分类:全部 · 时间:全部 · 排序:最新优先",
    filtersExpanded: false,
    incomeFeatureEnabled: false,
    monthlyIncomeInput: "",
    monthlyIncomeValue: 0,
    monthlySurplus: 0,
    popMenu: { show: false, top: 0, right: 0 },
    showForm: false,
    formMode: "add",
    formId: null,
    formAmount: "",
    formItem: "",
    formCategory: DEFAULT_CATEGORY,
    formCustomCategory: "",
    formDate: "",
    formDatePart: "",
    formTimePart: todayISO().slice(11, 16),
  },

  onLoad() {
    this._dpr = wx.getWindowInfo().pixelRatio || 2;
    this.restoreIncomeSettings();
  },

  onShow() {
    const authed = !!getToken();
    if (!authed) {
      this.setData({
        authed: false,
        stats: { total: 0, count: 0 },
        ledgers: [],
        nextBeforeId: null,
        hasMore: true,
        loadingMore: false,
        listGroups: [],
        filteredCount: 0,
        formCategories: CATS,
        filterCategories: [],
        catStats: { total: 0, entries: [] },
        dailyTrend: [],
      });
      return;
    }
    this.setData({ authed: true });
    this.loadData();
  },

  restoreIncomeSettings() {
    let enabled = false;
    try {
      enabled = !!wx.getStorageSync(STORAGE_INCOME_ENABLED);
    } catch (e) {
      enabled = false;
    }

    let raw = "";
    try {
      const stored = wx.getStorageSync(STORAGE_INCOME_VALUE);
      raw = stored === undefined || stored === null ? "" : String(stored);
    } catch (e) {
      raw = "";
    }

    const input = sanitizeIncomeInput(raw);
    const value = toNumber(input);
    const monthlySurplus = Math.round((value - toNumber(this.data.stats.total)) * 100) / 100;
    this.setData({
      incomeFeatureEnabled: enabled,
      monthlyIncomeInput: input,
      monthlyIncomeValue: value,
      monthlySurplus,
    });
  },

  updateMonthlySurplus(expenseTotal) {
    const total = typeof expenseTotal === "number" ? expenseTotal : toNumber(this.data.stats.total);
    const monthlySurplus = Math.round((toNumber(this.data.monthlyIncomeValue) - total) * 100) / 100;
    this.setData({ monthlySurplus });
  },

  updateFilterSummary() {
    const cat = this.data.filterCategory === "all" ? "全部" : this.data.filterCategory;
    const range = RANGE_LABEL[this.data.filterRange] || "全部";
    const sort = SORT_LABEL[this.data.sortMode] || "最新优先";
    this.setData({ filterSummary: `分类:${cat} · 时间:${range} · 排序:${sort}` });
  },

  async loadData() {
    this.setData({ loading: true });
    try {
      const ledgers = await fetchLedgers(LEDGER_OVERVIEW_LIMIT);
      const rows = (Array.isArray(ledgers) ? ledgers : []).map((r) => {
        const category = normalizeCategory(r.category);
        const dt = toDisplayDate(r.transaction_date);
        return {
          ...r,
          category,
          _time: fmtDateTime(r.transaction_date),
          _catClr: catClr(category),
          _displayMs: dt ? dt.getTime() : NaN,
        };
      });

      const now = getDisplayNow();
      const monthStart = monthStartMs(now);
      const monthEnd = monthEndMs(now);
      const monthRows = rows.filter((r) => {
        const ms = rowDisplayMs(r);
        return Number.isFinite(ms) && ms >= monthStart && ms < monthEnd;
      });

      const monthTotal = Math.round(
        monthRows.reduce((sum, r) => sum + toNumber(r.amount), 0) * 100
      ) / 100;
      const stats = { total: monthTotal, count: monthRows.length };
      const catStats = buildCatStats(monthRows);
      const dailyTrend = buildDailyTrend(monthRows);
      const totalTrend = dailyTrend.reduce((sum, d) => sum + toNumber(d.value), 0);
      const avgDaily = dailyTrend.length
        ? Math.round((totalTrend / dailyTrend.length) * 100) / 100
        : 0;
      const maxDay = dailyTrend.reduce((a, b) => (b.value > a.value ? b : a), {
        label: "-",
        value: 0,
      });

      const seedRows = rows.slice(0, this.data.pageSize);
      const categoryState = buildCategoryState(
        seedRows,
        this.data.formCategory,
        this.data.filterCategory
      );
      const tail = seedRows.length > 0 ? seedRows[seedRows.length - 1] : null;
      const nextBeforeId = tail && Number.isFinite(Number(tail.id)) ? Number(tail.id) : null;
      const hasMore = seedRows.length >= this.data.pageSize;

      this.setData(
        {
          stats,
          ledgers: seedRows,
          nextBeforeId,
          hasMore,
          loadingMore: false,
          catStats,
          dailyTrend,
          avgDaily,
          maxDay,
          ...categoryState,
        },
        () => {
          this.updateMonthlySurplus(stats.total);
          this.applyListFilters();
          if (this.data.activeTab === "overview") {
            setTimeout(() => {
              this.drawPie();
              this.drawTrend();
            }, 100);
          }
        }
      );
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyListFilters() {
    let rows = Array.isArray(this.data.ledgers) ? [...this.data.ledgers] : [];
    const kw = String(this.data.filterKeyword || "").trim().toLowerCase();
    const cat = this.data.filterCategory;
    const range = this.data.filterRange;
    const sort = this.data.sortMode;

    if (kw) {
      rows = rows.filter((r) => {
        const hay = `${r.item || ""} ${r.category || ""} ${r.amount || ""}`.toLowerCase();
        return hay.includes(kw);
      });
    }
    if (cat && cat !== "all") {
      rows = rows.filter((r) => String(r.category || "").trim() === cat);
    }

    if (range !== "all") {
      const now = getDisplayNow();
      const todayStart = dayStartMs(now);
      const todayEnd = todayStart + 86400000;
      const monthStart = monthStartMs(now);
      const monthEnd = monthEndMs(now);
      let start = todayStart;
      if (range === "7d") start = todayStart - 6 * 86400000;
      if (range === "30d") start = todayStart - 29 * 86400000;

      rows = rows.filter((r) => {
        const ms = rowDisplayMs(r);
        if (!Number.isFinite(ms)) return false;
        if (range === "today") return ms >= todayStart && ms < todayEnd;
        if (range === "month") return ms >= monthStart && ms < monthEnd;
        return ms >= start && ms < todayEnd;
      });
    }

    rows.sort((a, b) => {
      const ta = rowDisplayMs(a);
      const tb = rowDisplayMs(b);
      const aa = toNumber(a.amount);
      const ab = toNumber(b.amount);
      if (sort === "time_asc") return ta - tb;
      if (sort === "amount_desc") return ab - aa || tb - ta;
      if (sort === "amount_asc") return aa - ab || tb - ta;
      return tb - ta;
    });

    const groups = [];
    const gmap = {};
    rows.forEach((row) => {
      const ms = rowDisplayMs(row);
      const dt = Number.isFinite(ms) ? new Date(ms) : null;
      const key = dt ? dayKey(dt) : "unknown";
      if (!gmap[key]) {
        const g = { key, label: dt ? dayLabel(dt) : "未知日期", total: 0, count: 0, rows: [] };
        gmap[key] = g;
        groups.push(g);
      }
      gmap[key].rows.push(row);
      gmap[key].count += 1;
      gmap[key].total = Math.round((gmap[key].total + toNumber(row.amount)) * 100) / 100;
    });

    this.setData({ listGroups: groups, filteredCount: rows.length }, () => {
      this.updateFilterSummary();
    });
  },

  async loadMoreLedgers() {
    if (this.data.loadingMore || !this.data.hasMore) return;
    const beforeId = this.data.nextBeforeId;
    if (!beforeId) {
      this.setData({ hasMore: false });
      return;
    }

    this.setData({ loadingMore: true });
    try {
      const incoming = decorateLedgerRows(await fetchLedgers(this.data.pageSize, beforeId));
      const mergedRows = mergeLedgerRows(this.data.ledgers || [], incoming);
      const categoryState = buildCategoryState(
        mergedRows,
        this.data.formCategory,
        this.data.filterCategory
      );
      const tail = mergedRows.length > 0 ? mergedRows[mergedRows.length - 1] : null;
      const nextBeforeId = tail && Number.isFinite(Number(tail.id)) ? Number(tail.id) : null;
      const hasMore = incoming.length >= this.data.pageSize;

      this.setData(
        {
          ledgers: mergedRows,
          nextBeforeId,
          hasMore,
          ...categoryState,
        },
        () => this.applyListFilters()
      );
    } catch (err) {
      wx.showToast({ title: err.message || "鍔犺浇澶辫触", icon: "none" });
    } finally {
      this.setData({ loadingMore: false });
    }
  },

  onListScrollToLower() {
    this.loadMoreLedgers();
  },

  findListItemById(id) {
    const target = Number(id);
    if (!target) return null;
    for (const group of this.data.listGroups || []) {
      for (const row of group.rows || []) {
        if (Number(row.id) === target) return row;
      }
    }
    return null;
  },

  onToggleFilters() {
    this.setData({ filtersExpanded: !this.data.filtersExpanded });
  },

  onFilterKeyword(e) {
    this.setData({ filterKeyword: e.detail.value || "" }, () => this.applyListFilters());
  },

  onFilterCategory(e) {
    const next = String(e.currentTarget.dataset.category || "all");
    this.setData({ filterCategory: next }, () => this.applyListFilters());
  },

  onFilterRange(e) {
    const next = String(e.currentTarget.dataset.range || "all");
    if (!RANGE_KEYS.includes(next)) return;
    this.setData({ filterRange: next }, () => this.applyListFilters());
  },

  onFilterSort(e) {
    const next = String(e.currentTarget.dataset.sort || "time_desc");
    if (!SORT_KEYS.includes(next)) return;
    this.setData({ sortMode: next }, () => this.applyListFilters());
  },

  onResetFilters() {
    this.setData(
      {
        filterKeyword: "",
        filterCategory: "all",
        filterRange: "all",
        sortMode: "time_desc",
      },
      () => this.applyListFilters()
    );
  },

  onIncomeToggle(e) {
    const enabled = !!e.detail.value;
    this.setData({ incomeFeatureEnabled: enabled }, () => {
      try {
        wx.setStorageSync(STORAGE_INCOME_ENABLED, enabled);
      } catch (err) {}
      this.updateMonthlySurplus();
    });
  },

  onIncomeInput(e) {
    const cleaned = sanitizeIncomeInput(e.detail.value || "");
    const value = toNumber(cleaned);
    this.setData(
      {
        monthlyIncomeInput: cleaned,
        monthlyIncomeValue: value,
      },
      () => {
        try {
          wx.setStorageSync(STORAGE_INCOME_VALUE, cleaned);
        } catch (err) {}
        this.updateMonthlySurplus();
      }
    );
  },

  onShowMore(e) {
    const id = e.currentTarget.dataset.id;
    this._popItem = this.findListItemById(id);
    if (!this._popItem) return;
    const sysInfo = wx.getWindowInfo();
    this.createSelectorQuery()
      .select(`#more-${id}`)
      .boundingClientRect((rect) => {
        if (!rect) return;
        const right = sysInfo.windowWidth - rect.right;
        this.setData({ popMenu: { show: true, top: rect.bottom + 2, right } });
      })
      .exec();
  },

  onCloseMore() {
    this.setData({ "popMenu.show": false });
  },

  onPopEdit() {
    this.setData({ "popMenu.show": false });
    if (this._popItem) this.doEdit(this._popItem);
  },

  onPopDelete() {
    this.setData({ "popMenu.show": false });
    if (this._popItem) this.doDelete(this._popItem);
  },

  onSwitchTab(e) {
    const tab = e.currentTarget.dataset.tab;
    this.setData({ activeTab: tab }, () => {
      if (tab === "overview") {
        setTimeout(() => {
          this.drawPie();
          this.drawTrend();
        }, 60);
      }
      if (tab === "list") this.applyListFilters();
    });
  },

  onShowAdd() {
    const now = localDateTimeParts(todayISO());
    const formCategory = (this.data.formCategories || []).includes(DEFAULT_CATEGORY)
      ? DEFAULT_CATEGORY
      : ((this.data.formCategories || [])[0] || DEFAULT_CATEGORY);
    this.setData({
      showForm: true,
      formMode: "add",
      formId: null,
      formAmount: "",
      formItem: "",
      formCategory,
      formCustomCategory: "",
      formDate: now.value,
      formDatePart: now.date,
      formTimePart: now.time,
    });
  },

  doEdit(item) {
    if (!item) return;
    const picked = pickerPartsFromIso(item.transaction_date);
    const category = normalizeCategory(item.category);
    const formCategories = (this.data.formCategories || []).includes(category)
      ? (this.data.formCategories || [])
      : ([...(this.data.formCategories || []), category]);
    this.setData({
      showForm: true,
      formMode: "edit",
      formId: item.id,
      formAmount: String(item.amount || ""),
      formItem: item.item || "",
      formCategory: category,
      formCustomCategory: "",
      formCategories,
      formDate: picked.value,
      formDatePart: picked.date,
      formTimePart: picked.time,
    });
  },

  onModalInnerTap() {},

  onCloseForm() {
    wx.hideKeyboard({ complete: () => {} });
    this.setData({ showForm: false }, () => {
      if (this.data.activeTab === "overview") {
        setTimeout(() => {
          this.drawPie();
          this.drawTrend();
        }, 60);
      }
    });
  },

  onFormAmount(e) {
    this.setData({ formAmount: e.detail.value });
  },

  onFormItem(e) {
    this.setData({ formItem: e.detail.value });
  },

  onFormCat(e) {
    const cats = this.data.formCategories || [];
    this.setData({ formCategory: cats[e.detail.value] || this.data.formCategory || DEFAULT_CATEGORY });
  },

  onFormCustomCategory(e) {
    this.setData({ formCustomCategory: e.detail.value || "" });
  },

  onFormDate(e) {
    const date = e.detail.value || localDateTimeParts(todayISO()).date;
    const time = this.data.formTimePart || localDateTimeParts(todayISO()).time;
    this.setData({ formDatePart: date, formTimePart: time, formDate: `${date}T${time}` });
  },

  onFormTime(e) {
    const date = this.data.formDatePart || localDateTimeParts(todayISO()).date;
    const time = e.detail.value || localDateTimeParts(todayISO()).time;
    this.setData({ formDatePart: date, formTimePart: time, formDate: `${date}T${time}` });
  },

  async onFormSubmit() {
    const amt = parseFloat(this.data.formAmount);
    if (!amt || amt <= 0) {
      wx.showToast({ title: "请输入金额", icon: "none" });
      return;
    }
    const customCategory = String(this.data.formCustomCategory || "").trim();
    const finalCategory = customCategory || this.data.formCategory || DEFAULT_CATEGORY;
    const data = {
      amount: amt,
      item: this.data.formItem || "手动记录",
      category: finalCategory,
    };
    const date = this.data.formDatePart || localDateTimeParts(todayISO()).date;
    const time = this.data.formTimePart || localDateTimeParts(todayISO()).time;
    data.transaction_date = `${date}T${time}:00`;

    try {
      if (this.data.formMode === "add") {
        await createLedger(data);
        wx.showToast({ title: "添加成功", icon: "success" });
      } else {
        await updateLedger(this.data.formId, data);
        wx.showToast({ title: "修改成功", icon: "success" });
      }
      wx.hideKeyboard({ complete: () => {} });
      this.setData({ showForm: false });
      this.loadData();
    } catch (err) {
      wx.showToast({ title: err.message || "操作失败", icon: "none" });
    }
  },

  doDelete(item) {
    if (!item) return;
    wx.showModal({
      title: "确认删除",
      content: `删除「${item.item || "账单"}」¥${item.amount}？`,
      confirmColor: "#EF4444",
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await deleteLedger(item.id);
          wx.showToast({ title: "已删除", icon: "success" });
          this.loadData();
        } catch (err) {
          wx.showToast({ title: err.message || "删除失败", icon: "none" });
        }
      },
    });
  },

  drawPie() {
    this.createSelectorQuery()
      .select("#pieCanvas")
      .fields({ node: true, rect: true })
      .exec((res) => {
        if (!res || !res[0] || !res[0].node) return;
        const c = res[0].node;
        const ctx = c.getContext("2d");
        const dpr = this._dpr;
        const w = res[0].width || 120;
        const h = res[0].height || 120;
        c.width = w * dpr;
        c.height = h * dpr;
        ctx.scale(dpr, dpr);
        this._pie(ctx, w, h);
      });
  },

  _pie(ctx, w, h) {
    const { entries, total } = this.data.catStats;
    const cx = w / 2;
    const cy = h / 2;
    const r = Math.min(cx, cy) - 6;
    const ir = r * 0.58;
    ctx.clearRect(0, 0, w, h);

    if (!entries.length) {
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.strokeStyle = "#e5e7eb";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = "#9ca3af";
      ctx.font = "13px -apple-system,sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("暂无数据", cx, cy);
      return;
    }

    let a = -Math.PI / 2;
    for (const e of entries) {
      const sw = (e.value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, a, a + sw);
      ctx.closePath();
      ctx.fillStyle = e.color;
      ctx.fill();
      a += sw;
    }
    ctx.beginPath();
    ctx.arc(cx, cy, ir, 0, Math.PI * 2);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.fillStyle = "#111827";
    ctx.font = "bold 17px -apple-system,sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`¥${total}`, cx, cy - 6);
    ctx.fillStyle = "#6b7280";
    ctx.font = "11px -apple-system,sans-serif";
    ctx.fillText("总支出", cx, cy + 12);
  },

  drawTrend() {
    this.createSelectorQuery()
      .select("#trendCanvas")
      .fields({ node: true, rect: true })
      .exec((res) => {
        if (!res || !res[0] || !res[0].node) return;
        const c = res[0].node;
        const ctx = c.getContext("2d");
        const dpr = this._dpr;
        const w = res[0].width || 300;
        const h = res[0].height || 160;
        c.width = w * dpr;
        c.height = h * dpr;
        ctx.scale(dpr, dpr);
        this._trend(ctx, w, h);
      });
  },

  _trend(ctx, w, h) {
    const data = this.data.dailyTrend;
    const pL = 34;
    const pR = 10;
    const pT = 14;
    const pB = 28;
    const cW = w - pL - pR;
    const cH = h - pT - pB;
    ctx.clearRect(0, 0, w, h);

    const vals = data.map((d) => d.value);
    const mx = Math.max(...vals, 1);
    ctx.strokeStyle = "#f0f0f5";
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const y = pT + (cH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(pL, y);
      ctx.lineTo(w - pR, y);
      ctx.stroke();
    }
    ctx.fillStyle = "#9ca3af";
    ctx.font = "9px -apple-system,sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i++) {
      const y = pT + (cH / 4) * i;
      ctx.fillText(`${Math.round(mx * (1 - i / 4))}`, pL - 5, y);
    }

    if (data.length < 2) return;
    const sx = cW / (data.length - 1);
    const pts = data.map((d, i) => ({ x: pL + i * sx, y: pT + cH - (d.value / mx) * cH }));
    const grd = ctx.createLinearGradient(0, pT, 0, pT + cH);
    grd.addColorStop(0, "rgba(79,110,247,0.15)");
    grd.addColorStop(1, "rgba(79,110,247,0.01)");
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pT + cH);
    for (const p of pts) ctx.lineTo(p.x, p.y);
    ctx.lineTo(pts[pts.length - 1].x, pT + cH);
    ctx.closePath();
    ctx.fillStyle = grd;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.strokeStyle = "#4F6EF7";
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.stroke();
    for (const p of pts) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = "#4F6EF7";
      ctx.fill();
    }
    ctx.fillStyle = "#9ca3af";
    ctx.font = "9px -apple-system,sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let i = 0; i < data.length; i++) {
      if (i % 4 === 0 || i === data.length - 1) {
        ctx.fillText(data[i].label, pts[i].x, pT + cH + 6);
      }
    }
  },

  onGoLogin() {
    wx.navigateTo({
      url: `/pages/login/index?redirect=${encodeURIComponent("/pages/ledger/index")}`,
    });
  },

  onShareAppMessage() {
    return {
      title: "效率工具 - 记账 提醒 日程",
      path: "/pages/home/index",
    };
  },

  onShareTimeline() {
    return {
      title: "效率工具 - 记账 提醒 日程",
    };
  },
});
