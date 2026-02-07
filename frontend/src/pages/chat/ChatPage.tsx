import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../../components/ui/button";
import { apiRequest, streamSsePost } from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { useThemeStore } from "../../store/theme";
import { ChatWindow, type ChatMessage } from "../../components/chat/ChatWindow";
import { ConversationSidebar } from "../../components/chat/ConversationSidebar";
import { LedgerStatsCard } from "../../components/chat/LedgerStatsCard";
import { LedgerListCard } from "../../components/chat/LedgerListCard";
import { ProfileCard } from "../../components/chat/ProfileCard";
import { BindingCard } from "../../components/chat/BindingCard";
import { SkillsPanel } from "../../components/skills/SkillsPanel";
import { CalendarPanel } from "../../components/chat/CalendarPanel";
import {
  MessageSquare,
  Zap,
  Calendar,
  Wallet,
  User,
  Link2,
  LogOut,
  Sun,
  Moon,
  Menu,
  X,
  Bot,
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

type ActiveView = "chat" | "skills" | "calendar" | "ledger" | "profile" | "binding";

const NAV_ITEMS: { key: ActiveView; label: string; icon: React.ElementType }[] = [
  { key: "chat", label: "对话", icon: MessageSquare },
  { key: "skills", label: "技能", icon: Zap },
  { key: "calendar", label: "日历", icon: Calendar },
  { key: "ledger", label: "账单", icon: Wallet },
  { key: "profile", label: "账号", icon: User },
  { key: "binding", label: "绑定", icon: Link2 },
];

export function ChatPage() {
  const { token, setToken } = useAuthStore();
  const { theme, toggleTheme } = useThemeStore();
  const queryClient = useQueryClient();
  const [streamingReply, setStreamingReply] = useState("");
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const { data: profile } = useQuery<Profile>({
    queryKey: ["profile"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/user/profile", {}, token),
  });

  const { data: history = [] } = useQuery<ChatMessage[]>({
    queryKey: ["history"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/chat/history", {}, token),
  });

  const { data: stats = emptyStats } = useQuery<LedgerStats>({
    queryKey: ["stats"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/stats/ledger", {}, token),
  });

  const sendMutation = useMutation({
    mutationFn: async (payload: { content: string; imageUrls: string[] }) => {
      setStreamingReply("");
      await streamSsePost(
        "/api/chat/send?stream=true",
        { content: payload.content, image_urls: payload.imageUrls },
        token,
        (chunk) => setStreamingReply((prev) => prev + chunk)
      );
    },
    onSuccess: async () => {
      setStreamingReply("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] }),
        queryClient.invalidateQueries({ queryKey: ["profile"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] }),
      ]);
    },
    onError: () => setStreamingReply(""),
  });

  async function handleSend(content: string, imageUrls: string[]) {
    await sendMutation.mutateAsync({ content, imageUrls });
  }

  function renderMain() {
    switch (activeView) {
      case "chat":
        return (
          <div className="flex h-full gap-4 animate-fade-in">
            <div className="hidden xl:block w-72 shrink-0">
              <ConversationSidebar token={token} />
            </div>
            <div className="flex-1 min-w-0">
              <ChatWindow
                history={history}
                streamingReply={streamingReply}
                pending={sendMutation.isPending}
                onSend={handleSend}
                profile={profile}
              />
            </div>
          </div>
        );
      case "skills":
        return (
          <div className="animate-fade-in h-full overflow-y-auto p-1">
            <SkillsPanel token={token} />
          </div>
        );
      case "calendar":
        return (
          <div className="animate-fade-in h-full overflow-y-auto p-1">
            <CalendarPanel token={token} />
          </div>
        );
      case "ledger":
        return (
          <div className="animate-fade-in h-full overflow-y-auto p-1">
            <div className="max-w-2xl mx-auto space-y-4">
              <LedgerStatsCard stats={stats} />
              <LedgerListCard token={token} />
            </div>
          </div>
        );
      case "profile":
        return (
          <div className="animate-fade-in h-full overflow-y-auto p-1">
            <div className="max-w-lg mx-auto">
              <ProfileCard profile={profile} />
            </div>
          </div>
        );
      case "binding":
        return (
          <div className="animate-fade-in h-full overflow-y-auto p-1">
            <div className="max-w-lg mx-auto">
              <BindingCard token={token} />
            </div>
          </div>
        );
    }
  }

  return (
    <div className="flex h-screen bg-surface overflow-hidden">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-50 w-[220px] flex flex-col bg-surface-card border-r border-border
          transform transition-transform duration-300 ease-out
          lg:static lg:translate-x-0
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        {/* Logo area */}
        <div className="flex items-center gap-3 px-5 h-16 shrink-0 border-b border-border">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-white">
            <Bot size={20} />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-content truncate">
              {profile?.ai_name || "PAI"}
            </p>
            <p className="text-xs text-content-tertiary truncate">
              {profile?.nickname || "用户"}
            </p>
          </div>
          <button
            className="ml-auto lg:hidden text-content-secondary hover:text-content"
            onClick={() => setSidebarOpen(false)}
          >
            <X size={18} />
          </button>
        </div>

        {/* Nav items */}
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
          {NAV_ITEMS.map(({ key, label, icon: Icon }) => {
            const active = activeView === key;
            return (
              <button
                key={key}
                onClick={() => {
                  setActiveView(key);
                  setSidebarOpen(false);
                }}
                className={`
                  w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium
                  transition-all duration-200 select-none
                  ${
                    active
                      ? "bg-accent/10 text-accent"
                      : "text-content-secondary hover:bg-surface-hover hover:text-content"
                  }
                `}
              >
                <Icon size={18} />
                <span>{label}</span>
              </button>
            );
          })}
        </nav>

        {/* Bottom actions */}
        <div className="shrink-0 border-t border-border px-3 py-3 space-y-1">
          <button
            onClick={toggleTheme}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm text-content-secondary hover:bg-surface-hover hover:text-content transition-all duration-200"
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            <span>{theme === "dark" ? "浅色模式" : "深色模式"}</span>
          </button>
          <button
            onClick={() => setToken(null)}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm text-danger hover:bg-danger/10 transition-all duration-200"
          >
            <LogOut size={18} />
            <span>退出登录</span>
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 flex flex-col min-w-0 h-screen">
        {/* Top bar (mobile) */}
        <header className="flex items-center h-14 px-4 shrink-0 border-b border-border bg-surface-card lg:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            className="text-content-secondary hover:text-content mr-3"
          >
            <Menu size={22} />
          </button>
          <p className="text-sm font-semibold text-content">
            {NAV_ITEMS.find((n) => n.key === activeView)?.label || "PAI"}
          </p>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={toggleTheme}
              className="p-2 rounded-lg text-content-secondary hover:text-content hover:bg-surface-hover transition-colors"
            >
              {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            </button>
          </div>
        </header>

        {/* Content area */}
        <div className="flex-1 min-h-0 p-4 lg:p-6">
          {renderMain()}
        </div>
      </main>
    </div>
  );
}
