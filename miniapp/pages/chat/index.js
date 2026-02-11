const { getToken } = require("../../utils/auth");
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
const { pickImages } = require("../../utils/image");
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

function normalizeMessage(item) {
  const role = item.role === "assistant" ? "assistant" : "user";
  const content = item.content || "";
  return {
    role,
    content,
    display_content: content,
    content_nodes: role === "assistant" ? markdownToRichNodes(content) : "",
    created_at: item.created_at || nowIso(),
    timeText: fmtTime(item.created_at || nowIso()),
    image_urls: Array.isArray(item.image_urls) ? item.image_urls : []
  };
}

function normText(value) {
  return String(value || "").trim();
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
    inputText: "",
    selectedImages: [],
    sending: false,
    wsOpen: false,
    notifyCards: []
  },

  onLoad() {
    this._wsTask = null;
    this._pingTimer = null;
    this._seenKeys = new Set();
    this._seenReminderKeys = new Set();
    this._sendingLock = false;
    this._pendingUserEcho = [];
    this._streamQueue = [];
    this._streaming = false;
    this._streamTimer = null;
    this._scrollTimers = [];
    this._pendingReplyTimer = null;
    this._pendingNonce = 0;
  },

  onShow() {
    const authed = this.syncAuthState();
    if (authed) {
      this.loadInitial();
      this.loadConversations();
      this.connectSocket();
      this.scrollToBottom(true);
      // 从首页快捷指令跳转过来
      const app = getApp();
      if (app.globalData.pendingCmd) {
        const cmd = app.globalData.pendingCmd;
        app.globalData.pendingCmd = "";
        this.setData({ inputText: cmd });
      }
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
    this.clearScrollRetry();
    this.clearPendingReplyTimer();
  },

  onUnload() {
    this.closeSocket();
    this.stopStream();
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

  async loadInitial() {
    if (!this.data.authed) return;
    try {
      const [profile, history, stats] = await Promise.all([
        fetchProfile(),
        fetchHistory(),
        fetchLedgerStats(30)
      ]);
      const messages = (history || []).map(normalizeMessage);
      this._seenKeys.clear();
      messages.forEach((m) => this._seenKeys.add(this.messageKey(m)));
      this.setData({
        profile,
        stats: stats || { total: 0, count: 0 },
        messages
      }, () => {
        this.scrollToBottom(true);
      });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
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
    return `${msg.role}|${msg.created_at}|${msg.content}`;
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

  appendMessages(rows) {
    const newMsgs = [];
    let hasAssistant = false;
    for (const row of rows) {
      const msg = normalizeMessage(row);
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

    task.onClose(() => {
      this.setData({ wsOpen: false });
      if (this._pingTimer) {
        clearInterval(this._pingTimer);
        this._pingTimer = null;
      }
      this._wsTask = null;
      // 自动重连（3秒后），避免网络波动导致断连
      if (this.data.authed && !this._wsReconnectTimer) {
        this._wsReconnectTimer = setTimeout(() => {
          this._wsReconnectTimer = null;
          if (this.data.authed && !this._wsTask) this.connectSocket();
        }, 3000);
      }
    });

    task.onError(() => {
      this.setData({ wsOpen: false });
      this._wsTask = null;
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

      if (payload.type === "message" && payload.content) {
        const role = payload.role === "assistant" ? "assistant" : "user";
        if (role === "user" && this.consumePendingUserEcho(payload.content)) {
          return;
        }
        if (role === "assistant") {
          this.setPendingState("");
          this.enqueueAssistantStream(payload.content, payload.created_at || nowIso());
        } else {
          this.appendMessages([{ role, content: payload.content, created_at: payload.created_at || nowIso() }]);
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

  onUseTemplate(e) {
    const text = String(e.currentTarget.dataset.template || "").trim();
    if (!text) return;
    this.setData({ inputText: text });
    this.scrollToBottom();
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

    const userMsg = {
      role: "user",
      content: text || "[图片]",
      created_at: nowIso(),
      image_urls: selectedImagesSnapshot.map((x) => x.path)
    };
    this.appendMessages([userMsg]);
    const payloadText = text || "识别图片";
    this.queuePendingUserEcho(payloadText);
    this.queuePendingUserEcho(text || "[图片]");

    // Optimistic clear: avoid keeping sent text in input while waiting server response.
    this.setData({ sending: true, inputText: "", selectedImages: [] });
    this.setPendingState("thinking");
    try {
      const imageUrls = selectedImagesSnapshot.map((x) => x.dataUrl);
      const res = await sendChat(payloadText, imageUrls);
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
    } catch (err) {
      this.setPendingState("");
      // Rollback input on failure to prevent user text loss.
      this.setData({
        inputText: text,
        selectedImages: selectedImagesSnapshot
      });
      wx.showToast({ title: err.message || "发送失败", icon: "none" });
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
