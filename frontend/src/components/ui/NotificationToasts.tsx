import { Calendar, X } from "./icons";
import { formatHmLocal } from "../../lib/datetime";

export interface ToastItem {
  id: string;
  title: string;
  content: string;
  createdAt: string;
}

interface NotificationToastsProps {
  items: ToastItem[];
  onDismiss: (id: string) => void;
}

function formatTime(iso: string) {
  return formatHmLocal(iso);
}

export function NotificationToasts({ items, onDismiss }: NotificationToastsProps) {
  if (items.length === 0) return null;

  return (
    <div className="pointer-events-none fixed right-4 top-16 z-[80] w-[min(92vw,360px)] space-y-2 xl:top-4">
      {items.map((item) => (
        <div
          key={item.id}
          className="pointer-events-auto animate-slide-in rounded-2xl border border-border bg-surface-card shadow-card backdrop-blur-md"
        >
          <div className="flex items-start gap-3 p-3">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-subtle text-accent">
              <Calendar size={16} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-semibold text-content">{item.title}</p>
                <button
                  type="button"
                  onClick={() => onDismiss(item.id)}
                  className="rounded-md p-1 text-content-tertiary hover:bg-surface-hover hover:text-content transition-colors"
                >
                  <X size={14} />
                </button>
              </div>
              <p className="mt-1 text-sm text-content-secondary whitespace-pre-wrap break-words">
                {item.content}
              </p>
              <p className="mt-2 text-[11px] text-content-tertiary">{formatTime(item.createdAt)}</p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
