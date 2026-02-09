const { getToken } = require("../../utils/auth");
const {
  fetchHistory,
  fetchProfile,
  fetchLedgerStats,
  sendChat,
  getWsUrl
} = require("../../utils/http");
const { pickImages } = require("../../utils/image");
const { markdownToRichNodes } = require("../../utils/markdown");

function fmtTime(isoText) {
  if (!isoText) return "";
  const dt = new Date(isoText);
  if (Number.isNaN(dt.getTime())) return "";
  const hh = `${dt.getHours()}`.padStart(2, "0");
  const mm = `${dt.getMinutes()}`.padStart(2, "0");
  return `${hh}:${mm}`;
}

function nowIso() {
  return new Date().toISOString();
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
    this._sendingLock = false;
    this._pendingUserEcho = [];
    this._streamQueue = [];
    this._streaming = false;
    this._streamTimer = null;
  },

  onShow() {
    const authed = this.syncAuthState();
    if (authed) {
      this.loadInitial();
      this.connectSocket();
      return;
    }
    this.closeSocket();
    this.setData({
      profile: { ai_name: "PAI", ai_emoji: "" },
      stats: { total: 0, count: 0 },
      messages: [],
      notifyCards: []
    });
  },

  onHide() {
    this.closeSocket();
    this.stopStream();
  },

  onUnload() {
    this.closeSocket();
    this.stopStream();
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
        this.scrollToBottom();
      });
    } catch (err) {
      wx.showToast({ title: err.message || "加载失败", icon: "none" });
    }
  },

  messageKey(msg) {
    return `${msg.role}|${msg.created_at}|${msg.content}`;
  },

  appendMessages(rows) {
    const next = [...this.data.messages];
    for (const row of rows) {
      const msg = normalizeMessage(row);
      const key = this.messageKey(msg);
      if (this._seenKeys.has(key)) continue;
      this._seenKeys.add(key);
      next.push(msg);
    }
    this.setData({ messages: next }, () => this.scrollToBottom());
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
    const next = [...this.data.messages, placeholder];
    this.setData({ messages: next }, () => this.scrollToBottom());

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
      const rows = [...this.data.messages];
      const row = rows[current.index];
      if (!row) {
        this._streaming = false;
        this.runStreamQueue();
        return;
      }
      rows[current.index] = {
        ...row,
        display_content: partial,
        content_nodes: markdownToRichNodes(partial),
      };
      this.setData({ messages: rows }, () => this.scrollToBottom());
      if (cursor >= fullText.length) {
        this._streaming = false;
        this.runStreamQueue();
        return;
      }
      this._streamTimer = setTimeout(tick, 18);
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

  scrollToBottom() {
    const last = this.data.messages[this.data.messages.length - 1];
    if (!last) return;
    const marker = `msg-${this.data.messages.length - 1}`;
    this.setData({ scrollIntoView: marker });
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
    });

    task.onError(() => {
      this.setData({ wsOpen: false });
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
          this.enqueueAssistantStream(payload.content, payload.created_at || nowIso());
        } else {
          this.appendMessages([{ role, content: payload.content, created_at: payload.created_at || nowIso() }]);
        }
        this.refreshStats();
      }
    });
  },

  closeSocket() {
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
    const hasImages = this.data.selectedImages.length > 0;
    if (!text && !hasImages) return;
    this._sendingLock = true;

    const userMsg = {
      role: "user",
      content: text || "[图片]",
      created_at: nowIso(),
      image_urls: this.data.selectedImages.map((x) => x.path)
    };
    this.appendMessages([userMsg]);
    const payloadText = text || "请帮我识别这张图片";
    this.queuePendingUserEcho(payloadText);
    this.queuePendingUserEcho(text || "[图片]");

    this.setData({ sending: true });
    try {
      const imageUrls = this.data.selectedImages.map((x) => x.dataUrl);
      const res = await sendChat(payloadText, imageUrls);
      const responses = Array.isArray(res.responses) ? res.responses : [];
      // If websocket is not connected, fallback to local append to avoid blank replies.
      if (!this.data.wsOpen) {
        responses.forEach((item) => {
          this.enqueueAssistantStream(item, nowIso());
        });
      }
      this.setData({ inputText: "", selectedImages: [] });
      this.refreshStats();
    } catch (err) {
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
    const redirect = encodeURIComponent("/pages/chat/index");
    wx.navigateTo({ url: `/pages/login/index?redirect=${redirect}` });
  }
});
