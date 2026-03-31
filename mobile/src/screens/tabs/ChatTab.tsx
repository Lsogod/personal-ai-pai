import { Ionicons } from "@expo/vector-icons";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  Dimensions,
  FlatList,
  Image,
  LayoutAnimation,
  Keyboard,
  KeyboardEvent,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  UIManager,
  View,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Markdown from "react-native-markdown-display";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  ChatMessage,
  ConversationItem,
  createConversation,
  deleteConversation,
  fetchConversations,
  fetchHistory,
  fetchProfile,
  getChatWsUrl,
  getClientPlatform,
  getNotificationsWsUrl,
  sendChat,
  switchConversation,
} from "../../lib/api";
import { formatHmLocal, formatMdHmLocal } from "../../lib/date";
import { useAuthStore } from "../../store/auth";
import { colors, radii, shadowMd, shadowSm, spacing, surfaceCard } from "../../design/tokens";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type ChatTabProps = {
  bottomInset: number;
  consumePrefill?: () => string | undefined;
};

type NoticeCard = {
  id: string;
  content: string;
  timeText: string;
};

type RenderableChatMessage = ChatMessage & {
  __streaming?: boolean;
};

/* ------------------------------------------------------------------ */
/*  Quick suggestions for empty state                                  */
/* ------------------------------------------------------------------ */

const SUGGESTIONS = [
  { icon: "receipt-outline" as const, text: "午饭 35 元记一笔" },
  { icon: "calendar-outline" as const, text: "明早 9 点提醒我开会" },
  { icon: "search-outline" as const, text: "这个月花了多少钱？" },
  { icon: "chatbubble-outline" as const, text: "帮我写一段自我介绍" },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function getDisplayText(item: ChatMessage) {
  if (item.content === "[图片]" && Array.isArray(item.image_urls) && item.image_urls.length > 0) {
    return "";
  }
  return item.content || "";
}

function getTimeLabel(dateStr: string, prevDateStr?: string): string | null {
  if (!prevDateStr) return formatMdHmLocal(dateStr);
  const curr = new Date(dateStr).getTime();
  const prev = new Date(prevDateStr).getTime();
  if (curr - prev > 5 * 60 * 1000) return formatMdHmLocal(dateStr);
  return null;
}

/* ------------------------------------------------------------------ */
/*  Typing indicator component                                        */
/* ------------------------------------------------------------------ */

function TypingIndicator() {
  const dots = [useRef(new Animated.Value(0)).current, useRef(new Animated.Value(0)).current, useRef(new Animated.Value(0)).current];

  useEffect(() => {
    const animations = dots.map((dot, i) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(i * 160),
          Animated.timing(dot, { toValue: 1, duration: 320, useNativeDriver: true }),
          Animated.timing(dot, { toValue: 0, duration: 320, useNativeDriver: true }),
        ])
      )
    );
    animations.forEach((a) => a.start());
    return () => animations.forEach((a) => a.stop());
  }, []);

  return (
    <View style={styles.typingRow}>
      <View style={styles.avatarSmall}>
        <Ionicons name="sparkles" size={14} color={colors.primary} />
      </View>
      <View style={styles.typingBubble}>
        {dots.map((dot, i) => (
          <Animated.View
            key={i}
            style={[
              styles.typingDot,
              { opacity: dot.interpolate({ inputRange: [0, 1], outputRange: [0.3, 1] }) },
            ]}
          />
        ))}
      </View>
    </View>
  );
}

function MessageReveal({ children }: { children: React.ReactNode }) {
  const opacity = useRef(new Animated.Value(0)).current;
  const translateY = useRef(new Animated.Value(10)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(opacity, { toValue: 1, duration: 240, useNativeDriver: true }),
      Animated.timing(translateY, { toValue: 0, duration: 240, useNativeDriver: true }),
    ]).start();
  }, [opacity, translateY]);

  return <Animated.View style={{ opacity, transform: [{ translateY }] }}>{children}</Animated.View>;
}

/* ------------------------------------------------------------------ */
/*  Main ChatTab                                                       */
/* ------------------------------------------------------------------ */

