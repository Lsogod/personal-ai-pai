const SUBSCRIBE_CACHE_PREFIX = "pai_subscribe_status:";

function makeCacheKey(templateId) {
  return `${SUBSCRIBE_CACHE_PREFIX}${String(templateId || "").trim()}`;
}

function readSubscribeCache(templateId) {
  const tid = String(templateId || "").trim();
  if (!tid) return null;
  try {
    const value = wx.getStorageSync(makeCacheKey(tid));
    if (value && typeof value === "object") {
      return value;
    }
  } catch (_) {
    // ignore
  }
  return null;
}

function markSubscribeAccepted(templateId) {
  const tid = String(templateId || "").trim();
  if (!tid) return;
  try {
    wx.setStorageSync(makeCacheKey(tid), { lastAcceptAt: Date.now() });
  } catch (_) {
    // ignore
  }
}

function clearSubscribeAccepted(templateId) {
  const tid = String(templateId || "").trim();
  if (!tid) return;
  try {
    wx.removeStorageSync(makeCacheKey(tid));
  } catch (_) {
    // ignore
  }
}

function buildStatus(status, text, hint) {
  return {
    status,
    text,
    hint,
  };
}

function getSubscribeStatus(templateId, authed) {
  const tid = String(templateId || "").trim();
  if (!authed) {
    return Promise.resolve(buildStatus("checking", "未登录", "登录后可查看提醒订阅状态"));
  }
  if (!tid) {
    return Promise.resolve(buildStatus("unconfigured", "未配置", "当前未配置订阅模板 ID"));
  }
  if (!wx.getSetting) {
    return Promise.resolve(buildStatus("unknown", "未知", "当前基础库不支持读取订阅授权状态"));
  }
  const cached = readSubscribeCache(tid);
  return new Promise((resolve) => {
    wx.getSetting({
      withSubscriptions: true,
      success: (res) => {
        const subscriptions = (res && res.subscriptionsSetting) || {};
        if (subscriptions && subscriptions.mainSwitch === false) {
          resolve(buildStatus("reject", "总开关关闭", "请在微信通知设置中打开订阅消息总开关"));
          return;
        }
        const itemSettings = (subscriptions && subscriptions.itemSettings) || {};
        const state = itemSettings[tid];
        if (state === "accept") {
          resolve(buildStatus("accept", "已授权", "提醒可尝试走微信订阅消息离线推送"));
          return;
        }
        if (state === "reject") {
          resolve(buildStatus("reject", "已拒绝", "你可以重新点击请求提醒订阅授权"));
          return;
        }
        if (state === "ban") {
          resolve(buildStatus("ban", "已封禁", "该模板当前不可再请求授权，请检查微信侧限制"));
          return;
        }
        if (cached && cached.lastAcceptAt) {
          resolve(buildStatus("accept", "已授权", "已记录最近一次授权，后续提醒将尝试离线推送"));
          return;
        }
        resolve(buildStatus("unset", "未授权", "点击后可开启离线提醒订阅"));
      },
      fail: () => {
        if (cached && cached.lastAcceptAt) {
          resolve(buildStatus("accept", "已授权", "已记录最近一次授权，后续提醒将尝试离线推送"));
          return;
        }
        resolve(buildStatus("unknown", "读取失败", "暂时无法读取授权状态，可重新点击授权"));
      },
    });
  });
}

module.exports = {
  getSubscribeStatus,
  markSubscribeAccepted,
  clearSubscribeAccepted,
};
