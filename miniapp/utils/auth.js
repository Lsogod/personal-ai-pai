const TOKEN_KEY = "pai_token";
let authNoticeAt = 0;
let authNavigating = false;

function setToken(token) {
  wx.setStorageSync(TOKEN_KEY, token || "");
}

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || "";
}

function clearToken() {
  wx.removeStorageSync(TOKEN_KEY);
}

function getCurrentRoute() {
  try {
    const pages = getCurrentPages();
    if (!Array.isArray(pages) || pages.length === 0) return "/pages/chat/index";
    const current = pages[pages.length - 1];
    const route = `/${String(current.route || "").replace(/^\/+/, "")}`;
    return route || "/pages/chat/index";
  } catch (_) {
    return "/pages/chat/index";
  }
}

function handleAuthExpired(message = "登录已失效，请重新登录") {
  clearToken();
  try {
    const app = getApp && getApp();
    if (app && app.globalData) {
      app.globalData.token = "";
    }
  } catch (_) {
    // ignore
  }

  const now = Date.now();
  if (now - authNoticeAt < 3000) return;
  authNoticeAt = now;

  const route = getCurrentRoute();
  if (route.startsWith("/pages/login/index")) {
    wx.showToast({ title: message, icon: "none" });
    return;
  }
  if (authNavigating) return;
  authNavigating = true;

  const redirect = encodeURIComponent(route);
  wx.showModal({
    title: "登录状态失效",
    content: `${message}\n请重新登录后继续使用。`,
    showCancel: false,
    confirmText: "去登录",
    success() {
      wx.navigateTo({ url: `/pages/login/index?redirect=${redirect}` });
    },
    complete() {
      setTimeout(() => {
        authNavigating = false;
      }, 500);
    },
  });
}

module.exports = {
  TOKEN_KEY,
  setToken,
  getToken,
  clearToken,
  handleAuthExpired,
};