export function ChatTab({ bottomInset, consumePrefill }: ChatTabProps) {
  const token = useAuthStore((state) => state.token);
  const queryClient = useQueryClient();
  const flatListRef = useRef<FlatList | null>(null);
  const insets = useSafeAreaInsets();

  const [input, setInput] = useState(() => consumePrefill?.() || "");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [screenError, setScreenError] = useState<string | null>(null);
  const [wsState, setWsState] = useState<"connecting" | "open" | "closed">("connecting");
  const [notifyCards, setNotifyCards] = useState<NoticeCard[]>([]);
  const [streamingReply, setStreamingReply] = useState("");
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  const [composerHeight, setComposerHeight] = useState(86);
  const [showScrollDown, setShowScrollDown] = useState(false);
  const streamBufferRef = useRef("");
  const streamFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollSyncTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const streamingStartedAtRef = useRef<string>(new Date().toISOString());
  const streamCommittedRef = useRef(false);
  const chatSocketRef = useRef<WebSocket | null>(null);
  const chatSocketReadyRef = useRef(false);
  const pendingStreamResolveRef = useRef<((value: { reply: string }) => void) | null>(null);
  const pendingStreamRejectRef = useRef<((reason?: unknown) => void) | null>(null);
  const lastAnimatedMessageCountRef = useRef(0);

  useEffect(() => {
    const prefill = consumePrefill?.();
    if (prefill) {
      setInput(prefill);
    }
  }, [consumePrefill]);

  useEffect(() => {
    if (Platform.OS === "android" && UIManager.setLayoutAnimationEnabledExperimental) {
      UIManager.setLayoutAnimationEnabledExperimental(true);
    }
  }, []);

  useEffect(() => {
    return () => {
      if (streamFlushTimerRef.current !== null) {
        clearTimeout(streamFlushTimerRef.current);
        streamFlushTimerRef.current = null;
      }
      scrollSyncTimersRef.current.forEach((timer) => clearTimeout(timer));
      scrollSyncTimersRef.current = [];
    };
  }, []);

  /* ---- Queries ---- */

  const profileQuery = useQuery({
    queryKey: ["profile"],
    enabled: !!token,
    queryFn: () => fetchProfile(token!),
  });

  const historyQuery = useQuery({
    queryKey: ["history"],
    enabled: !!token,
    queryFn: () => fetchHistory(token!),
    refetchInterval: token ? 30_000 : false,
  });

  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    enabled: !!token,
    queryFn: () => fetchConversations(token!),
  });

  function flushStreamingReplySoon() {
    if (streamFlushTimerRef.current !== null) return;
    streamFlushTimerRef.current = setTimeout(() => {
      streamFlushTimerRef.current = null;
      setStreamingReply(streamBufferRef.current);
    }, 40);
  }

  function scrollToBottom(animated = true) {
    requestAnimationFrame(() => {
      flatListRef.current?.scrollToEnd({ animated });
    });
  }

  function animateLayout() {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
  }

  function runScrollSync(delays: number[], animated = false) {
    scrollSyncTimersRef.current.forEach((timer) => clearTimeout(timer));
    scrollSyncTimersRef.current = delays.map((delayMs) =>
      setTimeout(() => {
        scrollToBottom(animated);
      }, delayMs)
    );
  }

  function clearStreamingState() {
    if (streamFlushTimerRef.current !== null) {
      clearTimeout(streamFlushTimerRef.current);
      streamFlushTimerRef.current = null;
    }
    streamBufferRef.current = "";
    setStreamingReply("");
    streamCommittedRef.current = false;
  }

  async function waitForChatSocketReady(timeoutMs = 1500) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
      const socket = chatSocketRef.current;
      if (socket && chatSocketReadyRef.current && socket.readyState === WebSocket.OPEN) {
        return socket;
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return null;
  }

  function appendAssistantMessage(content: string, createdAt?: string) {
    const text = String(content || "");
    if (!text.trim()) return;
    queryClient.setQueryData<ChatMessage[]>(["history"], (prev = []) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant" && (last.content || "").trim() === text.trim()) {
        return prev;
      }
      return [
        ...prev,
        {
          role: "assistant",
          content: text,
          created_at: createdAt || new Date().toISOString(),
          image_urls: [],
        },
      ];
    });
  }

  function commitStreamedReply(createdAt?: string) {
    if (streamCommittedRef.current) return;
    const reply = streamBufferRef.current;
    if (!reply.trim()) return;
    appendAssistantMessage(reply, createdAt || streamingStartedAtRef.current);
    streamCommittedRef.current = true;
    if (streamFlushTimerRef.current !== null) {
      clearTimeout(streamFlushTimerRef.current);
      streamFlushTimerRef.current = null;
    }
    setStreamingReply("");
  }

  /* ---- WebSocket ---- */

  useEffect(() => {
    if (!token) return;
    let closedManually = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closedManually) return;
      setWsState("connecting");
      socket = new WebSocket(getNotificationsWsUrl(token));
      socket.onopen = () => setWsState("open");
      socket.onerror = () => setWsState("closed");
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (payload?.type === "reminder" && payload?.content) {
            const createdAt = String(payload.created_at || new Date().toISOString());
            const reminder: ChatMessage = {
              role: "assistant",
              content: String(payload.content),
              created_at: createdAt,
              image_urls: [],
            };
            queryClient.setQueryData<ChatMessage[]>(["history"], (prev = []) => [...prev, reminder]);
            setNotifyCards((prev) =>
              [
                {
                  id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                  content: reminder.content,
                  timeText: formatHmLocal(createdAt),
                },
                ...prev,
              ].slice(0, 3)
            );
          }
        } catch {}
      };
      socket.onclose = () => {
        setWsState("closed");
        if (!closedManually) reconnectTimer = setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      closedManually = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [queryClient, token]);

  useEffect(() => {
    const showEvent = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
    const hideEvent = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";

    const handleKeyboardShow = (event: KeyboardEvent) => {
      const windowHeight = Dimensions.get("window").height;
      const overlapByScreenY = windowHeight - (event.endCoordinates?.screenY || windowHeight);
      const nextHeight = Math.max(overlapByScreenY || event.endCoordinates?.height || 0, 0);
      animateLayout();
      setKeyboardHeight(nextHeight);
      setKeyboardOpen(true);
      runScrollSync(Platform.OS === "ios" ? [0, 120, 260, 420] : [0, 80, 180, 320], false);
    };

    const handleKeyboardHide = (_event: KeyboardEvent) => {
      animateLayout();
      setKeyboardHeight(0);
      setKeyboardOpen(false);
      runScrollSync(Platform.OS === "ios" ? [40, 140] : [20, 100], false);
    };

    const showSub = Keyboard.addListener(showEvent, handleKeyboardShow);
    const hideSub = Keyboard.addListener(hideEvent, handleKeyboardHide);
    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, [insets.bottom]);

  useEffect(() => {
    if (!token) return;
    let closedManually = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let socket: WebSocket | null = null;

    const cleanupPending = (reason?: string) => {
      if (pendingStreamRejectRef.current) {
        pendingStreamRejectRef.current(new Error(reason || "聊天连接已断开，请重试。"));
        pendingStreamRejectRef.current = null;
        pendingStreamResolveRef.current = null;
      }
    };

    const connect = () => {
      if (closedManually) return;
      socket = new WebSocket(getChatWsUrl(token));
      chatSocketRef.current = socket;
      chatSocketReadyRef.current = false;

      socket.onopen = () => {
        chatSocketReadyRef.current = true;
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (payload?.type === "message_chunk") {
            const createdAt = String(payload.created_at || new Date().toISOString());
            if (!streamBufferRef.current) {
              streamingStartedAtRef.current = createdAt;
              streamCommittedRef.current = false;
            }
            const chunk = typeof payload.chunk === "string" ? payload.chunk : "";
            if (chunk) {
              streamBufferRef.current += chunk;
              flushStreamingReplySoon();
            }
            if (payload.done === true) {
              const finalReply = streamBufferRef.current;
              if (streamFlushTimerRef.current !== null) {
                clearTimeout(streamFlushTimerRef.current);
                streamFlushTimerRef.current = null;
              }
              setStreamingReply(finalReply);
              commitStreamedReply(createdAt);
              pendingStreamResolveRef.current?.({ reply: finalReply });
              pendingStreamResolveRef.current = null;
              pendingStreamRejectRef.current = null;
              void queryClient.invalidateQueries({ queryKey: ["conversations"] });
            }
            return;
          }

          if (payload?.type === "chat_error" || payload?.ok === false) {
            const errorText = String(payload?.error || "请求失败。");
            pendingStreamRejectRef.current?.(new Error(errorText));
            pendingStreamResolveRef.current = null;
            pendingStreamRejectRef.current = null;
          }
        } catch {}
      };

      socket.onerror = () => {
        chatSocketReadyRef.current = false;
      };

      socket.onclose = () => {
        chatSocketReadyRef.current = false;
        chatSocketRef.current = null;
        cleanupPending("聊天连接已断开，请重试。");
        if (!closedManually) {
          reconnectTimer = setTimeout(connect, 2000);
        }
      };
    };

    connect();
    return () => {
      closedManually = true;
      chatSocketReadyRef.current = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      cleanupPending();
      socket?.close();
      chatSocketRef.current = null;
    };
  }, [queryClient, token]);

  /* ---- Mutations ---- */

  const sendMutation = useMutation({
    mutationFn: async (content: string) => {
      clearStreamingState();
      streamingStartedAtRef.current = new Date().toISOString();

      const socket = await waitForChatSocketReady();
      if (socket && chatSocketReadyRef.current && socket.readyState === WebSocket.OPEN) {
        return await new Promise<{ reply: string }>((resolve, reject) => {
          pendingStreamResolveRef.current = resolve;
          pendingStreamRejectRef.current = reject;
          try {
            socket.send(
              JSON.stringify({
                content,
                image_urls: [],
                source_platform: getClientPlatform(),
                stream: true,
              })
            );
          } catch (error) {
            pendingStreamResolveRef.current = null;
            pendingStreamRejectRef.current = null;
            reject(error);
          }
        });
      }

      const result = await sendChat(content, token!, getClientPlatform());
      return { reply: (result.responses || []).join("\n") };
    },
    onMutate: async (content) => {
      await queryClient.cancelQueries({ queryKey: ["history"] });
      const prev = queryClient.getQueryData<ChatMessage[]>(["history"]) || [];
      queryClient.setQueryData<ChatMessage[]>(["history"], [
        ...prev,
        { role: "user", content, created_at: new Date().toISOString(), image_urls: [] },
      ]);
      setScreenError(null);
      clearStreamingState();
      streamingStartedAtRef.current = new Date().toISOString();
      return { prev };
    },
    onSuccess: async (data) => {
      if (!streamCommittedRef.current) {
        const fallbackReply = streamBufferRef.current.trim() ? streamBufferRef.current : data.reply;
        if (fallbackReply.trim()) {
          appendAssistantMessage(fallbackReply, streamingStartedAtRef.current);
        }
      }
      clearStreamingState();
      setInput("");
      await queryClient.invalidateQueries({ queryKey: ["history"] });
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (error: Error, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(["history"], ctx.prev);
      clearStreamingState();
      setScreenError(error.message);
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => createConversation(undefined, token!),
    onSuccess: async () => {
      setDrawerOpen(false);
      setScreenError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (e: Error) => setScreenError(e.message),
  });

  const switchMutation = useMutation({
    mutationFn: async (id: number) => switchConversation(id, token!),
    onSuccess: async () => {
      setDrawerOpen(false);
      setScreenError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (e: Error) => setScreenError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: number) => deleteConversation(id, token!),
    onSuccess: async () => {
      setScreenError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (e: Error) => setScreenError(e.message),
  });

  /* ---- Derived ---- */

  const activeConversation = useMemo(
    () => conversationsQuery.data?.find((c) => c.active) || null,
    [conversationsQuery.data]
  );

  const baseMessages = historyQuery.data || [];
  const messages: RenderableChatMessage[] = useMemo(() => {
    if (!streamingReply) return baseMessages;
    const lastHistory = baseMessages.length > 0 ? baseMessages[baseMessages.length - 1] : null;
    if (
      lastHistory?.role === "assistant" &&
      (lastHistory.content || "").trim() === streamingReply.trim()
    ) {
      return baseMessages;
    }
    return [
      ...baseMessages,
      {
        role: "assistant",
        content: streamingReply,
        created_at: streamingStartedAtRef.current,
        image_urls: [],
        __streaming: true,
      },
    ];
  }, [baseMessages, streamingReply]);
  const loading = historyQuery.isLoading && messages.length === 0;
  const nickname = profileQuery.data?.nickname || "你";
  const aiEmoji = profileQuery.data?.ai_emoji || "✨";

  useEffect(() => {
    if (messages.length > lastAnimatedMessageCountRef.current) {
      animateLayout();
    }
    lastAnimatedMessageCountRef.current = messages.length;
    if (messages.length === 0 && !streamingReply) return;
    scrollToBottom(!keyboardOpen && !streamingReply);
    if (keyboardOpen) {
      runScrollSync([0, 90], false);
    }
  }, [keyboardOpen, messages.length, streamingReply]);

  useEffect(() => {
    if (!keyboardOpen) return;
    runScrollSync([0, 80], false);
  }, [bottomInset, composerHeight, keyboardHeight, keyboardOpen]);

  /* ---- Handlers ---- */

  function handleVoicePress() {
    Alert.alert(
      "语音输入",
      "当前这版运行在 Expo Go，实时语音识别需要切到 development build 或补后端转写接口。现在可以先用 iPhone 键盘自带听写。"
    );
  }

  async function handleSend(text?: string) {
    const content = (text ?? input).trim();
    if (!content || sendMutation.isPending) return;
    setInput("");
    await sendMutation.mutateAsync(content);
  }

  async function refreshAll() {
    setScreenError(null);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["profile"] }),
      queryClient.invalidateQueries({ queryKey: ["history"] }),
      queryClient.invalidateQueries({ queryKey: ["conversations"] }),
    ]);
  }

  /* ---- Render helpers ---- */

  function renderMessage({ item, index }: { item: RenderableChatMessage; index: number }) {
    const text = getDisplayText(item);
    const isUser = item.role !== "assistant";
    const prevItem = index > 0 ? messages[index - 1] : undefined;
    const timeLabel = getTimeLabel(item.created_at, prevItem?.created_at);
    const hasImages = Array.isArray(item.image_urls) && item.image_urls.length > 0;

    return (
      <MessageReveal>
        <View>
        {timeLabel ? <Text style={styles.timeLabel}>{timeLabel}</Text> : null}

        {isUser ? (
          /* ---- User bubble (right) ---- */
          <View style={styles.userRow}>
            <View style={styles.userBubble}>
              {hasImages ? (
                <View style={styles.imageGrid}>
                  {item.image_urls!.map((url) => (
                    <Image key={url} source={{ uri: url }} style={styles.msgImage} resizeMode="cover" />
                  ))}
                </View>
              ) : null}
              {text ? <Text style={styles.userText}>{text}</Text> : null}
            </View>
          </View>
        ) : (
          /* ---- Assistant bubble (left) ---- */
          <View style={styles.assistantRow}>
            <View style={styles.avatarSmall}>
              <Text style={styles.avatarEmoji}>{aiEmoji}</Text>
            </View>
            <View style={styles.assistantBubble}>
              {hasImages ? (
                <View style={styles.imageGrid}>
                  {item.image_urls!.map((url) => (
                    <Image key={url} source={{ uri: url }} style={styles.msgImage} resizeMode="cover" />
                  ))}
                </View>
              ) : null}
              {text ? (
                item.__streaming ? (
                  <Text style={styles.assistantText}>{text}</Text>
                ) : (
                  <Markdown style={markdownStyles}>{text}</Markdown>
                )
              ) : null}
            </View>
          </View>
        )}
        </View>
      </MessageReveal>
    );
  }

  /* ---- Empty state ---- */

  function renderEmptyState() {
    return (
      <View style={styles.emptyState}>
        <View style={styles.emptyIconWrap}>
          <Text style={styles.emptyEmoji}>{aiEmoji}</Text>
        </View>
        <Text style={styles.emptyTitle}>Hi {nickname}，有什么可以帮你的？</Text>
        <Text style={styles.emptySubtitle}>我是你的 AI 助手，可以帮你记账、设提醒、回答问题</Text>
        <View style={styles.suggestionsGrid}>
          {SUGGESTIONS.map((s) => (
            <Pressable key={s.text} style={styles.suggestionChip} onPress={() => void handleSend(s.text)}>
              <Ionicons name={s.icon} size={16} color={colors.primary} />
              <Text style={styles.suggestionText}>{s.text}</Text>
            </Pressable>
          ))}
        </View>
      </View>
    );
  }

  /* ================================================================ */
  /*  RENDER                                                          */
  /* ================================================================ */

  const composerOffset = keyboardOpen ? keyboardHeight : bottomInset;
  const viewportInset = composerHeight + composerOffset;

  return (
    <View style={styles.page}>
      {/* ---- Conversation Drawer ---- */}
      <Modal visible={drawerOpen} transparent animationType="fade" onRequestClose={() => setDrawerOpen(false)}>
        <View style={styles.modalWrap}>
          <Pressable style={styles.drawerMask} onPress={() => setDrawerOpen(false)} />
          <View style={[styles.drawerPanel, { paddingTop: insets.top + 12 }]}>
            <View style={styles.drawerHeader}>
              <Text style={styles.drawerTitle}>对话记录</Text>
              <Pressable style={styles.drawerNewBtn} onPress={() => void createMutation.mutateAsync()}>
                <Ionicons name="add" size={18} color="#fff" />
                <Text style={styles.drawerNewText}>新对话</Text>
              </Pressable>
            </View>

            <ScrollView contentContainerStyle={styles.drawerList} showsVerticalScrollIndicator={false}>
              {conversationsQuery.isLoading ? (
                <ActivityIndicator style={{ marginTop: 40 }} color={colors.primary} />
              ) : null}
              {!conversationsQuery.isLoading && (conversationsQuery.data || []).length === 0 ? (
                <Text style={styles.drawerEmpty}>还没有对话记录</Text>
              ) : null}
              {(conversationsQuery.data || []).map((item: ConversationItem) => (
                <Pressable
                  key={item.id}
                  style={[styles.convItem, item.active && styles.convItemActive]}
                  onPress={() => void switchMutation.mutateAsync(item.id)}
                >
                  <View style={styles.convBody}>
                    <Text numberOfLines={1} style={[styles.convTitle, item.active && styles.convTitleActive]}>
                      {item.title}
                    </Text>
                    <Text numberOfLines={1} style={styles.convSummary}>
                      {item.summary || "暂无摘要"}
                    </Text>
                    <Text style={styles.convTime}>{formatMdHmLocal(item.last_message_at)}</Text>
                  </View>
                  <Pressable
                    style={styles.convDeleteBtn}
                    onPress={(e) => {
                      e.stopPropagation?.();
                      void deleteMutation.mutateAsync(item.id);
                    }}
                    hitSlop={8}
                  >
                    <Ionicons name="trash-outline" size={16} color={colors.text4} />
                  </Pressable>
                </Pressable>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* ---- Header ---- */}
      <View style={[styles.header, { paddingTop: insets.top + 4 }]}>
        <View style={styles.headerRow}>
          <View style={styles.headerLeft}>
            <View style={styles.headerAvatar}>
              <Text style={{ fontSize: 18 }}>{aiEmoji}</Text>
            </View>
            <View>
              <Text style={styles.headerTitle}>PAI</Text>
              <View style={styles.headerStatusRow}>
                <View style={[styles.statusDot, wsState === "open" && styles.statusDotOn]} />
                <Text style={styles.headerStatus}>
                  {wsState === "open" ? "在线" : wsState === "connecting" ? "连接中..." : "离线"}
                </Text>
              </View>
            </View>
          </View>
          <View style={styles.headerActions}>
            <Pressable style={styles.headerIconBtn} onPress={() => void createMutation.mutateAsync()}>
              <Ionicons name="create-outline" size={20} color={colors.text2} />
            </Pressable>
            <Pressable style={styles.headerIconBtn} onPress={() => setDrawerOpen(true)}>
              <Ionicons name="chatbubbles-outline" size={20} color={colors.text2} />
            </Pressable>
          </View>
        </View>
      </View>

      {/* ---- Notification cards ---- */}
      {notifyCards.length > 0 ? (
        <View style={styles.notifyStack}>
          {notifyCards.map((card) => (
            <View key={card.id} style={styles.notifyCard}>
              <View style={styles.notifyHead}>
                <View style={styles.notifyTitleRow}>
                  <Ionicons name="notifications" size={13} color="#fff" />
                  <Text style={styles.notifyTitle}>日程提醒</Text>
                </View>
                <Pressable onPress={() => setNotifyCards((p) => p.filter((c) => c.id !== card.id))}>
                  <Ionicons name="close" size={15} color="rgba(255,255,255,0.7)" />
                </Pressable>
              </View>
              <Text style={styles.notifyBody}>{card.content}</Text>
            </View>
          ))}
        </View>
      ) : null}

      {/* ---- Error ---- */}
      {screenError ? (
        <Pressable style={styles.errorBox} onPress={() => setScreenError(null)}>
          <Text style={styles.errorText}>{screenError}</Text>
          <Ionicons name="close-circle" size={16} color={colors.danger} />
        </Pressable>
      ) : null}

      <View style={styles.chatArea}>
        <View style={[styles.messageViewport, { bottom: viewportInset }]}>
          {loading ? (
            <View style={styles.loadingWrap}>
              <ActivityIndicator size="large" color={colors.primary} />
            </View>
          ) : messages.length === 0 ? (
            renderEmptyState()
          ) : (
            <FlatList
              ref={flatListRef}
              style={styles.messageListView}
              data={messages}
              keyExtractor={(item, i) => `${item.created_at}-${i}`}
              renderItem={renderMessage}
              contentContainerStyle={styles.messageList}
              showsVerticalScrollIndicator={false}
              scrollIndicatorInsets={{ bottom: 20 }}
              onContentSizeChange={() => scrollToBottom(!keyboardOpen && !streamingReply)}
              onLayout={() => scrollToBottom(false)}
              onScroll={(e) => {
                const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
                const distanceFromBottom = contentSize.height - layoutMeasurement.height - contentOffset.y;
                setShowScrollDown(distanceFromBottom > 120);
              }}
              scrollEventThrottle={80}
              ListFooterComponent={sendMutation.isPending && !streamingReply ? <TypingIndicator /> : null}
              keyboardShouldPersistTaps="handled"
            />
          )}
        </View>

        {showScrollDown && messages.length > 0 ? (
          <Pressable
            style={[styles.scrollDownBtn, { bottom: composerOffset + composerHeight + 8 }]}
            onPress={() => { scrollToBottom(true); setShowScrollDown(false); }}
          >
            <Ionicons name="chevron-down" size={20} color={colors.primary} />
          </Pressable>
        ) : null}

        <View
          style={[
            styles.composerDock,
            {
              bottom: composerOffset,
              paddingBottom: keyboardOpen ? 12 : 14,
            },
          ]}
          onLayout={(event) => {
            const nextHeight = Math.ceil(event.nativeEvent.layout.height);
            if (nextHeight > 0 && Math.abs(nextHeight - composerHeight) > 2) {
              setComposerHeight(nextHeight);
            }
          }}
        >
          <View style={styles.composer}>
            <Pressable style={styles.voiceBtn} onPress={handleVoicePress}>
              <Ionicons name="mic-outline" size={18} color={colors.primary} />
            </Pressable>
            <TextInput
              value={input}
              onChangeText={setInput}
              multiline
              placeholder="跟 PAI 说点什么..."
              placeholderTextColor={colors.text4}
              style={styles.composerInput}
              onFocus={() => {
                runScrollSync([80, 180, 320], false);
              }}
              onSubmitEditing={() => void handleSend()}
              blurOnSubmit={false}
            />
            <Pressable
              style={[styles.sendBtn, (!input.trim() || sendMutation.isPending) && styles.sendBtnDisabled]}
              disabled={!input.trim() || sendMutation.isPending}
              onPress={() => void handleSend()}
            >
              <Ionicons name="arrow-up" size={18} color="#fff" />
            </Pressable>
          </View>
        </View>
      </View>
    </View>
  );
}

/* ================================================================== */
/*  Styles                                                            */
/* ================================================================== */

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  chatArea: {
    flex: 1,
    position: "relative",
  },
  messageViewport: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },

  /* ---- Header ---- */
  header: {
    paddingHorizontal: spacing.pageX,
    paddingBottom: 12,
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.borderLight,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  headerLeft: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  headerAvatar: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.primaryLight,
    alignItems: "center",
    justifyContent: "center",
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.text,
  },
  headerStatusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    marginTop: 1,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: colors.text4,
  },
  statusDotOn: {
    backgroundColor: colors.accent,
  },
  headerStatus: {
    fontSize: 12,
    color: colors.text3,
  },
  headerActions: {
    flexDirection: "row",
    gap: 4,
  },
  headerIconBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
  },

  /* ---- Notifications ---- */
  notifyStack: {
    paddingHorizontal: spacing.pageX,
    paddingTop: 10,
    gap: 8,
  },
  notifyCard: {
    backgroundColor: "rgba(26,29,38,0.92)",
    borderRadius: radii.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  notifyHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 6,
  },
  notifyTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
  },
  notifyTitle: {
    fontSize: 12,
    fontWeight: "600",
    color: "#fff",
  },
  notifyBody: {
    fontSize: 14,
    lineHeight: 20,
    color: "rgba(255,255,255,0.9)",
  },

  /* ---- Error ---- */
  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginHorizontal: spacing.pageX,
    marginTop: 8,
    borderRadius: radii.sm,
    backgroundColor: colors.dangerLight,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  errorText: {
    flex: 1,
    fontSize: 13,
    color: colors.danger,
    marginRight: 8,
  },

  /* ---- Loading ---- */
  loadingWrap: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },

  /* ---- Empty state ---- */
  emptyState: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 32,
    paddingBottom: 20,
    gap: 12,
  },
  emptyIconWrap: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: colors.primaryLight,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 4,
  },
  emptyEmoji: {
    fontSize: 32,
  },
  emptyTitle: {
    fontSize: 20,
    fontWeight: "700",
    color: colors.text,
    textAlign: "center",
  },
  emptySubtitle: {
    fontSize: 14,
    lineHeight: 21,
    color: colors.text3,
    textAlign: "center",
    marginBottom: 8,
  },
  suggestionsGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  suggestionChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: radii.full,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  suggestionText: {
    fontSize: 13,
    fontWeight: "600",
    color: colors.text2,
  },

  /* ---- Messages ---- */
  messageListView: {
    flex: 1,
  },
  messageList: {
    flexGrow: 1,
    paddingHorizontal: spacing.pageX,
    paddingTop: 12,
    paddingBottom: 18,
  },
  timeLabel: {
    textAlign: "center",
    fontSize: 12,
    color: colors.text4,
    marginVertical: 12,
  },

  /* User bubble */
  userRow: {
    flexDirection: "row",
    justifyContent: "flex-end",
    marginBottom: 10,
    paddingLeft: 52,
  },
  userBubble: {
    maxWidth: "85%",
    backgroundColor: colors.primary,
    borderRadius: 20,
    borderBottomRightRadius: 6,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  userText: {
    fontSize: 16,
    lineHeight: 23,
    color: "#fff",
  },

  /* Assistant bubble */
  assistantRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    marginBottom: 10,
    paddingRight: 52,
    gap: 8,
  },
  avatarSmall: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.primaryLight,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 2,
  },
  avatarEmoji: {
    fontSize: 16,
  },
  assistantBubble: {
    maxWidth: "85%",
    backgroundColor: colors.surface,
    borderRadius: 20,
    borderBottomLeftRadius: 6,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.borderLight,
  },
  assistantText: {
    fontSize: 16,
    lineHeight: 23,
    color: colors.text,
  },

  /* Images */
  imageGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 8,
  },
  msgImage: {
    width: 140,
    height: 140,
    borderRadius: 12,
    backgroundColor: colors.borderLight,
  },

  /* Typing indicator */
  typingRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    paddingRight: 52,
    gap: 8,
    marginBottom: 10,
    paddingHorizontal: spacing.pageX,
  },
  typingBubble: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: colors.surface,
    borderRadius: 20,
    borderBottomLeftRadius: 6,
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.borderLight,
  },
  typingDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.text3,
  },

  /* ---- Scroll to bottom ---- */
  scrollDownBtn: {
    position: "absolute",
    right: 16,
    zIndex: 11,
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderLight,
    alignItems: "center",
    justifyContent: "center",
    ...shadowSm,
  },

  /* ---- Composer ---- */
  composerDock: {
    position: "absolute",
    left: 0,
    right: 0,
    zIndex: 10,
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 10,
    backgroundColor: colors.bg,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.borderLight,
  },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 10,
    minHeight: 58,
    paddingLeft: 10,
    paddingRight: 10,
    paddingVertical: 8,
    ...surfaceCard,
  },
  voiceBtn: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primaryLight,
  },
  composerInput: {
    flex: 1,
    minHeight: 40,
    maxHeight: 120,
    paddingHorizontal: 2,
    paddingVertical: 8,
    fontSize: 16,
    lineHeight: 22,
    color: colors.text,
  },
  sendBtn: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primary,
  },
  sendBtnDisabled: {
    opacity: 0.35,
  },

  /* ---- Drawer ---- */
  modalWrap: {
    flex: 1,
    flexDirection: "row",
  },
  drawerMask: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  drawerPanel: {
    width: "80%",
    maxWidth: 340,
    backgroundColor: colors.surface,
    ...shadowMd,
  },
  drawerHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 18,
    paddingBottom: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.borderLight,
  },
  drawerTitle: {
    fontSize: 18,
    fontWeight: "700",
    color: colors.text,
  },
  drawerNewBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: radii.full,
    backgroundColor: colors.primary,
  },
  drawerNewText: {
    fontSize: 13,
    fontWeight: "600",
    color: "#fff",
  },
  drawerList: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    gap: 8,
  },
  drawerEmpty: {
    paddingTop: 40,
    textAlign: "center",
    color: colors.text3,
    fontSize: 14,
  },
  convItem: {
    flexDirection: "row",
    alignItems: "center",
    padding: 14,
    borderRadius: radii.md,
    backgroundColor: colors.bg,
  },
  convItemActive: {
    backgroundColor: colors.primaryLight,
    borderWidth: 1,
    borderColor: "rgba(79,110,247,0.15)",
  },
  convBody: {
    flex: 1,
    gap: 4,
  },
  convTitle: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.text,
  },
  convTitleActive: {
    color: colors.primary,
  },
  convSummary: {
    fontSize: 12,
    color: colors.text3,
  },
  convTime: {
    fontSize: 11,
    color: colors.text4,
    marginTop: 2,
  },
  convDeleteBtn: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
  },
});

