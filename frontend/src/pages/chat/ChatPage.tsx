import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "../../components/ui/button";
import { apiRequest, streamSsePost } from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { ChatWindow, type ChatMessage } from "../../components/chat/ChatWindow";
import { ConversationSidebar } from "../../components/chat/ConversationSidebar";
import { LedgerStatsCard } from "../../components/chat/LedgerStatsCard";
import { LedgerListCard } from "../../components/chat/LedgerListCard";
import { ProfileCard } from "../../components/chat/ProfileCard";
import { SkillsPanel } from "../../components/skills/SkillsPanel";
import { CalendarPanel } from "../../components/chat/CalendarPanel";

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

export function ChatPage() {
  const { token, setToken } = useAuthStore();
  const queryClient = useQueryClient();
  const [streamingReply, setStreamingReply] = useState("");
  const [activeView, setActiveView] = useState<"chat" | "skills" | "calendar">("chat");

  const { data: profile } = useQuery<Profile>({
    queryKey: ["profile"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/user/profile", {}, token)
  });

  const { data: history = [] } = useQuery<ChatMessage[]>({
    queryKey: ["history"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/chat/history", {}, token)
  });

  const { data: stats = emptyStats } = useQuery<LedgerStats>({
    queryKey: ["stats"],
    enabled: !!token,
    queryFn: () => apiRequest("/api/stats/ledger", {}, token)
  });

  const sendMutation = useMutation({
    mutationFn: async (payload: { content: string; imageUrls: string[] }) => {
      setStreamingReply("");
      await streamSsePost(
        "/api/chat/send?stream=true",
        {
          content: payload.content,
          image_urls: payload.imageUrls
        },
        token,
        (chunk) => {
          setStreamingReply((prev) => prev + chunk);
        }
      );
    },
    onSuccess: async () => {
      setStreamingReply("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] }),
        queryClient.invalidateQueries({ queryKey: ["profile"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] })
      ]);
    },
    onError: () => {
      setStreamingReply("");
    }
  });

  async function handleSend(content: string, imageUrls: string[]) {
    await sendMutation.mutateAsync({ content, imageUrls });
  }

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-5">
        <header className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900 text-sm font-semibold text-white">
                AI
              </div>
              <div>
                <p className="text-sm font-semibold">{profile?.ai_name || "PAI"} 助手</p>
                <p className="text-xs text-slate-500">
                  当前用户：{profile?.nickname || "用户"} · 引导阶段 {profile?.setup_stage ?? 0}
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                variant={activeView === "chat" ? "default" : "ghost"}
                onClick={() => setActiveView("chat")}
              >
                聊天
              </Button>
              <Button
                variant={activeView === "skills" ? "default" : "ghost"}
                onClick={() => setActiveView("skills")}
              >
                技能
              </Button>
              <Button
                variant={activeView === "calendar" ? "default" : "ghost"}
                onClick={() => setActiveView("calendar")}
              >
                日历
              </Button>
              <Button variant="ghost" onClick={() => queryClient.invalidateQueries()}>
                刷新
              </Button>
              <Button variant="subtle" onClick={() => setToken(null)}>
                退出
              </Button>
            </div>
          </div>
        </header>

        {activeView === "chat" ? (
          <main className="mt-4 grid flex-1 gap-4 lg:grid-cols-[260px_minmax(0,1fr)_280px]">
            <ConversationSidebar token={token} />
            <div className="min-w-0">
              <ChatWindow
                history={history}
                streamingReply={streamingReply}
                pending={sendMutation.isPending}
                onSend={handleSend}
              />
            </div>
            <aside className="space-y-4">
              <LedgerStatsCard stats={stats} />
              <LedgerListCard token={token} />
              <ProfileCard profile={profile} />
            </aside>
          </main>
        ) : activeView === "skills" ? (
          <main className="mt-4 flex-1">
            <SkillsPanel token={token} />
          </main>
        ) : (
          <main className="mt-4 flex-1">
            <CalendarPanel token={token} />
          </main>
        )}
      </div>
    </div>
  );
}
