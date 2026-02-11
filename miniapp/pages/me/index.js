const config = require("../../config");
const { clearToken, getToken } = require("../../utils/auth");
const { fetchProfile } = require("../../utils/http");

Page({
  data: {
    authed: false,
    profile: null,
    loading: false,
  },

  onShow() {
    const token = getToken();
    const authed = !!token;
    this.setData({ authed });
    if (!authed) {
      this.setData({ profile: null });
      return;
    }
    this.loadProfile();
  },

  async loadProfile() {
    this.setData({ loading: true });
    try {
      const profile = await fetchProfile();
      this.setData({ profile: profile || null });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  requireLoginThen(action) {
    if (this.data.authed) {
      action();
      return;
    }
    const redirect = encodeURIComponent("/pages/me/index");
    wx.navigateTo({ url: `/pages/login/index?redirect=${redirect}` });
  },

  onGoLogin() {
    const redirect = encodeURIComponent("/pages/me/index");
    wx.navigateTo({ url: `/pages/login/index?redirect=${redirect}` });
  },

  onOpenSkills() {
    this.requireLoginThen(() => wx.navigateTo({ url: "/pages/me/skills/index" }));
  },

  onOpenBinding() {
    this.requireLoginThen(() => wx.navigateTo({ url: "/pages/me/binding/index" }));
  },

  onOpenFeedback() {
    this.requireLoginThen(() => wx.navigateTo({ url: "/pages/me/feedback/index" }));
  },

  onEnableReminderSubscribe() {
    this.requireLoginThen(() => {
      const tid = String(config.SUBSCRIBE_TEMPLATE_ID || "").trim();
      if (!tid) {
        wx.showToast({
          title: "未配置模板ID，请先在 miniapp/config.local.js 设置",
          icon: "none",
        });
        return;
      }
      wx.requestSubscribeMessage({
        tmplIds: [tid],
        success: (res) => {
          if (res && res[tid] === "accept") {
            wx.showToast({ title: "订阅授权成功", icon: "none" });
            return;
          }
          wx.showToast({ title: "你未勾选该订阅模板", icon: "none" });
        },
        fail: (err) => {
          wx.showToast({ title: err.errMsg || "订阅请求失败", icon: "none" });
        },
      });
    });
  },

  onLogout() {
    clearToken();
    getApp().globalData.token = "";
    this.setData({ authed: false, profile: null });
    wx.showToast({ title: "已退出登录", icon: "none" });
  },

  onShareAppMessage() {
    return { title: "效率工具 - 记账 提醒 日程", path: "/pages/home/index" };
  },

  onShareTimeline() {
    return { title: "效率工具 - 记账 提醒 日程" };
  },
});
