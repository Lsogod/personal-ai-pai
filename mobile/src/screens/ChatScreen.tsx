import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  Image,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  API_BASE,
  API_BASE_HELP,
  ChatMessage,
  ConversationItem,
  createConversation,
  fetchConversations,
  fetchHistory,
  fetchProfile,
  getNotificationsWsUrl,
  sendChat,
  switchConversation,
} from "../lib/api";
import { useAuthStore } from "../store/auth";

type WsState = "connecting" | "open" | "closed";

function formatTime(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getDisplayText(message: ChatMessage) {
  if (message.content === "[图片]" && Array.isArray(message.image_urls) && message.image_urls.length > 0) {
    return "";
  }
  return message.content || "";
}

export function ChatScreen() {
  const token = useAuthStore((state) => state.token);
  const setToken = useAuthStore((state) => state.setToken);
  const queryClient = useQueryClient();
  const scrollRef = useRef<ScrollView | null>(null);
  const [input, setInput] = useState("");
  const [screenError, setScreenError] = useState<string | null>(null);
  const [wsState, setWsState] = useState<WsState>(API_BASE ? "connecting" : "closed");

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

  const activeConversation = useMemo(
    () => conversationsQuery.data?.find((item) => item.active) || null,
    [conversationsQuery.data]
  );

  useEffect(() => {
    const timer = setTimeout(() => {
      scrollRef.current?.scrollToEnd({ animated: true });
    }, 40);

    return () => clearTimeout(timer);
  }, [historyQuery.data]);

  useEffect(() => {
    if (!token || !API_BASE) {
      return;
    }

    let closedManually = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closedManually) return;
      setWsState("connecting");
      socket = new WebSocket(getNotificationsWsUrl(token));

      socket.onopen = () => {
        setWsState("open");
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (payload?.type === "reminder" && payload?.content) {
            const reminder: ChatMessage = {
              role: "assistant",
              content: String(payload.content),
              created_at: String(payload.created_at || new Date().toISOString()),
              image_urls: [],
            };
            queryClient.setQueryData<ChatMessage[]>(["history"], (prev = []) => [...prev, reminder]);
          }
        } catch {
          // Ignore malformed payloads to keep the session alive.
        }
      };

      socket.onerror = () => {
        setWsState("closed");
      };

      socket.onclose = () => {
        setWsState("closed");
        if (closedManually) return;
        reconnectTimer = setTimeout(connect, 3_000);
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
    mutationFn: async (content: string) => {
      return sendChat(content, token!);
    },
    onMutate: async (content) => {
      await queryClient.cancelQueries({ queryKey: ["history"] });
      const previousHistory = queryClient.getQueryData<ChatMessage[]>(["history"]) || [];
      const optimisticMessage: ChatMessage = {
        role: "user",
        content,
        created_at: new Date().toISOString(),
        image_urls: [],
      };
      queryClient.setQueryData<ChatMessage[]>(["history"], [...previousHistory, optimisticMessage]);
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
    onError: (error: Error, _content, context) => {
      if (context?.previousHistory) {
        queryClient.setQueryData(["history"], context.previousHistory);
      }
      setScreenError(error.message);
    },
  });

  const switchMutation = useMutation({
    mutationFn: async (conversationId: number) => switchConversation(conversationId, token!),
    onSuccess: async () => {
      setScreenError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (error: Error) => {
      setScreenError(error.message);
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => createConversation(undefined, token!),
    onSuccess: async () => {
      setScreenError(null);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await queryClient.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (error: Error) => {
      setScreenError(error.message);
    },
  });

  const loading = profileQuery.isLoading || historyQuery.isLoading;
  const messages = historyQuery.data || [];
  const wsText = wsState === "open" ? "提醒通道已连接" : wsState === "connecting" ? "提醒通道连接中" : "提醒通道已断开";

  async function refreshAll() {
    setScreenError(null);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["profile"] }),
      queryClient.invalidateQueries({ queryKey: ["history"] }),
      queryClient.invalidateQueries({ queryKey: ["conversations"] }),
    ]);
  }

  async function logout() {
    await setToken(null);
    queryClient.clear();
  }

  async function handleSend() {
    const content = input.trim();
    if (!content || sendMutation.isPending) return;
    await sendMutation.mutateAsync(content);
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <KeyboardAvoidingView
        style={styles.keyboard}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View style={styles.header}>
          <View style={styles.headerMain}>
            <Text style={styles.headerTitle}>
              {profileQuery.data?.ai_emoji || "🤖"} {profileQuery.data?.ai_name || "PAI"}
            </Text>
            <Text style={styles.headerSubtitle}>
              {profileQuery.data?.nickname || "未命名用户"} · {activeConversation?.title || "当前会话"}
            </Text>
          </View>
          <Pressable onPress={() => void logout()} style={styles.headerButton}>
            <Text style={styles.headerButtonText}>退出</Text>
          </Pressable>
        </View>

        <View style={styles.statusRow}>
          <View style={[styles.statusDot, wsState === "open" ? styles.statusDotOpen : styles.statusDotClosed]} />
          <Text style={styles.statusText}>{API_BASE ? wsText : API_BASE_HELP}</Text>
        </View>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.conversationRow}
        >
          {(conversationsQuery.data || []).map((conversation: ConversationItem) => (
            <Pressable
              key={conversation.id}
              style={[
                styles.conversationChip,
                conversation.active && styles.conversationChipActive,
              ]}
              disabled={switchMutation.isPending}
              onPress={() => void switchMutation.mutateAsync(conversation.id)}
            >
              <Text
                numberOfLines={1}
                style={[
                  styles.conversationChipText,
                  conversation.active && styles.conversationChipTextActive,
                ]}
              >
                {conversation.title}
              </Text>
            </Pressable>
          ))}
          <Pressable
            style={[styles.conversationChip, styles.conversationCreateChip]}
            disabled={createMutation.isPending}
            onPress={() => void createMutation.mutateAsync()}
          >
            <Text style={styles.conversationCreateText}>
              {createMutation.isPending ? "创建中..." : "+ 新会话"}
            </Text>
          </Pressable>
        </ScrollView>

        {!!screenError && <Text style={styles.errorText}>{screenError}</Text>}

        <ScrollView
          ref={scrollRef}
          style={styles.messages}
          contentContainerStyle={styles.messagesContent}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void refreshAll()} />}
        >
          {loading && messages.length === 0 ? (
            <View style={styles.loadingBlock}>
              <ActivityIndicator size="large" color="#0f172a" />
              <Text style={styles.loadingText}>正在加载聊天内容</Text>
            </View>
          ) : (
            messages.map((message, index) => {
              const isUser = message.role !== "assistant";
              const text = getDisplayText(message);
              return (
                <View
                  key={`${message.created_at}-${index}`}
                  style={[styles.messageRow, isUser ? styles.messageRowUser : styles.messageRowAssistant]}
                >
                  <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleAssistant]}>
                    {!!text && (
                      <Text style={[styles.bubbleText, isUser ? styles.bubbleTextUser : styles.bubbleTextAssistant]}>
                        {text}
                      </Text>
                    )}
                    {Array.isArray(message.image_urls) && message.image_urls.length > 0 && (
                      <View style={styles.imageList}>
                        {message.image_urls.map((url) => (
                          <Image key={url} source={{ uri: url }} style={styles.messageImage} />
                        ))}
                      </View>
                    )}
                    <Text style={[styles.timeText, isUser ? styles.timeTextUser : styles.timeTextAssistant]}>
                      {formatTime(message.created_at)}
                    </Text>
                  </View>
                </View>
              );
            })
          )}
        </ScrollView>

        <View style={styles.inputWrap}>
          <TextInput
            value={input}
            onChangeText={setInput}
            multiline
            placeholder="输入消息..."
            placeholderTextColor="#94a3b8"
            style={styles.input}
          />
          <Pressable
            style={[styles.sendButton, sendMutation.isPending && styles.sendButtonDisabled]}
            disabled={sendMutation.isPending || !input.trim()}
            onPress={() => void handleSend()}
          >
            <Text style={styles.sendButtonText}>
              {sendMutation.isPending ? "发送中" : "发送"}
            </Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: "#f8fafc",
  },
  keyboard: {
    flex: 1,
  },
  header: {
    paddingHorizontal: 18,
    paddingTop: 12,
    paddingBottom: 6,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  headerMain: {
    flex: 1,
    paddingRight: 16,
    gap: 4,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: "800",
    color: "#0f172a",
  },
  headerSubtitle: {
    fontSize: 13,
    color: "#64748b",
  },
  headerButton: {
    borderRadius: 14,
    backgroundColor: "#e2e8f0",
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  headerButtonText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#0f172a",
  },
  statusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 18,
    paddingVertical: 8,
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 999,
  },
  statusDotOpen: {
    backgroundColor: "#16a34a",
  },
  statusDotClosed: {
    backgroundColor: "#f59e0b",
  },
  statusText: {
    flex: 1,
    fontSize: 12,
    lineHeight: 18,
    color: "#475569",
  },
  conversationRow: {
    paddingHorizontal: 14,
    paddingBottom: 8,
    gap: 10,
  },
  conversationChip: {
    maxWidth: 180,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#cbd5e1",
    backgroundColor: "#ffffff",
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  conversationChipActive: {
    borderColor: "#0f172a",
    backgroundColor: "#0f172a",
  },
  conversationChipText: {
    fontSize: 13,
    fontWeight: "600",
    color: "#334155",
  },
  conversationChipTextActive: {
    color: "#ffffff",
  },
  conversationCreateChip: {
    borderStyle: "dashed",
  },
  conversationCreateText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#0f172a",
  },
  errorText: {
    marginHorizontal: 18,
    marginBottom: 10,
    borderRadius: 14,
    backgroundColor: "#fee2e2",
    color: "#b91c1c",
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 14,
    lineHeight: 20,
  },
  messages: {
    flex: 1,
  },
  messagesContent: {
    paddingHorizontal: 18,
    paddingBottom: 20,
    gap: 12,
  },
  loadingBlock: {
    alignItems: "center",
    justifyContent: "center",
    paddingTop: 80,
    gap: 10,
  },
  loadingText: {
    fontSize: 14,
    color: "#64748b",
  },
  messageRow: {
    flexDirection: "row",
  },
  messageRowUser: {
    justifyContent: "flex-end",
  },
  messageRowAssistant: {
    justifyContent: "flex-start",
  },
  bubble: {
    maxWidth: "84%",
    borderRadius: 22,
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 10,
  },
  bubbleAssistant: {
    backgroundColor: "#ffffff",
  },
  bubbleUser: {
    backgroundColor: "#0f172a",
  },
  bubbleText: {
    fontSize: 15,
    lineHeight: 22,
  },
  bubbleTextAssistant: {
    color: "#0f172a",
  },
  bubbleTextUser: {
    color: "#ffffff",
  },
  imageList: {
    gap: 8,
  },
  messageImage: {
    width: 180,
    height: 180,
    borderRadius: 16,
    backgroundColor: "#e2e8f0",
  },
  timeText: {
    fontSize: 11,
  },
  timeTextAssistant: {
    color: "#94a3b8",
  },
  timeTextUser: {
    color: "#cbd5e1",
  },
  inputWrap: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 10,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 16,
    backgroundColor: "#ffffff",
    borderTopWidth: 1,
    borderTopColor: "#e2e8f0",
  },
  input: {
    flex: 1,
    maxHeight: 120,
    borderWidth: 1,
    borderColor: "#cbd5e1",
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingVertical: 12,
    backgroundColor: "#f8fafc",
    fontSize: 16,
    color: "#0f172a",
  },
  sendButton: {
    borderRadius: 16,
    backgroundColor: "#0f172a",
    paddingHorizontal: 18,
    paddingVertical: 14,
  },
  sendButtonDisabled: {
    opacity: 0.6,
  },
  sendButtonText: {
    fontSize: 14,
    fontWeight: "700",
    color: "#ffffff",
  },
});
