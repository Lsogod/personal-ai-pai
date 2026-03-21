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
import { formatMdHmLocal } from "../../lib/datetime";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Plus, Pencil, Trash2, MessageSquare } from "../ui/icons";
import { ConfirmDialog, PromptDialog } from "../ui/ConfirmDialog";

interface ConversationSidebarProps {
  token: string | null;
}

function formatTime(iso: string) {
  return formatMdHmLocal(iso);
}

export function ConversationSidebar({ token }: ConversationSidebarProps) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [confirmDeleteItem, setConfirmDeleteItem] = useState<ConversationItem | null>(null);
  const [promptRenameItem, setPromptRenameItem] = useState<ConversationItem | null>(null);

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
    onError: (_error: Error) => {
      // Error is surfaced through deleteMutation.error if needed
    },
  });

  function onRename(item: ConversationItem) {
    setPromptRenameItem(item);
  }

  function onDelete(item: ConversationItem) {
    if (deleteMutation.isPending) return;
    setConfirmDeleteItem(item);
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
          aria-label="新建会话"
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
                  <div className="flex gap-0.5 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onRename(item);
                      }}
                      disabled={renameMutation.isPending || deleteMutation.isPending}
                      className="p-1 rounded-md hover:bg-surface-active text-content-tertiary hover:text-content transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                      aria-label={`重命名会话「${item.title}」`}
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(item);
                      }}
                      disabled={deleteMutation.isPending}
                      className="p-1 rounded-md hover:bg-danger/10 text-content-tertiary hover:text-danger transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                      aria-label={`删除会话「${item.title}」`}
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

      {/* Delete confirmation dialog */}
      <ConfirmDialog
        open={!!confirmDeleteItem}
        title="删除会话"
        message={confirmDeleteItem ? `确认删除会话「${confirmDeleteItem.title}」吗？` : ""}
        confirmText="删除"
        variant="danger"
        onConfirm={() => {
          if (confirmDeleteItem) deleteMutation.mutate(confirmDeleteItem.id);
          setConfirmDeleteItem(null);
        }}
        onCancel={() => setConfirmDeleteItem(null)}
      />

      {/* Rename prompt dialog */}
      <PromptDialog
        open={!!promptRenameItem}
        title="重命名会话"
        message="请输入新会话名称"
        defaultValue={promptRenameItem?.title || ""}
        placeholder="会话名称"
        onConfirm={(value) => {
          const nextTitle = value.trim();
          if (nextTitle && promptRenameItem && nextTitle !== promptRenameItem.title) {
            renameMutation.mutate({ conversationId: promptRenameItem.id, title: nextTitle });
          }
          setPromptRenameItem(null);
        }}
        onCancel={() => setPromptRenameItem(null)}
      />
    </div>
  );
}
