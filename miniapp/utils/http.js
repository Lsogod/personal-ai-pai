const config = require("../config");
const { getToken } = require("./auth");

function toMessage(payload, statusCode) {
  if (typeof payload === "string" && payload) {
    return payload;
  }
  if (payload && typeof payload === "object") {
    if (typeof payload.detail === "string" && payload.detail) {
      return payload.detail;
    }
    if (Array.isArray(payload.detail) && payload.detail.length > 0) {
      return payload.detail.map((item) => {
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "参数";
        return `${field}: ${item.msg || "格式错误"}`;
      }).join("; ");
    }
  }
  if (statusCode >= 500) return "服务器暂时不可用";
  if (statusCode === 401) return "登录已失效，请重新登录";
  if (statusCode === 400) return "请求参数错误";
  return `请求失败(${statusCode})`;
}

function request(path, options = {}) {
  const token = options.token !== undefined ? options.token : getToken();
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${config.API_BASE_URL}${path}`,
      method: options.method || "GET",
      data: options.data || undefined,
      timeout: 20000,
      header: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.header || {})
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
          return;
        }
        reject(new Error(toMessage(res.data, res.statusCode)));
      },
      fail(err) {
        reject(new Error(err.errMsg || "网络异常"));
      }
    });
  });
}

function getWsUrl(token) {
  const base = config.API_BASE_URL || "";
  if (!base) return "";
  const wsBase = base.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  return `${wsBase}/api/notifications/ws?token=${encodeURIComponent(token)}`;
}

function miniappLogin(code, nickname) {
  return request("/api/miniapp/auth/login", {
    method: "POST",
    token: "",
    data: { code, nickname: nickname || undefined }
  });
}

function fetchProfile() {
  return request("/api/user/profile");
}

function fetchHistory() {
  return request("/api/chat/history");
}

function sendChat(content, imageUrls) {
  return request("/api/chat/send", {
    method: "POST",
    data: {
      content,
      image_urls: imageUrls || [],
      source_platform: "miniapp"
    }
  });
}

function fetchCalendar(startDate, endDate) {
  const start = encodeURIComponent(startDate);
  const end = encodeURIComponent(endDate);
  return request(`/api/calendar?start_date=${start}&end_date=${end}`);
}

function fetchLedgerStats(days = 30) {
  return request(`/api/stats/ledger?days=${days}`);
}

function fetchLedgers(limit = 30) {
  return request(`/api/ledgers?limit=${limit}`);
}

function fetchSkills() {
  return request("/api/skills");
}

function fetchIdentities() {
  return request("/api/user/identities");
}

function createBindCode(ttlMinutes = 10) {
  return request("/api/user/bind-code", {
    method: "POST",
    data: { ttl_minutes: ttlMinutes }
  });
}

function consumeBindCode(code) {
  return request("/api/user/bind-consume", {
    method: "POST",
    data: { code }
  });
}

module.exports = {
  request,
  getWsUrl,
  miniappLogin,
  fetchProfile,
  fetchHistory,
  sendChat,
  fetchCalendar,
  fetchLedgerStats,
  fetchLedgers,
  fetchSkills,
  fetchIdentities,
  createBindCode,
  consumeBindCode
};
