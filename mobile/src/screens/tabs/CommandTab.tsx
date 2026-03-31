import { Ionicons } from "@expo/vector-icons";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ChatMessage,
  ConversationItem,
  createConversation,
  deleteConversation,
  fetchConversations,
  fetchHistory,
  fetchLedgerStats,
  fetchProfile,
  getNotificationsWsUrl,
  sendChat,
  switchConversation,
} from "../../lib/api";
import { formatHmLocal, formatMdHmLocal } from "../../lib/date";
import { useAuthStore } from "../../store/auth";
import { colors, radii, shadowMd, shadowSm, spacing, surfaceCard } from "../../design/tokens";
import { useSafeAreaInsets } from "react-native-safe-area-context";

type CommandTabProps = {
  bottomInset: number;
};

type NoticeCard = {
  id: string;
  content: string;
  timeText: string;
};

function getDisplayText(item: ChatMessage) {
  if (item.content === "[图片]" && Array.isArray(item.image_urls) && item.image_urls.length > 0) {
    return "";
  }
  return item.content || "";
}

export function CommandTab({ bottomInset }: CommandTabProps) {
  const token = useAuthStore((state) => state.token);
  const queryClient = useQueryClient();
  const scrollRef = useRef<ScrollView | null>(null);
  const insets = useSafeAreaInsets();

  const [input, setInput] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [screenError, setScreenError] = useState<string | null>(null);
  const [wsState, setWsState] = useState<"connecting" | "open" | "closed">("connecting");
  const [notifyCards, setNotifyCards] = useState<NoticeCard[]>([]);

  const profileQuery = useQuery({
    queryKey: ["profile"],
    enabled: !!token,
    queryFn: () => fetchProfile(token!),
  });

  const statsQuery = useQuery({
    queryKey: ["stats", "month"],
    enabled: !!token,
    queryFn: () => fetchLedgerStats(token!, "month"),
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

  useEffect(() => {
    const timer = setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 50);
    return () => clearTimeout(timer);
  }, [historyQuery.data]);

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
            setNotifyCards((prev) => [
              {
                id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                content: reminder.content,
                timeText: formatHmLocal(createdAt),
              },
              ...prev,
            ].slice(0, 3));
          }
        } catch {
          // Ignore malformed notification payloads.
        }
      };
      socket.onclose = () => {
        setWsState("closed");
        if (closedManually) return;
        reconnectTimer = setTimeout(connect, 3000);
      };
    };

    connect();

    return () => {
      closedManually = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [queryClient, token]);

  const sendMutation = useMutation({
    mutationFn: async (content: string) => sendChat(content, token!),
    onMutate: async (content) => {
      await queryClient.cancelQueries({ queryKey: ["history"] });
      const previousHistory = queryClient.getQueryData<ChatMessage[]>(["history"]) || [];
      queryClient.setQueryData<ChatMessage[]>(["history"], [
        ...previousHistory,
        {
          role: "user",
          content,
          created_at: new Date().toISOString(),
          image_urls: [],
        },
      ]);
      setScreenError(null);
      return { previousHistory };
    },
    onSuccess: (data) => {
      const assistantMessages = (data.responses || []).map((content) => ({
        role: "assistant",
        content,
        created_at: new Date().toISOString(),
        image_urls: [],
      }));
      queryClient.setQueryData<ChatMessage[]>(["history"], (prev = []) => [...prev, ...assistantMessages]);
      setInput("");
      void queryClient.invalidateQueries({ queryKey: ["history"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (error: Error, _variables, context) => {
      if (context?.previousHistory) {
        queryClient.setQueryData(["history"], context.previousHistory);
      }
      setScreenError(error.message);
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => createConversation(undefined, token!),
    onSuccess: async () => {
      setSidebarOpen(false);
      setScreenError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (error: Error) => setScreenError(error.message),
  });

  const switchMutation = useMutation({
    mutationFn: async (conversationId: number) => switchConversation(conversationId, token!),
    onSuccess: async () => {
      setSidebarOpen(false);
      setScreenError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (error: Error) => setScreenError(error.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (conversationId: number) => deleteConversation(conversationId, token!),
    onSuccess: async () => {
      setScreenError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (error: Error) => setScreenError(error.message),
  });

  const activeConversation = useMemo(
    () => conversationsQuery.data?.find((item) => item.active) || null,
    [conversationsQuery.data]
  );

  const messages = historyQuery.data || [];
  const loading = historyQuery.isLoading && messages.length === 0;
  const wsText = wsState === "open" ? "在线" : wsState === "connecting" ? "连接中" : "离线";

  async function refreshAll() {
    setScreenError(null);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["profile"] }),
      queryClient.invalidateQueries({ queryKey: ["stats", "month"] }),
      queryClient.invalidateQueries({ queryKey: ["history"] }),
      queryClient.invalidateQueries({ queryKey: ["conversations"] }),
    ]);
  }

  async function handleSend() {
    const content = input.trim();
    if (!content || sendMutation.isPending) return;
    await sendMutation.mutateAsync(content);
  }

  return (
    <View style={styles.page}>
      <Modal visible={sidebarOpen} transparent animationType="fade" onRequestClose={() => setSidebarOpen(false)}>
        <View style={styles.modalWrap}>
          <Pressable style={styles.sidebarMask} onPress={() => setSidebarOpen(false)} />
          <View style={[styles.sidebarPanel, { paddingTop: insets.top + 8 }]}>
            <View style={styles.sidebarHeader}>
              <Text style={styles.sidebarTitle}>历史记录</Text>
              <Pressable style={styles.sidebarAddBtn} onPress={() => void createMutation.mutateAsync()}>
                <Text style={styles.sidebarAddText}>＋ 新建</Text>
              </Pressable>
            </View>

            <ScrollView contentContainerStyle={styles.sidebarList}>
              {conversationsQuery.isLoading ? <Text style={styles.sidebarEmpty}>加载中...</Text> : null}
              {!conversationsQuery.isLoading && (conversationsQuery.data || []).length === 0 ? (
                <Text style={styles.sidebarEmpty}>暂无历史记录</Text>
              ) : null}
              {(conversationsQuery.data || []).map((item: ConversationItem) => (
                <View key={item.id} style={[styles.convItem, item.active && styles.convItemActive]}>
                  <Pressable style={styles.convBody} onPress={() => void switchMutation.mutateAsync(item.id)}>
                    <Text numberOfLines={1} style={styles.convTitle}>
                      {item.title}
                    </Text>
                    <Text numberOfLines={2} style={styles.convSummary}>
                      {item.summary || "暂无摘要"}
                    </Text>
                    <Text style={styles.convTime}>{formatMdHmLocal(item.last_message_at)}</Text>
                  </Pressable>
                  <Pressable style={styles.convDelete} onPress={() => void deleteMutation.mutateAsync(item.id)}>
                    <Ionicons name="trash-outline" size={18} color={colors.text3} />
                  </Pressable>
                </View>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>

      <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <View style={styles.headerRow}>
          <View style={styles.headerLeft}>
            <Text style={styles.headerTitle}>指令面板</Text>
            <View style={[styles.statusDot, wsState === "open" && styles.statusDotOn]} />
          </View>
          <Pressable style={styles.historyBtn} onPress={() => setSidebarOpen(true)}>
            <Ionicons name="time-outline" size={16} color={colors.text2} />
            <Text style={styles.historyBtnText}>记录</Text>
          </Pressable>
        </View>
        <View style={styles.headerSub}>
          <View style={styles.headerPillRow}>
            <View style={styles.subscribePill}>
              <Text style={styles.subscribeLabel}>原生推送</Text>
              <Text style={styles.subscribeBadge}>待接入</Text>
            </View>
            <View style={styles.statPill}>
              <Text style={styles.statPillText}>¥{Number(statsQuery.data?.total || 0).toFixed(0)}</Text>
            </View>
            <View style={styles.statPill}>
              <Text style={styles.statPillText}>{Number(statsQuery.data?.count || 0)}笔</Text>
            </View>
          </View>
          <Text style={styles.headerCaption}>
            {profileQuery.data?.nickname || "用户"} · {activeConversation?.title || "当前会话"} · {wsText}
          </Text>
        </View>
      </View>

      {notifyCards.length > 0 ? (
        <View style={styles.notifyStack}>
          {notifyCards.map((card) => (
            <View key={card.id} style={styles.notifyCard}>
              <View style={styles.notifyHead}>
                <View style={styles.notifyTitleRow}>
                  <Ionicons name="time-outline" size={14} color="#ffffff" />
                  <Text style={styles.notifyTitle}>日程提醒</Text>
                </View>
                <Pressable onPress={() => setNotifyCards((prev) => prev.filter((item) => item.id !== card.id))}>
                  <Ionicons name="close" size={16} color="rgba(255,255,255,0.72)" />
                </Pressable>
              </View>
              <Text style={styles.notifyBody}>{card.content}</Text>
              <Text style={styles.notifyTime}>{card.timeText}</Text>
            </View>
          ))}
        </View>
      ) : null}

      {screenError ? <Text style={styles.errorBox}>{screenError}</Text> : null}

      <ScrollView
        ref={scrollRef}
        style={styles.logList}
        contentContainerStyle={[styles.logContent, { paddingBottom: bottomInset + 132 }]}
        refreshControl={<RefreshControl refreshing={historyQuery.isRefetching} onRefresh={() => void refreshAll()} />}
        keyboardShouldPersistTaps="handled"
      >
        {loading ? (
          <View style={styles.loadingWrap}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.loadingText}>正在加载对话...</Text>
          </View>
        ) : null}

        {messages.map((item, index) => {
          const text = getDisplayText(item);
          const isUser = item.role !== "assistant";
          return (
            <View key={`${item.created_at}-${index}`} style={styles.logEntry}>
              {isUser ? (
                <View style={styles.cmdCard}>
                  <Text style={styles.cmdPrompt}>&gt;</Text>
                  <View style={styles.cmdBody}>
                    {Array.isArray(item.image_urls) && item.image_urls.length > 0 ? (
                      <View style={styles.inlineImages}>
                        {item.image_urls.map((url) => (
                          <Image key={url} source={{ uri: url }} style={styles.inlineImage} />
                        ))}
                      </View>
                    ) : null}
                    {!!text ? <Text style={styles.cmdText}>{text}</Text> : null}
                  </View>
                  <Text style={styles.cmdTime}>{formatHmLocal(item.created_at)}</Text>
                </View>
              ) : (
                <View style={styles.resultWrap}>
                  <View style={styles.resultCard}>
                    {Array.isArray(item.image_urls) && item.image_urls.length > 0 ? (
                      <View style={styles.inlineImages}>
                        {item.image_urls.map((url) => (
                          <Image key={url} source={{ uri: url }} style={styles.resultImage} />
                        ))}
                      </View>
                    ) : null}
                    <Text style={styles.resultText}>{text}</Text>
                  </View>
                  <Text style={styles.resultTime}>{formatHmLocal(item.created_at)}</Text>
                </View>
              )}
            </View>
          );
        })}

        {sendMutation.isPending ? (
          <View style={styles.resultWrap}>
            <View style={[styles.resultCard, styles.pendingCard]}>
              <View style={styles.pendingRow}>
                <Text style={styles.pendingLabel}>执行中</Text>
                <Text style={styles.pendingDots}>···</Text>
              </View>
            </View>
          </View>
        ) : null}
      </ScrollView>

      <View style={[styles.composer, { bottom: Math.max(bottomInset - 6, 18) }]}>
        <View style={styles.composerBox}>
          <TextInput
            value={input}
            onChangeText={setInput}
            multiline
            placeholder="记账、提醒、查询..."
            placeholderTextColor="#b0b6c3"
            style={styles.composerInput}
          />
          <Pressable
            style={[styles.sendBtn, (!input.trim() || sendMutation.isPending) && styles.sendBtnDisabled]}
            disabled={!input.trim() || sendMutation.isPending}
            onPress={() => void handleSend()}
          >
            <Ionicons name="arrow-up" size={18} color="#ffffff" />
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  modalWrap: {
    flex: 1,
    flexDirection: "row",
  },
  sidebarMask: {
    flex: 1,
    backgroundColor: "rgba(26,29,38,0.2)",
  },
  sidebarPanel: {
    width: "82%",
    maxWidth: 360,
    backgroundColor: colors.surface,
    ...shadowMd,
  },
  sidebarHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 18,
    paddingBottom: 14,
    borderBottomWidth: 1,
    borderBottomColor: colors.borderLight,
  },
  sidebarTitle: {
    fontSize: 20,
    fontWeight: "700",
    color: colors.text,
  },
  sidebarAddBtn: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: radii.md,
    backgroundColor: colors.primary,
  },
  sidebarAddText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#ffffff",
  },
  sidebarList: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    gap: 10,
  },
  sidebarEmpty: {
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
    borderWidth: 1,
    borderColor: "transparent",
  },
  convItemActive: {
    backgroundColor: colors.primaryLight,
    borderColor: "rgba(79,110,247,0.18)",
  },
  convBody: {
    flex: 1,
    gap: 6,
  },
  convTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  convSummary: {
    fontSize: 12,
    lineHeight: 18,
    color: colors.text3,
  },
  convTime: {
    fontSize: 11,
    color: colors.text4,
  },
  convDelete: {
    width: 38,
    height: 38,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 999,
  },
  header: {
    paddingHorizontal: spacing.pageX,
    paddingBottom: 16,
    backgroundColor: "rgba(255,255,255,0.96)",
    borderBottomWidth: 1,
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
    gap: 10,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: "700",
    color: colors.text,
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 999,
    backgroundColor: colors.text4,
  },
  statusDotOn: {
    backgroundColor: colors.accent,
  },
  historyBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  historyBtnText: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text2,
  },
  headerSub: {
    marginTop: 12,
    gap: 8,
  },
  headerPillRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  subscribePill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: radii.full,
    backgroundColor: colors.bg,
  },
  subscribeLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text3,
  },
  subscribeBadge: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.primary,
    backgroundColor: colors.primaryLight,
    borderRadius: radii.full,
    overflow: "hidden",
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  statPill: {
    backgroundColor: colors.bg,
    borderRadius: radii.full,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  statPillText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text2,
  },
  headerCaption: {
    fontSize: 12,
    color: colors.text3,
  },
  notifyStack: {
    paddingHorizontal: spacing.pageX,
    paddingTop: 12,
    gap: 10,
  },
  notifyCard: {
    backgroundColor: colors.notification,
    borderRadius: radii.lg,
    paddingHorizontal: 16,
    paddingVertical: 14,
    ...shadowMd,
  },
  notifyHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  notifyTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  notifyTitle: {
    fontSize: 12,
    fontWeight: "700",
    color: "#ffffff",
  },
  notifyBody: {
    fontSize: 14,
    lineHeight: 20,
    color: "#ffffff",
  },
  notifyTime: {
    marginTop: 8,
    fontSize: 11,
    color: "rgba(255,255,255,0.55)",
  },
  errorBox: {
    marginHorizontal: spacing.pageX,
    marginTop: 12,
    borderRadius: radii.md,
    backgroundColor: "#fef2f2",
    color: colors.danger,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 13,
    lineHeight: 19,
  },
  logList: {
    flex: 1,
    paddingHorizontal: spacing.pageX,
  },
  logContent: {
    paddingTop: 16,
  },
  logEntry: {
    marginBottom: 14,
  },
  loadingWrap: {
    alignItems: "center",
    justifyContent: "center",
    paddingTop: 70,
    gap: 10,
  },
  loadingText: {
    fontSize: 14,
    color: colors.text3,
  },
  cmdCard: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    backgroundColor: colors.bg,
    borderRadius: radii.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderLeftWidth: 4,
    borderLeftColor: colors.primary,
  },
  cmdPrompt: {
    fontSize: 18,
    lineHeight: 24,
    fontWeight: "800",
    color: colors.primary,
  },
  cmdBody: {
    flex: 1,
  },
  cmdText: {
    fontSize: 16,
    lineHeight: 23,
    color: colors.text,
  },
  cmdTime: {
    fontSize: 11,
    color: colors.text4,
    marginTop: 3,
  },
  resultWrap: {
    paddingLeft: 14,
  },
  resultCard: {
    paddingHorizontal: 16,
    paddingVertical: 14,
    ...surfaceCard,
  },
  resultText: {
    fontSize: 15,
    lineHeight: 23,
    color: colors.text,
  },
  resultTime: {
    marginTop: 6,
    fontSize: 11,
    textAlign: "right",
    color: colors.text4,
    paddingRight: 6,
  },
  inlineImages: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 10,
  },
  inlineImage: {
    width: 92,
    height: 92,
    borderRadius: radii.sm,
    backgroundColor: colors.border,
  },
  resultImage: {
    width: 150,
    height: 150,
    borderRadius: radii.md,
    backgroundColor: colors.border,
  },
  pendingCard: {
    minWidth: 140,
    backgroundColor: colors.bg,
  },
  pendingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  pendingLabel: {
    fontSize: 15,
    color: colors.text2,
  },
  pendingDots: {
    fontSize: 22,
    color: colors.primary,
  },
  composer: {
    position: "absolute",
    left: 0,
    right: 0,
    paddingHorizontal: 12,
  },
  composerBox: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 10,
    backgroundColor: colors.surface,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: "#d8dce6",
    paddingHorizontal: 12,
    paddingVertical: 10,
    ...shadowMd,
  },
  composerInput: {
    flex: 1,
    minHeight: 44,
    maxHeight: 120,
    paddingHorizontal: 8,
    paddingVertical: 10,
    fontSize: 16,
    lineHeight: 22,
    color: colors.text,
  },
  sendBtn: {
    width: 42,
    height: 42,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primary,
    ...shadowSm,
  },
  sendBtnDisabled: {
    opacity: 0.45,
  },
});