const markdownStyles = StyleSheet.create({
  body: {
    marginTop: 0,
    marginBottom: 0,
    color: colors.text,
    fontSize: 16,
    lineHeight: 23,
  },
  heading1: {
    marginTop: 0,
    marginBottom: 10,
    fontSize: 20,
    lineHeight: 26,
    fontWeight: "800",
    color: colors.text,
  },
  heading2: {
    marginTop: 2,
    marginBottom: 8,
    fontSize: 18,
    lineHeight: 24,
    fontWeight: "800",
    color: colors.text,
  },
  heading3: {
    marginTop: 2,
    marginBottom: 8,
    fontSize: 16,
    lineHeight: 22,
    fontWeight: "800",
    color: colors.text,
  },
  paragraph: {
    marginTop: 0,
    marginBottom: 8,
    color: colors.text,
    fontSize: 16,
    lineHeight: 23,
  },
  bullet_list: {
    marginTop: 0,
    marginBottom: 8,
  },
  ordered_list: {
    marginTop: 0,
    marginBottom: 8,
  },
  list_item: {
    marginTop: 0,
    marginBottom: 4,
    color: colors.text,
  },
  bullet_list_icon: {
    color: colors.primary,
    marginRight: 6,
  },
  bullet_list_content: {
    color: colors.text,
    fontSize: 16,
    lineHeight: 23,
  },
  ordered_list_icon: {
    color: colors.primary,
  },
  ordered_list_content: {
    color: colors.text,
    fontSize: 16,
    lineHeight: 23,
  },
  strong: {
    fontWeight: "800",
    color: colors.text,
  },
  em: {
    fontStyle: "italic",
    color: colors.text,
  },
  code_inline: {
    backgroundColor: colors.bg,
    color: colors.primaryDark,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 8,
    overflow: "hidden",
  },
  code_block: {
    backgroundColor: colors.bg,
    color: colors.text,
    borderRadius: 12,
    padding: 12,
    marginTop: 2,
    marginBottom: 8,
  },
  fence: {
    backgroundColor: colors.bg,
    color: colors.text,
    borderRadius: 12,
    padding: 12,
    marginTop: 2,
    marginBottom: 8,
  },
  blockquote: {
    borderLeftWidth: 3,
    borderLeftColor: colors.primary,
    backgroundColor: colors.bg,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginTop: 2,
    marginBottom: 8,
  },
});
