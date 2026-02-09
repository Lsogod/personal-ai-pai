const { getToken } = require("../../utils/auth");
const { fetchLedgers, fetchLedgerStats } = require("../../utils/http");

function fmtDateTime(isoText) {
  if (!isoText) return "";
  const dt = new Date(isoText);
  if (Number.isNaN(dt.getTime())) return "";
  const y = dt.getFullYear();
  const m = `${dt.getMonth() + 1}`.padStart(2, "0");
  const d = `${dt.getDate()}`.padStart(2, "0");
  const hh = `${dt.getHours()}`.padStart(2, "0");
  const mm = `${dt.getMinutes()}`.padStart(2, "0");
  return `${y}-${m}-${d} ${hh}:${mm}`;
}

Page({
  data: {
    authed: false,
    loading: false,
    stats: { total: 0, count: 0 },
    ledgers: []
  },

  onShow() {
    const token = getToken();
    const authed = !!token;
    this.setData({ authed });
    if (!authed) {
      this.setData({ stats: { total: 0, count: 0 }, ledgers: [] });
      return;
    }
    this.loadData();
  },

  async loadData() {
    this.setData({ loading: true });
    try {
      const [stats, ledgers] = await Promise.all([fetchLedgerStats(30), fetchLedgers(50)]);
      const rows = Array.isArray(ledgers)
        ? ledgers.map((item) => ({ ...item, _time: fmtDateTime(item.transaction_date) }))
        : [];
      this.setData({
        stats: stats || { total: 0, count: 0 },
        ledgers: rows
      });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onGoLogin() {
    const redirect = encodeURIComponent("/pages/ledger/index");
    wx.navigateTo({ url: `/pages/login/index?redirect=${redirect}` });
  }
});
