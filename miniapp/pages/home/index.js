const { getToken } = require("../../utils/auth");
const { fetchHomePopupConfig } = require("../../utils/http");

const STORAGE_POPUP_DAY = "home_popup_seen_day_v1";
const STORAGE_POPUP_VERSION = "home_popup_seen_version_v1";

function formatLocalDay(serverTime) {
  const dt = serverTime ? new Date(serverTime) : new Date();
  const y = dt.getFullYear();
  const m = `${dt.getMonth() + 1}`.padStart(2, "0");
  const d = `${dt.getDate()}`.padStart(2, "0");
  return `${y}-${m}-${d}`;
}

Page({
  data: {
    authed: false,
    showPopup: false,
    popupConfig: null,
  },

  onOpenCommand() {
    wx.switchTab({ url: "/pages/chat/index" });
  },

  onQuickAddLedger() {
    const app = getApp();
    app.globalData.homeQuickAction = "ledger_add";
    wx.switchTab({ url: "/pages/ledger/index" });
  },

  onQuickAddSchedule() {
    const app = getApp();
    app.globalData.homeQuickAction = "schedule_add";
    wx.switchTab({ url: "/pages/calendar/index" });
  },

  onOpenMe() {
    wx.switchTab({ url: "/pages/me/index" });
  },

  onOpenCapabilitiesDetail() {
    wx.navigateTo({ url: "/pages/home/capabilities/index" });
  },

  onShow() {
    const app = getApp();
    const token = getToken() || "";
    app.globalData.token = token;
    this.setData({ authed: !!token });
    this.loadHomePopup();
  },

  shouldShowPopup(config) {
    const mode = String(config.show_mode || "once_per_day").toLowerCase();
    if (mode === "always") return true;
    if (mode === "once_per_version") {
      const seenVersion = Number(wx.getStorageSync(STORAGE_POPUP_VERSION) || 0);
      const currentVersion = Number(config.version || 1);
      return seenVersion < currentVersion;
    }
    const today = formatLocalDay(config.server_time);
    const seenDay = String(wx.getStorageSync(STORAGE_POPUP_DAY) || "");
    return seenDay !== today;
  },

  markPopupSeen(config) {
    const mode = String(config.show_mode || "once_per_day").toLowerCase();
    if (mode === "once_per_version") {
      wx.setStorageSync(STORAGE_POPUP_VERSION, Number(config.version || 1));
      return;
    }
    if (mode === "once_per_day") {
      wx.setStorageSync(STORAGE_POPUP_DAY, formatLocalDay(config.server_time));
    }
  },

  async loadHomePopup() {
    try {
      const config = await fetchHomePopupConfig();
      if (!config || !config.active || !config.enabled) {
        this.setData({ showPopup: false, popupConfig: null });
        return;
      }
      if (!this.shouldShowPopup(config)) {
        this.setData({ showPopup: false, popupConfig: null });
        return;
      }
      this.setData({ showPopup: true, popupConfig: config });
    } catch (err) {
      this.setData({ showPopup: false, popupConfig: null });
    }
  },

  onClosePopup() {
    const cfg = this.data.popupConfig;
    if (cfg) this.markPopupSeen(cfg);
    this.setData({ showPopup: false });
  },

  onShareAppMessage() {
    return { title: "效率工具 - 记账/提醒/日程", path: "/pages/home/index" };
  },
  onShareTimeline() {
    return { title: "效率工具 - 记账/提醒/日程" };
  },
});
