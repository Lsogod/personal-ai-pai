import { Ionicons } from "@expo/vector-icons";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
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

  useEffect(() => {
    const prefill = consumePrefill?.();
    if (prefill) {
      setInput(prefill);
    }
  }, [consumePrefill]);

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

  /* ---- Mutations ---- */

  const sendMutation = useMutation({
    mutationFn: async (content: string) => sendChat(content, token!),
    onMutate: async (content) => {
      await queryClient.cancelQueries({ queryKey: ["history"] });
      const prev = queryClient.getQueryData<ChatMessage[]>(["history"]) || [];
      queryClient.setQueryData<ChatMessage[]>(["history"], [
        ...prev,
        { role: "user", content, created_at: new Date().toISOString(), image_urls: [] },
      ]);
      setScreenError(null);
      return { prev };
    },
    onSuccess: (data) => {
      const msgs = (data.responses || []).map((c) => ({
        role: "assistant",
        content: c,
        created_at: new Date().toISOString(),
        image_urls: [],
      }));
      queryClient.setQueryData<ChatMessage[]>(["history"], (prev = []) => [...prev, ...msgs]);
      setInput("");
      void queryClient.invalidateQueries({ queryKey: ["history"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (error: Error, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(["history"], ctx.prev);
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
  const messages = historyQuery.data || [];
  const loading = historyQuery.isLoading && messages.length === 0;
  const nickname = profileQuery.data?.nickname || "你";
  const aiEmoji = profileQuery.data?.ai_emoji || "✨";

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

  function renderMessage({ item, index }: { item: ChatMessage; index: number }) {
    const text = getDisplayText(item);
    const isUser = item.role !== "assistant";
    const prevItem = index > 0 ? messages[index - 1] : undefined;
    const timeLabel = getTimeLabel(item.created_at, prevItem?.created_at);
    const hasImages = Array.isArray(item.image_urls) && item.image_urls.length > 0;

    return (
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
              {text ? <Markdown style={markdownStyles}>{text}</Markdown> : null}
            </View>
          </View>
        )}
      </View>
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

      <KeyboardAvoidingView
        style={styles.chatArea}
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        keyboardVerticalOffset={0}
      >
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
            onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: true })}
            onLayout={() => flatListRef.current?.scrollToEnd({ animated: false })}
            ListFooterComponent={sendMutation.isPending ? <TypingIndicator /> : null}
            keyboardShouldPersistTaps="handled"
          />
        )}

        <View style={[styles.composerDock, { paddingBottom: Math.max(bottomInset + 8, 14) }]}>
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
      </KeyboardAvoidingView>
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
    paddingBottom: 16,
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

  /* ---- Composer ---- */
  composerDock: {
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
