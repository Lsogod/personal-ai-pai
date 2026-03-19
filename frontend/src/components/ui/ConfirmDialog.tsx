import { useEffect, useRef } from "react";
import { X } from "./icons";
import { Button } from "./button";

interface ConfirmDialogProps {
  open: boolean;
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  variant?: "danger" | "default";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmText = "确定",
  cancelText = "取消",
  variant = "default",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  const onCancelRef = useRef(onCancel);
  onCancelRef.current = onCancel;

  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancelRef.current();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open]);

  // Trap focus
  useEffect(() => {
    if (open && dialogRef.current) {
      const firstBtn = dialogRef.current.querySelector("button");
      firstBtn?.focus();
    }
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title || "确认操作"}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm animate-fade-in"
        onClick={onCancel}
      />
      {/* Dialog */}
      <div
        ref={dialogRef}
        className="relative w-full max-w-[90vw] sm:max-w-sm rounded-2xl border border-border bg-surface-card p-5 shadow-elevated animate-fade-in"
      >
        <button
          type="button"
          onClick={onCancel}
          className="absolute right-3 top-3 rounded-lg p-1 text-content-tertiary hover:text-content hover:bg-surface-hover transition-colors"
          aria-label="关闭"
        >
          <X size={16} />
        </button>
        {title && (
          <h3 className="text-base font-semibold text-content mb-1.5 pr-6">{title}</h3>
        )}
        <p className="text-sm text-content-secondary leading-relaxed mb-5">{message}</p>
        <div className="flex gap-2 justify-end">
          <Button variant="ghost" size="sm" onClick={onCancel}>
            {cancelText}
          </Button>
          <Button
            variant={variant === "danger" ? "danger" : "default"}
            size="sm"
            onClick={onConfirm}
          >
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  );
}

interface PromptDialogProps {
  open: boolean;
  title?: string;
  message: string;
  defaultValue?: string;
  placeholder?: string;
  confirmText?: string;
  cancelText?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export function PromptDialog({
  open,
  title,
  message,
  defaultValue = "",
  placeholder = "",
  confirmText = "确定",
  cancelText = "取消",
  onConfirm,
  onCancel,
}: PromptDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const onCancelRef = useRef(onCancel);
  onCancelRef.current = onCancel;

  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancelRef.current();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open]);

  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [open]);

  if (!open) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onConfirm(inputRef.current?.value || "");
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title || "输入"}
    >
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm animate-fade-in"
        onClick={onCancel}
      />
      <div className="relative w-full max-w-[90vw] sm:max-w-sm rounded-2xl border border-border bg-surface-card p-5 shadow-elevated animate-fade-in">
        <button
          type="button"
          onClick={onCancel}
          className="absolute right-3 top-3 rounded-lg p-1 text-content-tertiary hover:text-content hover:bg-surface-hover transition-colors"
          aria-label="关闭"
        >
          <X size={16} />
        </button>
        {title && (
          <h3 className="text-base font-semibold text-content mb-1.5 pr-6">{title}</h3>
        )}
        <p className="text-sm text-content-secondary leading-relaxed mb-3">{message}</p>
        <form onSubmit={handleSubmit}>
          <input
            ref={inputRef}
            type="text"
            defaultValue={defaultValue}
            placeholder={placeholder}
            className="w-full rounded-xl border border-border bg-surface-input px-3 py-2 text-sm text-content placeholder:text-content-tertiary focus:outline-none focus:ring-2 focus:ring-accent/30 mb-4"
          />
          <div className="flex gap-2 justify-end">
            <Button variant="ghost" size="sm" type="button" onClick={onCancel}>
              {cancelText}
            </Button>
            <Button size="sm" type="submit">
              {confirmText}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
