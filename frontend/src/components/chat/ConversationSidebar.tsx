import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createConversation,
  deleteConversation,
  fetchConversations,
  renameConversation,
  switchConversation,
  type ConversationItem
} from "../../lib/api";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Card, CardContent, CardHeader } from "../ui/card";

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

  const { data: conversations = [] } = useQuery<ConversationItem[]>({
    queryKey: ["conversations"],
    enabled: !!token,
    queryFn: () => fetchConversations(token)
  });

  const activeConversation = useMemo(
    () => conversations.find((item) => item.active),
    [conversations]
  );

  const createMutation = useMutation({
    mutationFn: () => createConversation({ title: title.trim() || undefined }, token),
    onSuccess: async () => {
      setTitle("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] })
      ]);
    }
  });

  const switchMutation = useMutation({
    mutationFn: (conversationId: number) => switchConversation(conversationId, token),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] })
      ]);
    }
  });

  const renameMutation = useMutation({
    mutationFn: (payload: { conversationId: number; title: string }) =>
      renameConversation(payload.conversationId, payload.title, token),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] })
      ]);
    }
  });

  const deleteMutation = useMutation({
    mutationFn: (conversationId: number) => deleteConversation(conversationId, token),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] })
      ]);
    }
  });

  function onRename(item: ConversationItem) {
    const nextTitle = window.prompt("请输入新会话名称", item.title)?.trim();
    if (!nextTitle || nextTitle === item.title) {
      return;
    }
    renameMutation.mutate({ conversationId: item.id, title: nextTitle });
  }

  function onDelete(item: ConversationItem) {
    const ok = window.confirm(`确认删除会话 #${item.id}「${item.title}」吗？`);
    if (!ok) {
      return;
    }
    deleteMutation.mutate(item.id);
  }

  return (
    <Card className="h-full min-h-[560px]">
      <CardHeader className="border-b border-slate-200">
        <h2 className="text-base font-semibold text-slate-900">会话</h2>
      </CardHeader>
      <CardContent className="flex h-[calc(100%-64px)] flex-col gap-3 pt-3">
        <div className="grid gap-2">
          <Input
            placeholder="新会话标题（可选）"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={createMutation.isPending}
          />
          <Button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? "创建中..." : "新建会话"}
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {conversations.length === 0 ? (
            <p className="px-1 py-3 text-sm text-slate-500">暂无会话。</p>
          ) : (
            <div className="space-y-2">
              {conversations.map((item) => (
                <div
                  key={item.id}
                  className={[
                    "w-full rounded-lg border px-3 py-2",
                    item.active
                      ? "border-slate-900 bg-slate-100"
                      : "border-slate-200 bg-white"
                  ].join(" ")}
                >
                  <div className="flex items-start justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => switchMutation.mutate(item.id)}
                      disabled={switchMutation.isPending}
                      className="min-w-0 flex-1 text-left"
                    >
                      <p className="truncate text-sm font-semibold text-slate-900">{item.title}</p>
                      <p className="mt-1 line-clamp-2 text-xs text-slate-600">
                        {item.summary || "暂无摘要"}
                      </p>
                      <p className="mt-1 text-[11px] text-slate-500">
                        #{item.id} · {formatTime(item.last_message_at)}
                      </p>
                    </button>
                    <div className="flex gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onRename(item)}
                        disabled={renameMutation.isPending || deleteMutation.isPending}
                      >
                        改名
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onDelete(item)}
                        disabled={deleteMutation.isPending}
                      >
                        删除
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <p className="text-xs text-slate-500">
          当前会话：{activeConversation ? `#${activeConversation.id}` : "-"}
        </p>
      </CardContent>
    </Card>
  );
}
