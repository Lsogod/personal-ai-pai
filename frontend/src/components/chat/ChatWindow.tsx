import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

import { Image, Bot, ArrowUpCircle, X, Copy, Check } from "../ui/icons";
import { formatHmLocal } from "../../lib/datetime";

export interface ChatMessage {
  role: string;
  content: string;
  created_at: string;
}

type RenderChatMessage = ChatMessage & {
  __streaming?: boolean;
};

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

interface SelectedImage {
  id: string;
  name: string;
  dataUrl: string;
}

const MAX_IMAGES = 6;

function normalizeMarkdown(content: string) {
  const raw = (content || "").replace(/\r\n/g, "\n");
  return raw.replace(/<br\s*\/?>/gi, "\n");
}

function nodeToString(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") {
    return "";
  }
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map((item) => nodeToString(item)).join("");
  }
  if (typeof node === "object" && "props" in node) {
    return nodeToString((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

async function copyText(text: string) {
  if (!text) return;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function MarkdownPre({
  className,
  children,
}: {
  className?: string;
  children?: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const codeText = nodeToString(children).replace(/\n$/, "");

  async function handleCopy() {
    try {
      await copyText(codeText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="relative group">
      <button
        type="button"
        onClick={handleCopy}
        className="absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded-md border border-border bg-surface-card px-2 py-1 text-[11px] text-content-secondary hover:text-content hover:bg-surface-hover transition-colors"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {copied ? "已复制" : "复制"}
      </button>
      <pre className={`${className || ""} pt-9`}>{children}</pre>
    </div>
  );
}

function formatTime(iso: string) {
  return formatHmLocal(iso);
}

export function ChatWindow({ history, streamingReply, pending, onSend, profile }: ChatWindowProps) {
  const [content, setContent] = useState("");
  const [images, setImages] = useState<SelectedImage[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropAreaRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);
  const streamingStartedAtRef = useRef<string>(new Date().toISOString());

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

  useEffect(() => {
    if (streamingReply) return;
    streamingStartedAtRef.current = new Date().toISOString();
  }, [streamingReply]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "0px";
    const next = Math.max(44, Math.min(el.scrollHeight, 180));
    el.style.height = `${next}px`;
  }, [content]);

  async function fileToDataUrl(file: File): Promise<string> {
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(new Error("file_read_error"));
      reader.readAsDataURL(file);
    });
  }

  async function attachImageFile(file: File) {
    if (!file.type.startsWith("image/")) return;
    const dataUrl = await fileToDataUrl(file).catch(() => "");
    if (!dataUrl) return;
    setImages((prev) => {
      if (prev.length >= MAX_IMAGES) return prev;
      return [
        ...prev,
        {
          id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
          name: file.name || "上传图片",
          dataUrl,
        },
      ];
    });
  }

  async function handleSubmit(event?: FormEvent) {
    event?.preventDefault();
    const trimmed = content.trim();
    const imageUrls = images.map((item) => item.dataUrl);
    if (!trimmed && imageUrls.length === 0) return;

    setContent("");
    setImages([]);
    setIsDragOver(false);

    try {
      await onSend(trimmed, imageUrls);
    } catch {
      setContent(trimmed);
      setImages(
        imageUrls.map((dataUrl, idx) => ({
          id: `restore-${idx}-${Date.now()}`,
          name: `上传图片${idx + 1}`,
          dataUrl,
        }))
      );
    }
  }

  function handlePickImage() {
    if (pending) return;
    fileInputRef.current?.click();
  }

  function handleClearImages() {
    setImages([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function handleRemoveImage(id: string) {
    setImages((prev) => prev.filter((item) => item.id !== id));
  }

  async function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files || []);
    if (files.length === 0) return;
    for (const file of files) {
      // eslint-disable-next-line no-await-in-loop
      await attachImageFile(file);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
    const composing = Boolean(native.isComposing) || isComposingRef.current || native.keyCode === 229;
    if (composing) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  async function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const items = Array.from(e.clipboardData?.items || []).filter(
      (item) => item.kind === "file" && item.type.startsWith("image/")
    );
    if (items.length === 0) return;
    e.preventDefault();
    for (const item of items) {
      const file = item.getAsFile();
      if (!file) continue;
      // eslint-disable-next-line no-await-in-loop
      await attachImageFile(file);
    }
  }

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    const hasImage = Array.from(e.dataTransfer?.items || []).some(
      (item) => item.kind === "file" && item.type.startsWith("image/")
    );
    if (!hasImage) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    setIsDragOver(true);
  }

  function handleDragLeave(e: React.DragEvent<HTMLDivElement>) {
    if (!dropAreaRef.current) return;
    const nextTarget = e.relatedTarget as Node | null;
    if (nextTarget && dropAreaRef.current.contains(nextTarget)) return;
    setIsDragOver(false);
  }

  async function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer?.files || []).filter((item) => item.type.startsWith("image/"));
    if (files.length === 0) return;
    for (const file of files) {
      // eslint-disable-next-line no-await-in-loop
      await attachImageFile(file);
    }
  }

  const lastHistory = history.length > 0 ? history[history.length - 1] : null;
  const shouldShowStreaming =
    !!streamingReply &&
    !(
      lastHistory &&
      lastHistory.role === "assistant" &&
      (lastHistory.content || "").trim() === (streamingReply || "").trim()
    );

  const allMessages: RenderChatMessage[] = [
    ...history,
    ...(shouldShowStreaming
      ? [
          {
            role: "assistant",
            content: streamingReply,
            created_at: streamingStartedAtRef.current,
            __streaming: true,
          },
        ]
      : []),
  ];

  const isEmpty = allMessages.length === 0;

  return (
    <div className="flex flex-col h-full rounded-2xl border border-border bg-surface-card overflow-hidden">
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
                key={msg.__streaming ? "streaming-assistant" : `${msg.created_at}-${idx}`}
                className={`flex gap-3 ${msg.__streaming ? "" : "animate-fade-in"} ${
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
                    <div className="prose prose-sm max-w-none markdown-chat">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm, remarkBreaks]}
                        components={{
                          a: ({ ...props }) => (
                            <a {...props} target="_blank" rel="noreferrer noopener" />
                          ),
                          pre: ({ className, children }) => (
                            <MarkdownPre className={className}>{children}</MarkdownPre>
                          ),
                          code: ({ inline, className, children, ...props }) => {
                            if (inline) {
                              return (
                                <code {...props} className={className}>
                                  {children}
                                </code>
                              );
                            }
                            return <code {...props} className={className}>{children}</code>;
                          },
                          table: ({ children }) => (
                            <div className="markdown-table-wrap">
                              <table>{children}</table>
                            </div>
                          ),
                        }}
                      >
                        {normalizeMarkdown(msg.content)}
                      </ReactMarkdown>
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

      <div className="shrink-0 border-t border-border p-4">
        <div className="max-w-3xl mx-auto">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            onChange={handleFileChange}
            className="hidden"
          />
          {images.length > 0 && (
            <div className="mb-2 animate-fade-in rounded-xl border border-border bg-surface-input px-3 py-2">
              <div className="mb-2 text-[11px] text-content-tertiary">已选择 {images.length} 张图片</div>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {images.map((item) => (
                  <div key={item.id} className="relative h-14 w-14 shrink-0">
                    <img
                      src={item.dataUrl}
                      alt={item.name}
                      className="h-14 w-14 rounded-lg object-cover border border-border"
                    />
                    <button
                      type="button"
                      onClick={() => handleRemoveImage(item.id)}
                      className="absolute -right-1 -top-1 rounded-full bg-surface p-0.5 text-content-tertiary hover:text-content border border-border"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={handleClearImages}
                  className="h-14 shrink-0 rounded-lg border border-border px-2 text-[11px] text-content-tertiary hover:text-content hover:bg-surface-hover"
                >
                  清空
                </button>
              </div>
            </div>
          )}
          <form onSubmit={handleSubmit}>
            <div
              ref={dropAreaRef}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`relative rounded-2xl border transition-colors ${
                isDragOver ? "border-accent bg-accent-subtle/40" : "border-border bg-surface-input"
              }`}
            >
              <button
                type="button"
                onClick={handlePickImage}
                className={`absolute left-2 inset-y-0 my-auto z-10 flex h-9 w-9 items-center justify-center rounded-xl transition-colors ${
                  images.length > 0
                    ? "text-accent bg-surface-hover"
                    : "text-content-tertiary hover:text-content-secondary hover:bg-surface-hover"
                }`}
              >
                <Image size={20} />
              </button>
              <textarea
                ref={inputRef}
                placeholder="输入消息..."
                value={content}
                onChange={(e) => setContent(e.target.value)}
                onCompositionStart={() => {
                  isComposingRef.current = true;
                }}
                onCompositionEnd={() => {
                  isComposingRef.current = false;
                }}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                disabled={pending}
                rows={1}
                className="w-full resize-none rounded-2xl border-0 bg-transparent pl-14 pr-14 py-3 text-sm text-content placeholder:text-content-tertiary focus-visible:outline-none focus-visible:ring-0"
                style={{ minHeight: "44px", maxHeight: "180px" }}
              />
              <button
                type="submit"
                disabled={pending || (!content.trim() && images.length === 0)}
                className="absolute right-2 inset-y-0 my-auto z-10 flex h-10 w-10 items-center justify-center rounded-xl text-accent hover:bg-accent-subtle disabled:opacity-30 disabled:hover:bg-transparent transition-all"
              >
                <ArrowUpCircle size={22} />
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
