const { getToken } = require("../../../utils/auth");
const { fetchSkills } = require("../../../utils/http");

function statusLabel(value) {
  const key = String(value || "").toUpperCase();
  if (key === "BUILTIN") return "内置";
  if (key === "DRAFT") return "草稿";
  if (key === "PUBLISHED") return "已发布";
  if (key === "DISABLED") return "已停用";
  return value || "未知";
}

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
      const skills = (Array.isArray(rows) ? rows : []).map((item) => ({
        ...item,
        status_label: statusLabel(item.status),
      }));
      this.setData({ skills });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loading: false });
    }
  }
});
