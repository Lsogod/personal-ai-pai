const { getToken } = require("./utils/auth");

App({
  globalData: {
    token: "",
    profile: null,
    lastReminderAt: ""
  },

  onLaunch() {
    const token = getToken();
    if (token) {
      this.globalData.token = token;
    }
  }
});
