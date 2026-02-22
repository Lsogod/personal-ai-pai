import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiRequest, streamSsePost } from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { useThemeStore } from "../../store/theme";
import { ChatWindow, type ChatMessage } from "../../components/chat/ChatWindow";
import { ConversationSidebar } from "../../components/chat/ConversationSidebar";
import { ProfileCard } from "../../components/chat/ProfileCard";
import { NotificationToasts, type ToastItem } from "../../components/ui/NotificationToasts";
import { RightInfoPanel } from "./RightInfoPanel";
import {
  Menu,
  Bot,
  LogOut,
  Sun,
  Moon,
  LayoutGrid
} from "../../components/ui/icons";

interface Profile {
  uuid: string;
  nickname: string;
  ai_name: string;
  ai_emoji: string;
  platform: string;
  email?: string | null;
  setup_stage: number;
}

interface LedgerStats {
  total: number;
  count: number;
}

const emptyStats: LedgerStats = { total: 0, count: 0 };
const TOAST_LIMIT = 4;

export function ChatPage() {
  const { token, setToken } = useAuthStore();
  const { theme, toggleTheme } = useThemeStore();
  const queryClient = useQueryClient();
  const [streamingReply, setStreamingReply] = useState("");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [rightPanelOpen, setRightPanelOpen] = useState(true);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const streamBufferRef = useRef("");
  const streamFlushTimerRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);

  function playReminderSound() {
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    if (!audioCtxRef.current) {
      audioCtxRef.current = new AudioCtx();
    }
    const ctx = audioCtxRef.current;

    const play = () => {
      const master = ctx.createGain();
      master.gain.value = 0.08;
      master.connect(ctx.destination);
      const start = ctx.currentTime + 0.01;

      const tone = (freq: number, delay: number, duration: number) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(freq, start + delay);
        gain.gain.setValueAtTime(0.0001, start + delay);
        gain.gain.linearRampToValueAtTime(1, start + delay + 0.012);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + delay + duration);
        osc.connect(gain);
        gain.connect(master);
        osc.start(start + delay);
        osc.stop(start + delay + duration + 0.03);
      };

      tone(880, 0, 0.16);
      tone(1318.5, 0.2, 0.24);
    };

    if (ctx.state === "suspended") {
      ctx.resume().then(play).catch(() => {
        // Browser autoplay policy can block sound before user interaction.
      });
      return;
    }
    play();
  }

  function removeToast(id: string) {
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }

  function pushReminderToast(content: string, createdAt: string) {
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const toast: ToastItem = {
      id,
      title: "日程提醒",
      content,
      createdAt,
    };
    setToasts((prev) => [toast, ...prev].slice(0, TOAST_LIMIT));
  }

  const { data: profile } = useQuery<Profile>({
    queryKey: ["profile"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/user/profile", {}, token),
  });

  const { data: history = [] } = useQuery<ChatMessage[]>({
    queryKey: ["history"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/chat/history", {}, token),
    refetchInterval: token ? 15000 : false,
  });

  const { data: stats = emptyStats } = useQuery<LedgerStats>({
    queryKey: ["stats"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/stats/ledger?scope=month", {}, token),
    refetchInterval: token ? 15000 : false,
  });

  async function refreshSideData() {
    await Promise.all([
      queryClient.refetchQueries({ queryKey: ["history"], type: "active" }),
      queryClient.refetchQueries({ queryKey: ["calendar"], type: "active" }),
      queryClient.refetchQueries({ queryKey: ["ledgers"], type: "active" }),
      queryClient.refetchQueries({ queryKey: ["stats"], type: "active" }),
      queryClient.refetchQueries({ queryKey: ["conversations"], type: "active" }),
    ]);
  }

  useEffect(() => {
    if (!token) return;
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/api/notifications/ws?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        if (payload?.type === "reminder" && payload?.content) {
          const content = String(payload.content);
          const createdAt = String(payload.created_at || new Date().toISOString());
          const reminderMessage: ChatMessage = {
            role: "assistant",
            content,
            created_at: createdAt,
          };
          queryClient.setQueryData<ChatMessage[]>(["history"], (prev) => [
            ...(prev || []),
            reminderMessage,
          ]);
          pushReminderToast(content, createdAt);
          playReminderSound();
          void refreshSideData();
        }
        if (payload?.type === "message" && payload?.content) {
          void refreshSideData();
        }
      } catch {
        // Ignore malformed websocket payloads.
      }
    };

    return () => {
      ws.close();
    };
  }, [token, queryClient]);

  useEffect(() => {
    return () => {
      if (audioCtxRef.current) {
        void audioCtxRef.current.close().catch(() => {});
        audioCtxRef.current = null;
      }
    };
  }, []);

  const sendMutation = useMutation<
    void,
    Error,
    { content: string; imageUrls: string[] },
    { previousHistory: ChatMessage[] }
  >({
    onMutate: async (payload) => {
      await queryClient.cancelQueries({ queryKey: ["history"] });
      const previousHistory = queryClient.getQueryData<ChatMessage[]>(["history"]) || [];
      const optimisticMessage: ChatMessage = {
        role: "user",
        content: payload.content,
        image_urls: payload.imageUrls,
        created_at: new Date().toISOString(),
      };
      queryClient.setQueryData<ChatMessage[]>(["history"], [...previousHistory, optimisticMessage]);
      return { previousHistory };
    },
    mutationFn: async (payload: { content: string; imageUrls: string[] }) => {
      streamBufferRef.current = "";
      if (streamFlushTimerRef.current !== null) {
        window.clearTimeout(streamFlushTimerRef.current);
        streamFlushTimerRef.current = null;
      }
      setStreamingReply("");
      await streamSsePost(
        "/api/chat/send?stream=true",
        { content: payload.content, image_urls: payload.imageUrls },
        token,
        (chunk) => {
          streamBufferRef.current += chunk;
          if (streamFlushTimerRef.current === null) {
            streamFlushTimerRef.current = window.setTimeout(() => {
              streamFlushTimerRef.current = null;
              setStreamingReply(streamBufferRef.current);
            }, 40);
          }
        }
      );
      if (streamFlushTimerRef.current !== null) {
        window.clearTimeout(streamFlushTimerRef.current);
        streamFlushTimerRef.current = null;
      }
      setStreamingReply(streamBufferRef.current);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["profile"] });
      await refreshSideData();
      streamBufferRef.current = "";
      if (streamFlushTimerRef.current !== null) {
        window.clearTimeout(streamFlushTimerRef.current);
        streamFlushTimerRef.current = null;
      }
      setStreamingReply("");
    },
    onError: (_error, _payload, context) => {
      if (context?.previousHistory) {
        queryClient.setQueryData<ChatMessage[]>(["history"], context.previousHistory);
      }
      streamBufferRef.current = "";
      if (streamFlushTimerRef.current !== null) {
        window.clearTimeout(streamFlushTimerRef.current);
        streamFlushTimerRef.current = null;
      }
      setStreamingReply("");
    },
  });

  async function handleSend(content: string, imageUrls: string[]) {
    await sendMutation.mutateAsync({ content, imageUrls });
  }

  const handleLogout = () => {
    if (window.confirm("确定要退出登录吗？")) {
      setToken(null);
    }
  };

  return (
    <div className="flex h-screen bg-surface overflow-hidden text-content">
      <NotificationToasts items={toasts} onDismiss={removeToast} />
      {/* Mobile overlay */}
      {mobileMenuOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 lg:hidden"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      {/* Left Sidebar (History & User) */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-50 w-[280px] flex flex-col bg-surface-card border-r border-border
          transform transition-transform duration-300 ease-out
          lg:static lg:translate-x-0
          ${mobileMenuOpen ? "translate-x-0" : "-translate-x-full"}
          h-full
        `}
      >
        {/* Header Logo */}
        <div className="flex items-center gap-3 px-5 h-16 shrink-0 border-b border-border bg-surface-card z-10 transition-colors">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-content text-surface shadow-md">
            <Bot size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="font-bold text-lg text-content leading-tight tracking-tight">
              PAI
            </h1>
            <p className="text-xs text-content-tertiary truncate">
              Personal Assistant
            </p>
          </div>
          {/* Mobile close button */}
          <button 
            onClick={() => setMobileMenuOpen(false)}
            className="lg:hidden p-1 text-content-tertiary hover:text-content"
          >
            <LayoutGrid size={20} />
          </button>
        </div>

        {/* Conversation List */}
        <div className="flex-1 min-h-0 relative">
          <ConversationSidebar token={token} />
        </div>

        {/* Footer User Profile & Settings */}
        <div className="p-4 border-t border-border bg-surface-secondary/40 space-y-3 shrink-0 backdrop-blur-sm">
          {profile && <ProfileCard profile={profile} />}
          
          <div className="flex items-center justify-between gap-2 pt-1">
            <button
              onClick={toggleTheme}
              className="flex items-center justify-center h-8 w-8 rounded-lg text-content-secondary hover:bg-surface-hover hover:text-content transition-all active:scale-95 border border-transparent hover:border-border"
              title={theme === "dark" ? "切换亮色" : "切换深色"}
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            
            <button
              onClick={handleLogout}
              className="flex flex-1 items-center justify-end gap-1.5 h-8 px-2 rounded-lg text-content-tertiary hover:text-danger hover:bg-danger/5 transition-colors text-xs font-medium"
            >
              <span>退出</span>
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </aside>

      {/* Main Content Area (Chat) */}
      <main className="flex-1 flex flex-col min-w-0 bg-surface relative h-full">
        {/* Header Overlay for Mobile */}
        <div className="lg:hidden h-14 flex items-center px-4 border-b border-border bg-surface/80 backdrop-blur-md sticky top-0 z-30 justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileMenuOpen(true)}
              className="p-2 -ml-2 text-content-secondary hover:text-content active:scale-95 transition-transform"
            >
              <Menu size={20} />
            </button>
            <span className="font-medium text-content">对话</span>
          </div>
          <button
            onClick={() => setRightPanelOpen(!rightPanelOpen)}
            className={`p-2 -mr-2 transition-colors ${rightPanelOpen ? 'text-accent' : 'text-content-secondary'}`}
          >
            <LayoutGrid size={20} />
          </button>
        </div>

        {/* Chat Window */}
        <div className="flex-1 h-full min-h-0">
          <ChatWindow
            history={history}
            streamingReply={streamingReply}
            pending={sendMutation.isPending}
            onSend={handleSend}
            profile={profile}
          />
        </div>
      </main>

      {/* Right Sidebar (Widgets) */}
      <aside 
        className={`
          flex-col h-full border-l border-border bg-surface-card shrink-0 transition-all duration-300 ease-in-out
          ${rightPanelOpen ? "w-[360px] translate-x-0 opacity-100" : "w-0 translate-x-full opacity-0 hidden xl:hidden"}
          hidden xl:flex
        `}
      >
        <div className="w-[360px] h-full overflow-hidden">
           <RightInfoPanel token={token} stats={stats} />
        </div>
      </aside>
    </div>
  );
}
