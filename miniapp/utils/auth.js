const TOKEN_KEY = "pai_token";

function setToken(token) {
  wx.setStorageSync(TOKEN_KEY, token || "");
}

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || "";
}

function clearToken() {
  wx.removeStorageSync(TOKEN_KEY);
}

module.exports = {
  TOKEN_KEY,
  setToken,
  getToken,
  clearToken
};
