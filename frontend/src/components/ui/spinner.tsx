import { cn } from "../../lib/utils";

interface SpinnerProps {
  className?: string;
  size?: "sm" | "md" | "lg";
}

const sizeClasses = {
  sm: "h-4 w-4 border-2",
  md: "h-6 w-6 border-2",
  lg: "h-8 w-8 border-[3px]",
};

export function Spinner({ className, size = "md" }: SpinnerProps) {
  return (
    <div
      className={cn(
        "animate-spin rounded-full border-content/20 border-t-content",
        sizeClasses[size],
        className
      )}
    />
  );
}

interface SectionLoadingProps {
  text?: string;
}

export function SectionLoading({ text = "加载中..." }: SectionLoadingProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <Spinner size="lg" />
      <span className="text-sm text-content-secondary">{text}</span>
    </div>
  );
}

interface SectionErrorProps {
  message: string;
  onRetry?: () => void;
}

export function SectionError({ message, onRetry }: SectionErrorProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <div className="text-3xl">&#9888;&#65039;</div>
      <span className="text-sm text-danger">{message}</span>
      {onRetry ? (
        <button
          className="text-sm text-accent hover:underline"
          onClick={onRetry}
        >
          重试
        </button>
      ) : null}
    </div>
  );
}

interface EmptyStateProps {
  text?: string;
}

export function EmptyState({ text = "暂无数据" }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-8 gap-2">
      <div className="text-2xl opacity-40">&#128203;</div>
      <span className="text-sm text-content-secondary">{text}</span>
    </div>
  );
}
