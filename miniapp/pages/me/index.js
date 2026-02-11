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

  onLogout() {
    clearToken();
    getApp().globalData.token = "";
    this.setData({ authed: false, profile: null });
    wx.showToast({ title: "已退出", icon: "none" });
  },

  onShareAppMessage() {
    return { title: '效率工具 — 记账·提醒·日程', path: '/pages/home/index' };
  },
  onShareTimeline() {
    return { title: '效率工具 — 记账·提醒·日程' };
  }
});
