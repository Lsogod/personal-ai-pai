const { getToken } = require("./utils/auth");

App({
  globalData: {
    token: "",
    profile: null,
    lastReminderAt: "",
    pendingCmd: "",
    homeQuickAction: ""
  },

  onLaunch() {
    const token = getToken();
    if (token) {
      this.globalData.token = token;
    }
  }
});
