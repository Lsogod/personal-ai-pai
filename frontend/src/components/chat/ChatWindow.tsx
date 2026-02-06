import { FormEvent, useState } from "react";
import ReactMarkdown from "react-markdown";

import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Card, CardContent, CardHeader } from "../ui/card";

export interface ChatMessage {
  role: string;
  content: string;
  created_at: string;
}

interface ChatWindowProps {
  history: ChatMessage[];
  streamingReply: string;
  pending: boolean;
  onSend: (content: string, imageUrls: string[]) => Promise<void>;
}

function formatTime(iso: string) {
  const date = new Date(iso);
  return `${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}

export function ChatWindow({ history, streamingReply, pending, onSend }: ChatWindowProps) {
  const [content, setContent] = useState("");
  const [imageUrl, setImageUrl] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = content.trim();
    const images = imageUrl.trim() ? [imageUrl.trim()] : [];
    if (!trimmed && images.length === 0) {
      return;
    }
    await onSend(trimmed, images);
    setContent("");
    setImageUrl("");
  }

  return (
    <Card className="h-full min-h-[560px] flex flex-col">
      <CardHeader className="border-b border-slate-200">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-slate-900">对话</h2>
            <p className="text-xs text-slate-500">支持 Markdown，适合聊天机器人交互。</p>
          </div>
          <div className="rounded-full bg-emerald-100 px-2 py-1 text-xs font-medium text-emerald-700">
            在线
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <div className="flex-1 overflow-y-auto rounded-lg bg-slate-50 p-3">
          {history.length === 0 && !streamingReply && (
            <p className="py-10 text-center text-sm text-slate-500">暂无历史消息。</p>
          )}
          {[...history, ...(streamingReply ? [{ role: "assistant", content: streamingReply, created_at: new Date().toISOString() }] : [])].map(
            (msg, idx) => (
            <div
              key={`${msg.created_at}-${idx}`}
              className={`mb-3 flex items-end gap-2 ${
                msg.role === "assistant" ? "justify-start" : "justify-end"
              }`}
            >
              {msg.role === "assistant" && (
                <div className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white">
                  AI
                </div>
              )}
              <div
                className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                  msg.role === "assistant"
                    ? "rounded-bl-md bg-white text-slate-800 border border-slate-200"
                    : "rounded-br-md bg-slate-900 text-white"
                }`}
              >
                {msg.role === "assistant" ? (
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                ) : (
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                )}
                <p
                  className={`mt-1 text-[11px] ${
                    msg.role === "assistant" ? "text-slate-400" : "text-slate-300"
                  }`}
                >
                  {formatTime(msg.created_at)}
                </p>
              </div>
              {msg.role !== "assistant" && (
                <div className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700">
                  你
                </div>
              )}
            </div>
            )
          )}
        </div>
        <form className="grid gap-2" onSubmit={handleSubmit}>
          <div className="flex gap-2">
            <Input
              placeholder="输入消息，例如：帮我记录今天午饭 35 元"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              disabled={pending}
            />
            <Button type="submit" disabled={pending || (!content.trim() && !imageUrl.trim())}>
              {pending ? "发送中..." : "发送"}
            </Button>
          </div>
          <Input
            placeholder="可选：图片 URL（用于小票识别）"
            value={imageUrl}
            onChange={(e) => setImageUrl(e.target.value)}
            disabled={pending}
          />
        </form>
      </CardContent>
    </Card>
  );
}
