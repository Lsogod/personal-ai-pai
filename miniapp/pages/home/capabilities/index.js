Page({
  onOpenCommand() {
    wx.switchTab({ url: "/pages/chat/index" });
  },

  onOpenLedger() {
    wx.switchTab({ url: "/pages/ledger/index" });
  },

  onOpenCalendar() {
    wx.switchTab({ url: "/pages/calendar/index" });
  },

  onOpenBinding() {
    wx.navigateTo({ url: "/pages/me/binding/index" });
  },

  onOpenFeedback() {
    wx.navigateTo({ url: "/pages/me/feedback/index" });
  },
});
