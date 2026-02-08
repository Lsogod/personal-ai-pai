const { getToken } = require("../../utils/auth");
const {
  fetchHistory,
  fetchProfile,
  fetchLedgerStats,
  sendChat,
  getWsUrl
} = require("../../utils/http");
const { pickImages } = require("../../utils/image");

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
  return {
    role,
    content: item.content || "",
    created_at: item.created_at || nowIso(),
    image_urls: Array.isArray(item.image_urls) ? item.image_urls : []
  };
}

Page({
  data: {
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
    this.ensureLogin();
  },

  onShow() {
    this.ensureLogin();
    this.loadInitial();
    this.connectSocket();
  },

  onHide() {
    this.closeSocket();
  },

  onUnload() {
    this.closeSocket();
  },

  ensureLogin() {
    const token = getToken();
    if (!token) {
      wx.reLaunch({ url: "/pages/login/index" });
      return false;
    }
    getApp().globalData.token = token;
    return true;
  },

  async loadInitial() {
    if (!this.ensureLogin()) return;
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

  scrollToBottom() {
    const last = this.data.messages[this.data.messages.length - 1];
    if (!last) return;
    const marker = `msg-${this.data.messages.length - 1}`;
    this.setData({ scrollIntoView: marker });
  },

  connectSocket() {
    if (!this.ensureLogin()) return;
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
        this.appendMessages([{ role, content: payload.content, created_at: payload.created_at || nowIso() }]);
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
    this.setData({ inputText: e.detail.value || "" });
  },

  async onChooseImage() {
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

  async onSend() {
    if (this.data.sending) return;
    const text = (this.data.inputText || "").trim();
    const hasImages = this.data.selectedImages.length > 0;
    if (!text && !hasImages) return;

    const userMsg = {
      role: "user",
      content: text || "[图片]",
      created_at: nowIso(),
      image_urls: this.data.selectedImages.map((x) => x.path)
    };
    this.appendMessages([userMsg]);

    this.setData({ sending: true });
    try {
      const payloadText = text || "请帮我识别这张图片";
      const imageUrls = this.data.selectedImages.map((x) => x.dataUrl);
      const res = await sendChat(payloadText, imageUrls);
      const responses = Array.isArray(res.responses) ? res.responses : [];
      this.appendMessages(
        responses.map((item) => ({
          role: "assistant",
          content: item,
          created_at: nowIso()
        }))
      );
      this.setData({ inputText: "", selectedImages: [] });
      this.refreshStats();
    } catch (err) {
      wx.showToast({ title: err.message || "发送失败", icon: "none" });
    } finally {
      this.setData({ sending: false });
    }
  },

  async refreshStats() {
    try {
      const stats = await fetchLedgerStats(30);
      this.setData({ stats: stats || { total: 0, count: 0 } });
    } catch (e) {
      // ignore refresh errors
    }
  }
});
