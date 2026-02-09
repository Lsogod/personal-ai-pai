const { miniappLogin } = require("../../utils/http");
const { getToken, setToken } = require("../../utils/auth");

Page({
  data: {
    nickname: "",
    loading: false,
    error: "",
    redirect: "/pages/chat/index"
  },

  onLoad(options) {
    const redirect = decodeURIComponent((options && options.redirect) || "").trim();
    if (redirect.startsWith("/pages/")) {
      this.setData({ redirect });
    }
  },

  onShow() {
    const app = getApp();
    const token = app.globalData.token || getToken();
    if (token) {
      app.globalData.token = token;
      this.goAfterLogin();
    }
  },

  onNicknameInput(e) {
    this.setData({ nickname: e.detail.value || "", error: "" });
  },

  async onLogin() {
    if (this.data.loading) return;
    this.setData({ loading: true, error: "" });

    try {
      const wxLoginRes = await new Promise((resolve, reject) => {
        wx.login({
          success: resolve,
          fail: reject
        });
      });

      const code = wxLoginRes.code;
      if (!code) {
        throw new Error("微信登录失败：缺少 code");
      }

      const data = await miniappLogin(code, this.data.nickname);
      if (!data.access_token) {
        throw new Error("登录成功但未返回 token");
      }

      setToken(data.access_token);
      const app = getApp();
      app.globalData.token = data.access_token;
      this.goAfterLogin();
    } catch (err) {
      this.setData({ error: err.message || "登录失败，请稍后重试" });
    } finally {
      this.setData({ loading: false });
    }
  },

  goAfterLogin() {
    const redirect = this.data.redirect || "/pages/chat/index";
    const tabPages = [
      "/pages/chat/index",
      "/pages/ledger/index",
      "/pages/calendar/index",
      "/pages/me/index"
    ];
    if (tabPages.includes(redirect)) {
      wx.switchTab({ url: redirect });
      return;
    }
    wx.redirectTo({ url: redirect });
  },

  onBackHome() {
    wx.switchTab({ url: "/pages/chat/index" });
  }
});
