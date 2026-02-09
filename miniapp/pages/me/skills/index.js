const { getToken } = require("../../../utils/auth");
const { fetchSkills } = require("../../../utils/http");

Page({
  data: {
    skills: [],
    loading: false,
  },

  onShow() {
    if (!getToken()) {
      wx.showToast({ title: "请先登录", icon: "none" });
      wx.navigateBack();
      return;
    }
    this.loadSkills();
  },

  async loadSkills() {
    this.setData({ loading: true });
    try {
      const rows = await fetchSkills();
      this.setData({ skills: Array.isArray(rows) ? rows : [] });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  }
});
