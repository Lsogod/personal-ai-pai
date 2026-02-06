import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createConversation,
  fetchConversations,
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
                <button
                  key={item.id}
                  type="button"
                  onClick={() => switchMutation.mutate(item.id)}
                  disabled={switchMutation.isPending}
                  className={[
                    "w-full rounded-lg border px-3 py-2 text-left",
                    item.active
                      ? "border-slate-900 bg-slate-100"
                      : "border-slate-200 bg-white hover:bg-slate-50"
                  ].join(" ")}
                >
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <p className="mt-1 line-clamp-2 text-xs text-slate-600">
                    {item.summary || "暂无摘要"}
                  </p>
                  <p className="mt-1 text-[11px] text-slate-500">
                    #{item.id} · {formatTime(item.last_message_at)}
                  </p>
                </button>
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

