const { miniappLogin } = require("../../utils/http");
const { setToken } = require("../../utils/auth");

Page({
  data: {
    nickname: "",
    loading: false,
    error: ""
  },

  onShow() {
    const app = getApp();
    if (app.globalData.token) {
      wx.switchTab({ url: "/pages/chat/index" });
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
      wx.switchTab({ url: "/pages/chat/index" });
    } catch (err) {
      this.setData({ error: err.message || "登录失败，请稍后重试" });
    } finally {
      this.setData({ loading: false });
    }
  }
});
