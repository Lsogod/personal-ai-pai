import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { Button } from "../ui/button";
import { Send, Image, Bot, ArrowUpCircle } from "../ui/icons";

export interface ChatMessage {
  role: string;
  content: string;
  created_at: string;
}

interface Profile {
  ai_name?: string;
  ai_emoji?: string;
  nickname?: string;
}

interface ChatWindowProps {
  history: ChatMessage[];
  streamingReply: string;
  pending: boolean;
  onSend: (content: string, imageUrls: string[]) => Promise<void>;
  profile?: Profile;
}

function formatTime(iso: string) {
  const date = new Date(iso);
  return `${date.getHours().toString().padStart(2, "0")}:${date.getMinutes().toString().padStart(2, "0")}`;
}

export function ChatWindow({ history, streamingReply, pending, onSend, profile }: ChatWindowProps) {
  const [content, setContent] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [showImageInput, setShowImageInput] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [history]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceToBottom > 160) return;
    const frame = requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [streamingReply]);

  async function handleSubmit(event?: FormEvent) {
    event?.preventDefault();
    const trimmed = content.trim();
    const images = imageUrl.trim() ? [imageUrl.trim()] : [];
    if (!trimmed && images.length === 0) return;

    // Clear input immediately for responsive UX; restore if request fails.
    setContent("");
    setImageUrl("");
    setShowImageInput(false);

    try {
      await onSend(trimmed, images);
    } catch {
      setContent(trimmed);
      setImageUrl(images[0] || "");
      setShowImageInput(images.length > 0);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  const allMessages = [
    ...history,
    ...(streamingReply
      ? [{ role: "assistant", content: streamingReply, created_at: new Date().toISOString() }]
      : []),
  ];

  const isEmpty = allMessages.length === 0;

  return (
    <div className="flex flex-col h-full rounded-2xl border border-border bg-surface-card overflow-hidden">
      {/* Messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 lg:p-6">
        {isEmpty ? (
          <div className="flex flex-col items-center justify-center h-full text-center py-20">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-surface-hover text-accent mb-5">
              <Bot size={32} />
            </div>
            <h2 className="text-xl font-semibold text-content mb-2">
              你好！我是 {profile?.ai_name || "PAI"}
            </h2>
            <p className="text-sm text-content-secondary max-w-md">
              我是你的私人 AI 助手，可以帮你记账、管理日程、翻译文档、提醒待办事项，
              还能绑定多平台账号。试着和我说点什么吧！
            </p>
            <div className="grid grid-cols-2 gap-2 mt-6 max-w-sm w-full">
              {["帮我记录午饭 35 元", "今天有什么日程？", "翻译这段英文", "创建一个新技能"].map(
                (hint) => (
                  <button
                    key={hint}
                    onClick={() => {
                      setContent(hint);
                      inputRef.current?.focus();
                    }}
                    className="px-3 py-2.5 rounded-xl border border-border text-xs text-content-secondary hover:bg-surface-hover hover:text-content transition-all duration-200 text-left"
                  >
                    {hint}
                  </button>
                )
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-4 max-w-3xl mx-auto">
            {allMessages.map((msg, idx) => (
              <div
                key={`${msg.created_at}-${idx}`}
                className={`flex gap-3 animate-fade-in ${
                  msg.role === "assistant" ? "justify-start" : "justify-end"
                }`}
              >
                {msg.role === "assistant" && (
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-surface-hover text-accent text-sm">
                    <Bot size={16} />
                  </div>
                )}
                <div
                  className={`max-w-[75%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                    msg.role === "assistant"
                      ? "bg-bubble-ai text-content rounded-tl-md"
                      : "bg-bubble-user text-white rounded-tr-md"
                  }`}
                >
                  {msg.role === "assistant" ? (
                    <div className="prose prose-sm max-w-none">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  ) : (
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                  )}
                  <p
                    className={`mt-1.5 text-[11px] ${
                      msg.role === "assistant" ? "text-content-tertiary" : "text-white/60"
                    }`}
                  >
                    {formatTime(msg.created_at)}
                  </p>
                </div>
                {msg.role !== "assistant" && (
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-content text-surface text-xs font-medium">
                    {profile?.nickname?.charAt(0) || "我"}
                  </div>
                )}
              </div>
            ))}
            {pending && !streamingReply && (
              <div className="flex gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-surface-hover text-accent">
                  <Bot size={16} />
                </div>
                <div className="bg-bubble-ai rounded-2xl rounded-tl-md px-4 py-3">
                  <div className="flex gap-1">
                    <span className="w-2 h-2 rounded-full bg-content-tertiary animate-pulse-soft" style={{ animationDelay: "0ms" }} />
                    <span className="w-2 h-2 rounded-full bg-content-tertiary animate-pulse-soft" style={{ animationDelay: "300ms" }} />
                    <span className="w-2 h-2 rounded-full bg-content-tertiary animate-pulse-soft" style={{ animationDelay: "600ms" }} />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="shrink-0 border-t border-border p-4">
        <div className="max-w-3xl mx-auto">
          {showImageInput && (
            <div className="mb-2 animate-fade-in">
              <input
                type="text"
                placeholder="粘贴图片 URL（用于小票识别）"
                value={imageUrl}
                onChange={(e) => setImageUrl(e.target.value)}
                disabled={pending}
                className="w-full rounded-xl border border-border bg-surface-input px-3 py-2 text-sm text-content placeholder:text-content-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              />
            </div>
          )}
          <form onSubmit={handleSubmit} className="flex items-end gap-2">
            <button
              type="button"
              onClick={() => setShowImageInput(!showImageInput)}
              className={`shrink-0 p-2.5 rounded-xl transition-colors ${
                showImageInput
                  ? "text-accent bg-surface-hover"
                  : "text-content-tertiary hover:text-content-secondary hover:bg-surface-hover"
              }`}
            >
              <Image size={20} />
            </button>
            <div className="flex-1 relative">
              <textarea
                ref={inputRef}
                placeholder="输入消息..."
                value={content}
                onChange={(e) => setContent(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={pending}
                rows={1}
                className="w-full resize-none rounded-2xl border border-border bg-surface-input pl-4 pr-12 py-3 text-sm text-content placeholder:text-content-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 max-h-32"
                style={{ minHeight: "44px" }}
              />
              <button
                type="submit"
                disabled={pending || (!content.trim() && !imageUrl.trim())}
                className="absolute right-2 bottom-2 p-1.5 rounded-xl text-accent hover:bg-accent-subtle disabled:opacity-30 disabled:hover:bg-transparent transition-all"
              >
                <ArrowUpCircle size={24} />
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
