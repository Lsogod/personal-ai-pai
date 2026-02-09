const { getToken } = require("../../utils/auth");

Page({
  data: {
    authed: false
  },

  onOpenChat() {
    wx.navigateTo({ url: "/pages/chat/index" });
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
