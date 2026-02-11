const { getToken } = require("../../../utils/auth");
const { fetchProfile, submitUserFeedback } = require("../../../utils/http");

function readVersionInfo() {
  let version = "dev";
  let envVersion = "develop";
  try {
    const info = wx.getAccountInfoSync();
    if (info && info.miniProgram) {
      version = info.miniProgram.version || "dev";
      envVersion = info.miniProgram.envVersion || "develop";
    }
  } catch (err) {}
  return { version, envVersion, display: `v${version} (${envVersion})` };
}

Page({
  data: {
    authed: false,
    profile: null,
    versionInfo: readVersionInfo(),
    suggestion: "",
    submitting: false,
  },

  onShow() {
    const authed = !!getToken();
    this.setData({ authed });
    if (!authed) {
      this.setData({ profile: null });
      return;
    }
    this.loadProfile();
  },

  async loadProfile() {
    try {
      const profile = await fetchProfile();
      this.setData({ profile: profile || null });
    } catch (err) {}
  },

  onInputSuggestion(e) {
    this.setData({ suggestion: e.detail.value || "" });
  },

  onCopyVersion() {
    const text = `PAI 版本 ${this.data.versionInfo.display}`;
    wx.setClipboardData({
      data: text,
      success: () => wx.showToast({ title: "版本信息已复制", icon: "none" }),
    });
  },

  async onSubmitSuggestion() {
    if (this.data.submitting) return;
    if (!this.data.authed) {
      wx.showToast({ title: "请先登录", icon: "none" });
      return;
    }
    const suggestion = (this.data.suggestion || "").trim();
    if (suggestion.length < 4) {
      wx.showToast({ title: "建议内容至少4个字", icon: "none" });
      return;
    }

    this.setData({ submitting: true });
    try {
      await submitUserFeedback({
        content: suggestion,
        app_version: this.data.versionInfo.version,
        env_version: this.data.versionInfo.envVersion,
        client_page: "pages/me/feedback/index",
      });
      this.setData({ suggestion: "" });
      wx.showToast({ title: "反馈已提交", icon: "success" });
    } catch (err) {
      wx.showToast({ title: err.message || "提交失败", icon: "none" });
    } finally {
      this.setData({ submitting: false });
    }
  },
});
