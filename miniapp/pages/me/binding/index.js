const { getToken, setToken } = require("../../../utils/auth");
const {
  fetchProfile,
  fetchIdentities,
  createBindCode,
  consumeBindCode,
} = require("../../../utils/http");

Page({
  data: {
    profile: null,
    identities: [],
    bindCode: "",
    generatedCode: "",
    codeExpireAt: "",
    loading: false,
  },

  onShow() {
    if (!getToken()) {
      wx.showToast({ title: "请先登录", icon: "none" });
      wx.navigateBack();
      return;
    }
    this.loadData();
  },

  async loadData() {
    this.setData({ loading: true });
    try {
      const [profile, identities] = await Promise.all([fetchProfile(), fetchIdentities()]);
      this.setData({ profile: profile || null, identities: identities || [] });
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
        codeExpireAt: res.expires_at || "",
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
});
