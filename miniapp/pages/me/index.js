const config = require("../../config");
const { clearToken, getToken, setToken } = require("../../utils/auth");
const {
  fetchProfile,
  fetchIdentities,
  createBindCode,
  consumeBindCode
} = require("../../utils/http");

Page({
  data: {
    authed: false,
    profile: null,
    identities: [],
    bindCode: "",
    generatedCode: "",
    codeExpireAt: "",
    loading: false
  },

  onShow() {
    const token = getToken();
    const authed = !!token;
    this.setData({ authed });
    if (!authed) {
      this.setData({
        profile: null,
        identities: [],
        bindCode: "",
        generatedCode: "",
        codeExpireAt: ""
      });
      return;
    }
    this.loadData();
  },

  async loadData() {
    this.setData({ loading: true });
    try {
      const [profile, identities] = await Promise.all([fetchProfile(), fetchIdentities()]);
      this.setData({ profile, identities: identities || [] });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onBindCodeInput(e) {
    this.setData({ bindCode: (e.detail.value || "").trim() });
  },

  async onCreateBindCode() {
    try {
      const res = await createBindCode(10);
      this.setData({
        generatedCode: res.code || "",
        codeExpireAt: res.expires_at || ""
      });
      wx.showToast({ title: "绑定码已生成", icon: "success" });
    } catch (err) {
      wx.showToast({ title: err.message || "生成失败", icon: "none" });
    }
  },

  async onConsumeBindCode() {
    const code = this.data.bindCode;
    if (!/^\d{6}$/.test(code)) {
      wx.showToast({ title: "请输入6位数字绑定码", icon: "none" });
      return;
    }
    try {
      const res = await consumeBindCode(code);
      if (res.access_token) {
        setToken(res.access_token);
        getApp().globalData.token = res.access_token;
      }
      wx.showToast({ title: res.message || "绑定成功", icon: "none" });
      this.setData({ bindCode: "" });
      this.loadData();
    } catch (err) {
      wx.showToast({ title: err.message || "绑定失败", icon: "none" });
    }
  },

  onCopyCode() {
    if (!this.data.generatedCode) return;
    wx.setClipboardData({ data: this.data.generatedCode });
  },

  onEnableReminderSubscribe() {
    const tid = config.SUBSCRIBE_TEMPLATE_ID;
    if (!tid) {
      wx.showToast({ title: "请先配置模板ID", icon: "none" });
      return;
    }
    wx.requestSubscribeMessage({
      tmplIds: [tid],
      success: () => {
        wx.showToast({ title: "订阅请求已发起", icon: "none" });
      },
      fail: (err) => {
        wx.showToast({ title: err.errMsg || "订阅失败", icon: "none" });
      }
    });
  },

  onLogout() {
    clearToken();
    getApp().globalData.token = "";
    this.setData({
      authed: false,
      profile: null,
      identities: [],
      bindCode: "",
      generatedCode: "",
      codeExpireAt: ""
    });
    wx.showToast({ title: "已退出", icon: "none" });
  },

  onGoLogin() {
    const redirect = encodeURIComponent("/pages/me/index");
    wx.navigateTo({ url: `/pages/login/index?redirect=${redirect}` });
  }
});
