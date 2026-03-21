const { getToken, handleAuthExpired } = require("../../utils/auth");
const config = require("../../config");
const {
  fetchHistory,
  fetchConversations,
  createConversation,
  switchConversation,
  deleteConversation,
  fetchProfile,
  fetchLedgerStats,
  sendChat,
  getWsUrl
} = require("../../utils/http");
const { pickImages, resolveImageUrlsForDisplay } = require("../../utils/image");
const { markdownToRichNodes } = require("../../utils/markdown");
const DISPLAY_TZ_OFFSET_MINUTES = 8 * 60; // Asia/Shanghai

/**
 * 解析 ISO 时间字符串并返回本地 HH:MM。
 * 兼容微信小程序 JS 引擎：手动处理带 "Z" 后缀的 UTC 时间，
 * 避免某些环境下 new Date("...Z") 不能正确转本地时区的问题。
 */
function fmtTime(isoText) {
  if (!isoText) return "";
  const dt = toDisplayDate(isoText);
  if (!dt) return "";
  const hh = `${dt.getUTCHours()}`.padStart(2, "0");
  const mm = `${dt.getUTCMinutes()}`.padStart(2, "0");
  return `${hh}:${mm}`;
}

function nowIso() {
  return new Date().toISOString();
}

function fmtDateTime(isoText) {
  if (!isoText) return "";
  const dt = toDisplayDate(isoText);
  if (!dt) return "";
  const mm = `${dt.getUTCMonth() + 1}`.padStart(2, "0");
  const dd = `${dt.getUTCDate()}`.padStart(2, "0");
  const hh = `${dt.getUTCHours()}`.padStart(2, "0");
  const mi = `${dt.getUTCMinutes()}`.padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function toDisplayDate(value) {
  const dt = parseDateTime(value);
  if (Number.isNaN(dt.getTime())) return "";
  return new Date(dt.getTime() + DISPLAY_TZ_OFFSET_MINUTES * 60 * 1000);
}

function parseDateTime(value) {
  const raw = String(value || "").trim();
  if (!raw) return new Date("");

  // Keep timezone info when present (e.g. trailing "Z") so local conversion is correct.
  let dt = new Date(raw);
  if (!Number.isNaN(dt.getTime())) return dt;

  // Fallback for engines that dislike "T" with timezone.
  dt = new Date(raw.replace("T", " "));
  if (!Number.isNaN(dt.getTime())) return dt;

  // Last fallback for older parsers.
  dt = new Date(raw.replace(/-/g, "/").replace("T", " ").replace(/Z$/, ""));
  return dt;
}

function compactText(value, maxLen = 80) {
  const src = String(value || "")
    .replace(/[\r\n]+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/[`*_>#]/g, " ")
    .trim();
  if (!src) return "";
  if (src.length <= maxLen) return src;
  return `${src.slice(0, maxLen)}...`;
}

function isTimeoutError(err) {
  const text = String((err && err.message) || err || "").toLowerCase();
  return text.includes("timeout") || text.includes("timed out") || text.includes("超时");
}

function normalizeMessage(item) {
  const role = item.role === "assistant" ? "assistant" : "user";
  const content = item.content || "";
  const imageUrls = Array.isArray(item.image_urls) ? item.image_urls : [];
  const displayContent =
    role === "user" && imageUrls.length > 0 && String(content).trim() === "[图片]"
      ? ""
      : content;
  return {
    role,
    content,
    display_content: displayContent,
    content_nodes: role === "assistant" ? markdownToRichNodes(content) : "",
    created_at: item.created_at || nowIso(),
    timeText: fmtTime(item.created_at || nowIso()),
    image_urls: imageUrls,
    display_image_urls: imageUrls
  };
}

async function normalizeMessageAsync(item) {
  const msg = normalizeMessage(item);
  msg.display_image_urls = await resolveImageUrlsForDisplay(msg.image_urls);
  return msg;
}

function normText(value) {
  return String(value || "").trim();
}

function toInt(value, fallback = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.trunc(n);
}

function buildOnboardingView(profile) {
  const setupStage = toInt(profile && profile.setup_stage, 0);
  const bindingStage = toInt(profile && profile.binding_stage, 0);
  if (setupStage >= 3) {
    return {
      guideVisible: false,
      onboardingTitle: "",
      onboardingHint: "",
      onboardingPlaceholder: "",
      onboardingSuggestions: [],
    };
  }

  if (setupStage === 0 && bindingStage <= 1) {
    return {
      guideVisible: true,
      onboardingTitle: "账号绑定引导",
      onboardingHint: "请先确认你在其他客户端是否已有账号，也可以直接输入绑定命令。",
      onboardingPlaceholder: "输入 有 / 没有",
      onboardingSuggestions: [
        { label: "有", text: "有" },
        { label: "没有", text: "没有" },
      ],
    };
  }

  if (setupStage <= 1) {
    return {
      guideVisible: true,
      onboardingTitle: "设置你的称呼",
      onboardingHint: "完成这个步骤后，我会按你设定的名字来称呼你。",
      onboardingPlaceholder: "输入你的称呼",
      onboardingSuggestions: [
        { label: "主人", text: "主人" },
      ],
    };
  }

  return {
    guideVisible: true,
    onboardingTitle: "给助手起名",
    onboardingHint: "再完成一步后将打开完整输入框，你可以开始正常使用指令面板。",
    onboardingPlaceholder: "例如：贾维斯",
    onboardingSuggestions: [
      { label: "贾维斯", text: "贾维斯" },
      { label: "PAI", text: "PAI" },
    ],
  };
}

Page({
  data: {
    authed: false,
    profile: null,
    stats: { total: 0, count: 0 },
    messages: [],
    sidebarOpen: false,
    conversations: [],
    loadingConversations: false,
    pendingState: "",
    toolSteps: [],
    toolStepsExpanded: true,
    toolStepsDone: false,
    toolStepsDoneCount: 0,
    inputText: "",
    guideVisible: true,
    onboardingTitle: "",
    onboardingHint: "",
    onboardingPlaceholder: "",
    onboardingSuggestions: [],
    onboardingInput: "",
    onboardingDefaultPlaceholder: "\u8bf7\u8f93\u5165",
    onboardingConfirmText: "\u786e\u8ba4",
    onboardingWaitingText: "\u6b63\u5728\u7b49\u5f85\u540e\u53f0\u56de\u590d...",
    selectedImages: [],
    sending: false,
    wsOpen: false,
    notifyCards: [],
    loadingHistory: false
  },

  onLoad() {
    this._wsTask = null;
    this._pingTimer = null;
    this._seenKeys = new Set();
    this._seenReminderKeys = new Set();
    this._wsChunkStreams = new Map();
    this._sendingLock = false;
    this._pendingUserEcho = [];
    this._streamQueue = [];
    this._streaming = false;
    this._streamTimer = null;
    this._scrollTimers = [];
    this._pendingReplyTimer = null;
    this._pendingNonce = 0;
    this._subscribePromptAt = 0;
  },

  onShow() {
    const authed = this.syncAuthState();
    if (authed) {
      this.loadInitial();
      this.loadConversations();
      this.connectSocket();
      this.scrollToBottom(true);
      // 从首页快捷指令跳转过来
      return;
    }
    this.closeSocket();
    this.setData({
      profile: { ai_name: "PAI", ai_emoji: "" },
      stats: { total: 0, count: 0 },
      messages: [],
      sidebarOpen: false,
      conversations: [],
      loadingConversations: false,
      pendingState: "",
      guideVisible: false,
      onboardingTitle: "",
      onboardingHint: "",
      onboardingPlaceholder: "",
      onboardingSuggestions: [],
      onboardingInput: "",
      notifyCards: []
    });
  },

  onReady() {
    this.scrollToBottom(true);
  },

  onPageShow() {
    // 每次页面可见时强制滚到底部
    this.scrollToBottom(true);
  },

  onHide() {
    this.closeSocket();
    this.stopStream();
    this.clearWsChunkStreams();
    this.clearScrollRetry();
    this.clearPendingReplyTimer();
  },

  onUnload() {
    this.closeSocket();
    this.stopStream();
    this.clearWsChunkStreams();
    this.clearScrollRetry();
    this.clearPendingReplyTimer();
  },

  syncAuthState() {
    const token = getToken();
    if (!token) {
      getApp().globalData.token = "";
      this.setData({ authed: false, wsOpen: false });
      return false;
    }
    getApp().globalData.token = token;
    this.setData({ authed: true });
    return true;
  },

  applyProfile(profile, extraPatch = {}) {
    const onboarding = buildOnboardingView(profile);
    this.setData({
      profile,
      guideVisible: onboarding.guideVisible,
      sidebarOpen: onboarding.guideVisible ? false : this.data.sidebarOpen,
      onboardingTitle: onboarding.onboardingTitle,
      onboardingHint: onboarding.onboardingHint,
      onboardingPlaceholder: onboarding.onboardingPlaceholder,
      onboardingSuggestions: onboarding.onboardingSuggestions,
      ...extraPatch,
    });
  },

  consumePendingCmd(profile) {
    const onboarding = buildOnboardingView(profile);
    if (onboarding.guideVisible) return;
    const app = getApp();
    const cmd = String(app.globalData.pendingCmd || "").trim();
    if (!cmd) return;
    app.globalData.pendingCmd = "";
    this.setData({ inputText: cmd });
  },

  async refreshProfileState() {
    if (!this.data.authed) return;
    try {
      const profile = await fetchProfile();
      this.applyProfile(profile);
      this.consumePendingCmd(profile);
    } catch (err) {
      // ignore profile refresh errors
    }
  },

  async loadInitial() {
    if (!this.data.authed) return;
    this.setData({ loadingHistory: true });
    try {
      const [profile, history, stats] = await Promise.all([
        fetchProfile(),
        fetchHistory(),
        fetchLedgerStats(30)
      ]);
      const messages = await Promise.all((history || []).map(normalizeMessageAsync));
      this._seenKeys.clear();
      messages.forEach((m) => this._seenKeys.add(this.messageKey(m)));
      this.clearWsChunkStreams();
      this.applyProfile(profile, {
        stats: stats || { total: 0, count: 0 },
        messages,
      });
      this.consumePendingCmd(profile);
      this.scrollToBottom(true);
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    } finally {
      this.setData({ loadingHistory: false });
    }
  },

  async loadConversations() {
    if (!this.data.authed) return;
    this.setData({ loadingConversations: true });
    try {
      const rows = await fetchConversations();
      const conversations = (rows || []).map((item) => ({
        id: item.id,
        title: compactText(item.title || `记录 #${item.id}`, 22),
        summary: compactText(item.summary || "", 88),
        active: !!item.active,
        lastText: fmtDateTime(item.last_message_at || ""),
      }));
      this.setData({ conversations, loadingConversations: false });
    } catch (err) {
      this.setData({ loadingConversations: false });
    }
  },

  onToggleSidebar() {
    if (!this.data.authed) {
      this.onGoLogin();
      return;
    }
    if (this.data.guideVisible) {
      wx.showToast({ title: "\u8bf7\u5148\u5b8c\u6210\u5f15\u5bfc", icon: "none" });
      return;
    }
    const next = !this.data.sidebarOpen;
    this.setData({ sidebarOpen: next });
    if (next) {
      this.loadConversations();
    }
  },

  onCloseSidebar() {
    this.setData({ sidebarOpen: false });
  },

  stopTap() {},

  async onNewConversation() {
    if (!this.data.authed) return;
    if (this._sendingLock) return;
    try {
      await createConversation();
      this.setData({ sidebarOpen: false });
      await this.loadConversations();
      await this.loadInitial();
      wx.showToast({ title: "已新建记录", icon: "none" });
    } catch (err) {
      wx.showToast({ title: err.message || "新建失败", icon: "none" });
    }
  },

  async onSwitchConversation(e) {
    if (!this.data.authed) return;
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    const current = this.data.conversations.find((x) => x.active);
    if (current && current.id === id) {
      this.setData({ sidebarOpen: false });
      return;
    }
    try {
      await switchConversation(id);
      this.setData({ sidebarOpen: false });
      await this.loadConversations();
      await this.loadInitial();
      wx.showToast({ title: "已切换记录", icon: "none" });
    } catch (err) {
      wx.showToast({ title: err.message || "切换失败", icon: "none" });
    }
  },

  /* ── 删除会话 ── */
  async onDeleteConversation(e) {
    const id = Number(e.currentTarget.dataset.id);
    if (!id) return;
    const { confirm } = await new Promise(resolve =>
      wx.showModal({
        title: "删除记录",
        content: "删除后无法恢复，是否继续？",
        confirmText: "删除",
        confirmColor: "#e74c3c",
        success: resolve,
        fail: () => resolve({ confirm: false })
      })
    );
    if (!confirm) return;
    try {
      await deleteConversation(id);
      wx.showToast({ title: "已删除", icon: "none" });
      await this.loadConversations();
      // 如果删的是当前活跃会话，重新加载消息
      const active = this.data.conversations.find(x => x.active);
      if (!active) {
        this.setData({ messages: [] });
        this._seenKeys.clear();
      }
    } catch (err) {
      wx.showToast({ title: err.message || "删除失败", icon: "none" });
    }
  },

  messageKey(msg) {
    return `${msg.role}|${msg.created_at}|${msg.content}|${(msg.image_urls || []).join("|")}`;
  },

  reminderEventKey(payload) {
    if (!payload || typeof payload !== "object") return "";
    if (payload.schedule_id !== undefined && payload.schedule_id !== null) {
      return `schedule:${payload.schedule_id}`;
    }
    const trigger = String(payload.trigger_time || "");
    const content = String(payload.content || "");
    if (!trigger && !content) return "";
    return `fallback:${trigger}|${content}`;
  },

  seenReminder(payload) {
    const key = this.reminderEventKey(payload);
    if (!key) return false;
    if (this._seenReminderKeys.has(key)) return true;
    this._seenReminderKeys.add(key);
    if (this._seenReminderKeys.size > 200) {
      const oldest = this._seenReminderKeys.values().next().value;
      this._seenReminderKeys.delete(oldest);
    }
    return false;
  },

  async appendMessages(rows) {
    const newMsgs = [];
    let hasAssistant = false;
    const normalizedRows = await Promise.all((rows || []).map(normalizeMessageAsync));
    for (const msg of normalizedRows) {
      if (msg.role === "assistant") hasAssistant = true;
      const key = this.messageKey(msg);
      if (this._seenKeys.has(key)) continue;
      this._seenKeys.add(key);
      newMsgs.push(msg);
    }
    if (!newMsgs.length) return;
    if (hasAssistant) this.clearPendingByAssistantSignal();
    const base = this.data.messages.length;
    const patch = {};
    newMsgs.forEach((m, i) => { patch[`messages[${base + i}]`] = m; });
    this.setData(patch, () => this.scrollToBottom());
  },

  clearWsChunkStreams() {
    this._wsChunkStreams = new Map();
  },

  appendAssistantChunkStream(streamId, chunk, createdAt) {
    const sid = String(streamId || "").trim();
    if (!sid) return;
    const text = String(chunk || "");
    const at = String(createdAt || nowIso());
    let index = this._wsChunkStreams.get(sid);
    const messages = [...this.data.messages];
    if (!Number.isFinite(index) || !messages[index]) {
      const seed = normalizeMessage({ role: "assistant", content: "", created_at: at });
      messages.push(seed);
      index = messages.length - 1;
      this._wsChunkStreams.set(sid, index);
    }
    if (text) {
      const current = messages[index];
      const nextContent = `${current.content || ""}${text}`;
      // During streaming, use plain text node to avoid broken markdown rendering
      messages[index] = {
        ...current,
        content: nextContent,
        display_content: nextContent,
        content_nodes: [{ type: "node", name: "span", attrs: { style: "white-space:pre-wrap;" }, children: [{ type: "text", text: nextContent }] }],
      };
    }
    this.setData({ messages }, () => this.scrollToBottom());
  },

  finishAssistantChunkStream(streamId) {
    const sid = String(streamId || "").trim();
    if (!sid) return;
    const index = this._wsChunkStreams.get(sid);
    this._wsChunkStreams.delete(sid);
    if (!Number.isFinite(index)) return;
    const messages = [...this.data.messages];
    const msg = messages[index];
    if (msg && msg.content) {
      // Stream finished – now render full markdown
      messages[index] = {
        ...msg,
        content_nodes: markdownToRichNodes(msg.content),
      };
      this.setData({ messages });
      this._seenKeys.add(this.messageKey(msg));
    }
    this.clearPendingByAssistantSignal();
  },

  clearPendingByAssistantSignal() {
    if (!this.data.pendingState) return;
    this._pendingNonce += 1;
    this.clearPendingReplyTimer();
    this.setData({ pendingState: "" });
  },

  setPendingState(state) {
    const nextState = state || "";
    this._pendingNonce += 1;
    const nonce = this._pendingNonce;
    this.clearPendingReplyTimer();
    if (this.data.pendingState === nextState) return;
    this.setData({ pendingState: nextState }, () => this.scrollToBottom());
    if (nextState) {
      this._pendingReplyTimer = setTimeout(() => {
        if (nonce !== this._pendingNonce) return;
        if (!this.data.pendingState) return;
        this.setData({ pendingState: "" });
      }, 20000);
    }
  },

  clearPendingReplyTimer() {
    if (this._pendingReplyTimer) {
      clearTimeout(this._pendingReplyTimer);
      this._pendingReplyTimer = null;
    }
  },

  stopStream() {
    this._streamQueue = [];
    this._streaming = false;
    if (this._streamTimer) {
      clearTimeout(this._streamTimer);
      this._streamTimer = null;
    }
  },

  enqueueAssistantStream(text, createdAt) {
    const raw = normText(text);
    if (!raw) return;
    this.clearPendingByAssistantSignal();
    const msg = normalizeMessage({ role: "assistant", content: raw, created_at: createdAt || nowIso() });
    const key = this.messageKey(msg);
    if (this._seenKeys.has(key)) return;
    this._seenKeys.add(key);

    const index = this.data.messages.length;
    const placeholder = {
      ...msg,
      content: raw,
      display_content: "",
      content_nodes: markdownToRichNodes(""),
    };
    this.setData({ [`messages[${index}]`]: placeholder }, () => this.scrollToBottom());

    this._streamQueue.push({ index, fullText: raw });
    this.runStreamQueue();
  },

  runStreamQueue() {
    if (this._streaming || this._streamQueue.length === 0) return;
    this._streaming = true;
    const current = this._streamQueue.shift();
    const fullText = current.fullText || "";
    const step = fullText.length > 240 ? 4 : fullText.length > 120 ? 3 : 2;
    let cursor = 0;

    const tick = () => {
      cursor = Math.min(fullText.length, cursor + step);
      const partial = fullText.slice(0, cursor);
      const path = `messages[${current.index}]`;
      this.setData({
        [`${path}.display_content`]: partial,
        [`${path}.content_nodes`]: markdownToRichNodes(partial),
      });
      if (cursor < fullText.length) {
        this._streamTimer = setTimeout(tick, 18);
      } else {
        this._streaming = false;
        this.scrollToBottom();
        this.runStreamQueue();
      }
    };

    tick();
  },

  queuePendingUserEcho(text) {
    const value = normText(text);
    if (!value) return;
    const now = Date.now();
    this._pendingUserEcho = this._pendingUserEcho
      .filter((x) => now - x.at < 20000)
      .concat([{ text: value, at: now }]);
  },

  consumePendingUserEcho(text) {
    const value = normText(text);
    if (!value) return false;
    const now = Date.now();
    const list = [];
    let matched = false;
    for (const row of this._pendingUserEcho) {
      if (now - row.at >= 20000) continue;
      if (!matched && row.text === value) {
        matched = true;
        continue;
      }
      list.push(row);
    }
    this._pendingUserEcho = list;
    return matched;
  },

  scrollToBottom(immediate = false) {
    this.clearScrollRetry();
    const delays = immediate ? [0, 150, 500] : [50, 300];
    this._scrollTimers = delays.map((delay) =>
      setTimeout(() => {
        wx.pageScrollTo({ scrollTop: 999999, duration: 0 });
      }, delay)
    );
  },

  clearScrollRetry() {
    if (Array.isArray(this._scrollTimers)) {
      this._scrollTimers.forEach((timer) => clearTimeout(timer));
      this._scrollTimers = [];
    }
  },

  onBubbleMediaLoad() {
    this.scrollToBottom();
  },

  /**
   * 检查订阅授权状态，未授权时弹出授权弹窗。
   * silent=true 时不显示 toast（用于自动触发场景）。
   */
  requestReminderSubscribe(silent) {
    const tid = String(config.SUBSCRIBE_TEMPLATE_ID || "").trim();
    if (!tid) {
      if (!silent) wx.showToast({ title: "未配置订阅模板ID", icon: "none" });
      return;
    }
    wx.getSetting({
      withSubscriptions: true,
      success: (settingRes) => {
        const sub = (settingRes && settingRes.subscriptionsSetting) || {};
        const items = sub.itemSettings || {};
        // 已永久授权，无需再弹
        if (items[tid] === "accept") return;
        // 已永久拒绝，弹窗也无效，提示用户去设置
        if (items[tid] === "reject") {
          if (!silent) {
            wx.showModal({
              title: "提醒推送已关闭",
              content: "你之前选择了拒绝订阅提醒，请到小程序设置中重新开启。",
              confirmText: "知道了",
              showCancel: false,
            });
          }
          return;
        }
        // 未做永久选择，弹出授权
        wx.requestSubscribeMessage({
          tmplIds: [tid],
          success: (res) => {
            if (res && res[tid] === "accept") {
              if (!silent) wx.showToast({ title: "提醒订阅授权成功", icon: "none" });
              return;
            }
            if (!silent) wx.showToast({ title: "未同意订阅，离线提醒可能收不到", icon: "none" });
          },
          fail: (err) => {
            if (!silent) wx.showToast({ title: (err && err.errMsg) || "订阅请求失败", icon: "none" });
          },
        });
      },
      fail: () => {
        // getSetting 失败，降级直接弹
        wx.requestSubscribeMessage({ tmplIds: [tid], success: () => {}, fail: () => {} });
      },
    });
  },

  promptSubscribeInvalid(payload) {
    const now = Date.now();
    if (now - this._subscribePromptAt < 5000) return;
    this._subscribePromptAt = now;
    const reason = String((payload && payload.reason) || "").trim();
    const content = String((payload && payload.content) || "").trim() || "提醒订阅已失效，请重新授权。";
    const text = reason ? `${content}\n原因：${reason}` : content;
    wx.showModal({
      title: "提醒订阅失效",
      content: text,
      confirmText: "去授权",
      cancelText: "稍后",
      success: (res) => {
        if (res && res.confirm) {
          this.requestReminderSubscribe();
        }
      },
    });
  },

  connectSocket() {
    if (!this.data.authed) return;
    if (this._wsTask) return;
    const token = getToken();
    const url = getWsUrl(token);
    if (!url) return;

    const task = wx.connectSocket({ url, timeout: 15000 });
    this._wsTask = task;

    task.onOpen(() => {
      this.setData({ wsOpen: true });
      if (this._pingTimer) clearInterval(this._pingTimer);
      this._pingTimer = setInterval(() => {
        try {
          this._wsTask && this._wsTask.send({ data: "ping" });
        } catch (e) {
          // ignore
        }
      }, 20000);
    });

    task.onClose((evt) => {
      this.setData({ wsOpen: false });
      if (this._pingTimer) {
        clearInterval(this._pingTimer);
        this._pingTimer = null;
      }
      this._wsTask = null;
      if (evt && Number(evt.code) === 4401) {
        this.setData({ authed: false });
        handleAuthExpired("登录已失效，请重新登录");
        return;
      }
      // 自动重连（3秒后），避免网络波动导致断连
      if (this.data.authed && !this._wsReconnectTimer) {
        this._wsReconnectTimer = setTimeout(() => {
          this._wsReconnectTimer = null;
          if (this.data.authed && !this._wsTask) this.connectSocket();
        }, 3000);
      }
    });

    task.onError((evt) => {
      this.setData({ wsOpen: false });
      this._wsTask = null;
      const msg = String((evt && evt.errMsg) || "").toLowerCase();
      if (msg.includes("4401") || msg.includes("invalid token")) {
        this.setData({ authed: false });
        handleAuthExpired("登录已失效，请重新登录");
      }
    });

    task.onMessage((evt) => {
      let payload = null;
      try {
        payload = JSON.parse(evt.data || "{}");
      } catch (e) {
        return;
      }
      if (!payload || !payload.type) return;

      if (payload.type === "reminder") {
        if (this.seenReminder(payload)) return;
        this.setPendingState("");
        const content = payload.content || "提醒";
        const createdAt = payload.created_at || nowIso();
        this.appendMessages([{ role: "assistant", content, created_at: createdAt }]);
        this.pushNotify({ content, createdAt });
        wx.vibrateShort({ type: "light" });
        this.refreshStats();
        return;
      }

      if (payload.type === "tool_event") {
        const tc = payload.tool_call || {};
        const event = tc.status || payload.event || ""; // "start" or "done"
        const toolName = tc.name || "";
        const toolLabel = tc.label || toolName;
        if (event === "start" || event === "tool_start") {
          const toolSteps = [...(this.data.toolSteps || [])];
          toolSteps.push({ name: toolLabel, status: "running" });
          const doneCount = toolSteps.filter(s => s.status === "done").length;
          this.setData({
            toolSteps,
            toolStepsDoneCount: doneCount,
            toolStepsDone: false,
            toolStepsExpanded: true,
            pendingState: "",
          });
        } else if (event === "done" || event === "tool_end") {
          const toolSteps = [...(this.data.toolSteps || [])];
          const idx = toolSteps.findIndex(s => s.name === toolLabel && s.status === "running");
          if (idx !== -1) {
            toolSteps[idx] = { ...toolSteps[idx], status: "done" };
          }
          const doneCount = toolSteps.filter(s => s.status === "done").length;
          const allDone = doneCount === toolSteps.length;
          this.setData({
            toolSteps,
            toolStepsDoneCount: doneCount,
            toolStepsDone: allDone,
            toolStepsExpanded: !allDone,
          });
          // 通过对话创建提醒后，检查订阅状态，未授权则弹出授权
          const tn = toolName.toLowerCase();
          if (tn === "schedule_insert" || tn === "schedule_update") {
            this.requestReminderSubscribe(true);
          }
        }
        this.scrollToBottom();
        return;
      }

      if (payload.type === "message_chunk") {
        const streamId = String(payload.stream_id || "").trim();
        if (!streamId) return;
        if (payload.done === true) {
          this.finishAssistantChunkStream(streamId);
          this.refreshStats();
          return;
        }
        this.setPendingState("");
        this.appendAssistantChunkStream(streamId, payload.chunk || "", payload.created_at || nowIso());
        return;
      }

      if (payload.type === "subscribe_invalid") {
        this.promptSubscribeInvalid(payload);
        return;
      }

      if (payload.type === "message" && (payload.content || (Array.isArray(payload.image_urls) && payload.image_urls.length > 0))) {
        const role = payload.role === "assistant" ? "assistant" : "user";
        if (role === "user" && this.consumePendingUserEcho(payload.content)) {
          return;
        }
        if (role === "assistant") {
          this.setPendingState("");
          this.enqueueAssistantStream(payload.content, payload.created_at || nowIso());
        } else {
          this.appendMessages([{
            role,
            content: payload.content || "",
            created_at: payload.created_at || nowIso(),
            image_urls: Array.isArray(payload.image_urls) ? payload.image_urls : []
          }]);
        }
        this.refreshStats();
      }
    });
  },

  closeSocket() {
    if (this._wsReconnectTimer) {
      clearTimeout(this._wsReconnectTimer);
      this._wsReconnectTimer = null;
    }
    if (this._pingTimer) {
      clearInterval(this._pingTimer);
      this._pingTimer = null;
    }
    if (this._wsTask) {
      try {
        this._wsTask.close({ code: 1000, reason: "page hide" });
      } catch (e) {
        // ignore
      }
      this._wsTask = null;
    }
  },

  pushNotify(item) {
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const row = {
      id,
      content: item.content,
      createdAt: item.createdAt,
      timeText: fmtTime(item.createdAt)
    };
    const list = [row, ...this.data.notifyCards].slice(0, 4);
    this.setData({ notifyCards: list });
  },

  onToggleToolSteps() {
    this.setData({ toolStepsExpanded: !this.data.toolStepsExpanded });
  },

  onDismissNotify(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({
      notifyCards: this.data.notifyCards.filter((x) => x.id !== id)
    });
  },

  onInput(e) {
    const raw = e.detail.value || "";
    // Enter to send (single newline at the end), align with chat-app behavior.
    if (raw.endsWith("\n")) {
      const cleaned = raw.replace(/\n+$/, "");
      this.setData({ inputText: cleaned });
      if (cleaned.trim()) {
        this.onSend();
      }
      return;
    }
    this.setData({ inputText: raw });
  },

  onOnboardingInput(e) {
    if (this.data.sending || this.data.pendingState) return;
    this.setData({ onboardingInput: e.detail.value || "" });
  },

  onOnboardingUseSuggestion(e) {
    if (this.data.sending || this.data.pendingState) return;
    const text = String(e.currentTarget.dataset.text || "").trim();
    if (!text) return;
    this.setData({ onboardingInput: "", inputText: text }, () => this.onSend());
  },

  onOnboardingSubmit() {
    if (this.data.sending || this.data.pendingState) return;
    const text = String(this.data.onboardingInput || "").trim();
    if (!text) return;
    this.setData({ onboardingInput: "", inputText: text }, () => this.onSend());
  },

  onConfirmSend() {
    this.onSend();
  },

  async onChooseImage() {
    if (!this.data.authed) {
      this.onGoLogin();
      return;
    }
    try {
      const remain = Math.max(0, 6 - this.data.selectedImages.length);
      if (remain <= 0) {
        wx.showToast({ title: "最多上传6张", icon: "none" });
        return;
      }
      const picked = await pickImages(remain);
      this.setData({ selectedImages: [...this.data.selectedImages, ...picked] });
    } catch (err) {
      wx.showToast({ title: err.message || "选择图片失败", icon: "none" });
    }
  },

  onRemoveImage(e) {
    const idx = Number(e.currentTarget.dataset.index);
    if (Number.isNaN(idx)) return;
    const next = [...this.data.selectedImages];
    next.splice(idx, 1);
    this.setData({ selectedImages: next });
  },

  onPreviewImage(e) {
    const idx = Number(e.currentTarget.dataset.index);
    const urls = this.data.selectedImages.map((x) => x.path);
    wx.previewImage({ current: urls[idx], urls });
  },

  onPreviewBubbleImage(e) {
    const current = e.currentTarget.dataset.src;
    wx.previewImage({ current, urls: [current] }); // Simple preview
  },

  async onSend() {
    if (this.data.sending || this._sendingLock) return;
    if (!this.data.authed) {
      this.onGoLogin();
      return;
    }
    const text = (this.data.inputText || "").trim();
    const selectedImagesSnapshot = [...this.data.selectedImages];
    const hasImages = selectedImagesSnapshot.length > 0;
    if (!text && !hasImages) return;
    this._sendingLock = true;
    const submittedContent = text || "[图片]";

    const userMsg = {
      role: "user",
      content: submittedContent,
      created_at: nowIso(),
      image_urls: selectedImagesSnapshot.map((x) => x.path)
    };
    this.appendMessages([userMsg]);
    this.queuePendingUserEcho(submittedContent);

    // Optimistic clear: avoid keeping sent text in input while waiting server response.
    this.setData({ sending: true, inputText: "", selectedImages: [], toolSteps: [], toolStepsExpanded: true, toolStepsDone: false, toolStepsDoneCount: 0 });
    this.setPendingState("thinking");
    try {
      const imageUrls = selectedImagesSnapshot.map((x) => x.dataUrl);
      const res = await sendChat(submittedContent, imageUrls);
      const responses = Array.isArray(res.responses) ? res.responses : [];
      if (!this.data.wsOpen && responses.length === 0) {
        this.setPendingState("");
      }
      // If websocket is not connected, fallback to local append to avoid blank replies.
      if (!this.data.wsOpen) {
        responses.forEach((item) => {
          this.enqueueAssistantStream(item, nowIso());
        });
      }
      this.refreshStats();
      this.refreshProfileState();
    } catch (err) {
      if (isTimeoutError(err) && this.data.wsOpen) {
        // Request timeout does not mean server dropped the turn; if websocket is
        // connected we continue waiting for async push to avoid false "failure".
        this.setPendingState("thinking");
      } else {
        this.setPendingState("");
        // Rollback input on failure to prevent user text loss.
        this.setData({
          inputText: text,
          selectedImages: selectedImagesSnapshot
        });
        wx.showToast({ title: err.message || "发送失败", icon: "none" });
      }
    } finally {
      this._sendingLock = false;
      this.setData({ sending: false });
    }
  },

  async refreshStats() {
    if (!this.data.authed) return;
    try {
      const stats = await fetchLedgerStats(30);
      this.setData({ stats: stats || { total: 0, count: 0 } });
    } catch (e) {
      // ignore refresh errors
    }
  },

  onGoLogin() {
    this.setPendingState("");
    this.setData({ sidebarOpen: false });
    const redirect = encodeURIComponent("/pages/chat/index");
    wx.navigateTo({ url: `/pages/login/index?redirect=${redirect}` });
  },

  onShareAppMessage() {
    return { title: '效率工具 — 记账·提醒·日程', path: '/pages/home/index' };
  },
  onShareTimeline() {
    return { title: '效率工具 — 记账·提醒·日程' };
  }
});
