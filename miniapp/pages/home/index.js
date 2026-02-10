const { getToken } = require("../../utils/auth");

Page({
  data: {
    authed: false
  },

  onOpenCommand() {
    wx.switchTab({ url: "/pages/chat/index" });
  },

  onQuickCmd(e) {
    const cmd = e.currentTarget.dataset.cmd || "";
    // 存到 globalData 让 chat 页读取
    getApp().globalData.pendingCmd = cmd;
    wx.switchTab({ url: "/pages/chat/index" });
  },

  onOpenLedger() {
    wx.switchTab({ url: "/pages/ledger/index" });
  },

  onOpenCalendar() {
    wx.switchTab({ url: "/pages/calendar/index" });
  },

  onShow() {
    const app = getApp();
    const token = getToken() || "";
    app.globalData.token = token;
    this.setData({ authed: !!token });
  }
});
