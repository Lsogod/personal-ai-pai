import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createConversation,
  deleteConversation,
  fetchConversations,
  renameConversation,
  switchConversation,
  type ConversationItem,
} from "../../lib/api";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Plus, Pencil, Trash2, MessageSquare } from "../ui/icons";

interface ConversationSidebarProps {
  token: string | null;
}

function formatTime(iso: string) {
  const date = new Date(iso);
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  const hour = `${date.getHours()}`.padStart(2, "0");
  const minute = `${date.getMinutes()}`.padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}

export function ConversationSidebar({ token }: ConversationSidebarProps) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [showNew, setShowNew] = useState(false);

  const { data: conversations = [] } = useQuery<ConversationItem[]>({
    queryKey: ["conversations"],
    enabled: !!token,
    queryFn: () => fetchConversations(token),
  });

  const activeConversation = useMemo(
    () => conversations.find((item) => item.active),
    [conversations]
  );

  const createMutation = useMutation({
    mutationFn: () => createConversation({ title: title.trim() || undefined }, token),
    onSuccess: async () => {
      setTitle("");
      setShowNew(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] }),
      ]);
    },
  });

  const switchMutation = useMutation({
    mutationFn: (conversationId: number) => switchConversation(conversationId, token),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] }),
      ]);
    },
  });

  const renameMutation = useMutation({
    mutationFn: (payload: { conversationId: number; title: string }) =>
      renameConversation(payload.conversationId, payload.title, token),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] }),
      ]);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (conversationId: number) => deleteConversation(conversationId, token),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] }),
      ]);
    },
  });

  function onRename(item: ConversationItem) {
    const nextTitle = window.prompt("请输入新会话名称", item.title)?.trim();
    if (!nextTitle || nextTitle === item.title) return;
    renameMutation.mutate({ conversationId: item.id, title: nextTitle });
  }

  function onDelete(item: ConversationItem) {
    const ok = window.confirm(`确认删除会话「${item.title}」吗？`);
    if (!ok) return;
    deleteMutation.mutate(item.id);
  }

  return (
    <div className="flex flex-col h-full bg-surface-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-10 shrink-0 border-b border-border/50">
        <h2 className="text-xs font-semibold text-content-tertiary uppercase tracking-wider">历史会话</h2>
        <button
          onClick={() => setShowNew(!showNew)}
          className="p-1 rounded-md text-content-secondary hover:text-accent hover:bg-surface-hover transition-colors"
          title="新建会话"
        >
          <Plus size={16} />
        </button>
      </div>

      {/* New conversation */}
      {showNew && (
        <div className="px-3 pt-3 pb-1 space-y-2 border-b border-border animate-fade-in">
          <Input
            placeholder="会话标题（可选）"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={createMutation.isPending}
            onKeyDown={(e) => {
              const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
              if (native.isComposing || native.keyCode === 229) return;
              if (e.key === "Enter") createMutation.mutate();
            }}
          />
          <Button
            className="w-full"
            size="sm"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? "创建中..." : "新建会话"}
          </Button>
        </div>
      )}

      {/* Conversations list */}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
        {conversations.length === 0 ? (
          <p className="px-3 py-8 text-sm text-content-tertiary text-center">暂无会话</p>
        ) : (
          conversations.map((item) => {
            const active = item.active;
            return (
              <div
                key={item.id}
                className={`group rounded-xl px-3 py-2.5 cursor-pointer transition-all duration-200 ${
                  active
                    ? "bg-surface-active text-accent"
                    : "hover:bg-surface-hover text-content"
                }`}
                onClick={() => !active && switchMutation.mutate(item.id)}
              >
                <div className="flex items-start gap-2.5">
                  <MessageSquare
                    size={15}
                    className={`shrink-0 mt-0.5 ${active ? "text-accent" : "text-content-tertiary"}`}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{item.title}</p>
                    <p className="text-xs text-content-tertiary mt-0.5 line-clamp-1">
                      {item.summary || "暂无摘要"}
                    </p>
                    <p className="text-[11px] text-content-tertiary mt-0.5">
                      {formatTime(item.last_message_at)}
                    </p>
                  </div>
                  <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onRename(item);
                      }}
                      className="p-1 rounded-md hover:bg-surface-active text-content-tertiary hover:text-content transition-colors"
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(item);
                      }}
                      className="p-1 rounded-md hover:bg-danger/10 text-content-tertiary hover:text-danger transition-colors"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Active indicator */}
      <div className="shrink-0 px-3 py-2 border-t border-border">
        <p className="text-xs text-content-tertiary">
          当前：{activeConversation ? `#${activeConversation.id} ${activeConversation.title}` : "无"}
        </p>
      </div>
    </div>
  );
}
