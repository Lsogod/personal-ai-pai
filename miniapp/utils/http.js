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
      timeout: options.timeout || 15000,
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

function fetchConversations() {
  return request("/api/conversations");
}

function createConversation(title) {
  return request("/api/conversations", {
    method: "POST",
    data: title ? { title } : {}
  });
}

function switchConversation(conversationId) {
  return request(`/api/conversations/${conversationId}/switch`, {
    method: "POST"
  });
}

function deleteConversation(conversationId) {
  return request(`/api/conversations/${conversationId}`, {
    method: "DELETE"
  });
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

function clampLimit(limit, fallback = 30, max = 200) {
  const n = Number(limit);
  if (!Number.isFinite(n)) return fallback;
  const i = Math.floor(n);
  if (i < 1) return 1;
  if (i > max) return max;
  return i;
}

function fetchLedgers(limit = 30, beforeId) {
  const safeLimit = clampLimit(limit, 30, 200);
  let path = `/api/ledgers?limit=${safeLimit}`;
  const cursor = Number(beforeId);
  if (Number.isFinite(cursor) && cursor > 0) {
    path += `&before_id=${Math.floor(cursor)}`;
  }
  return request(path);
}

function createLedger(data) {
  return request("/api/ledgers", { method: "POST", data });
}

function updateLedger(id, data) {
  return request(`/api/ledgers/${id}`, { method: "PATCH", data });
}

function deleteLedger(id) {
  return request(`/api/ledgers/${id}`, { method: "DELETE" });
}

function fetchSchedules(limit = 50) {
  const safeLimit = clampLimit(limit, 50, 200);
  return request(`/api/schedules?limit=${safeLimit}`);
}

function createSchedule(data) {
  return request("/api/schedules", { method: "POST", data });
}

function updateSchedule(id, data) {
  return request(`/api/schedules/${id}`, { method: "PATCH", data });
}

function deleteSchedule(id) {
  return request(`/api/schedules/${id}`, { method: "DELETE" });
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

function submitUserFeedback(data) {
  return request("/api/user/feedback", {
    method: "POST",
    data: data || {}
  });
}

module.exports = {
  request,
  getWsUrl,
  miniappLogin,
  fetchProfile,
  fetchHistory,
  fetchConversations,
  createConversation,
  switchConversation,
  deleteConversation,
  sendChat,
  fetchCalendar,
  fetchLedgerStats,
  fetchLedgers,
  createLedger,
  updateLedger,
  deleteLedger,
  fetchSchedules,
  createSchedule,
  updateSchedule,
  deleteSchedule,
  fetchSkills,
  fetchIdentities,
  createBindCode,
  consumeBindCode,
  submitUserFeedback
};
