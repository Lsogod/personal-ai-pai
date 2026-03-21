import { Component, ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary] 渲染错误:", error, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center min-h-screen p-8 text-center bg-surface">
          <div className="max-w-md w-full rounded-2xl border border-border bg-surface-card p-8 shadow-elevated">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-danger/10 text-danger mx-auto mb-5 text-3xl">
              ⚠️
            </div>
            <h2 className="text-xl font-semibold text-content mb-2">页面出现错误</h2>
            <p className="text-sm text-content-secondary leading-relaxed mb-6">
              很抱歉，页面加载时遇到了问题。请尝试刷新页面，或点击下方按钮重试。
            </p>
            <div className="flex gap-3 justify-center">
              <button
                type="button"
                onClick={this.handleRetry}
                className="inline-flex items-center justify-center rounded-xl bg-content text-surface px-4 h-10 text-sm font-medium transition-all duration-200 hover:bg-accent-hover"
              >
                重试
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="inline-flex items-center justify-center rounded-xl border border-border bg-surface-card text-content px-4 h-10 text-sm font-medium transition-all duration-200 hover:bg-surface-hover"
              >
                刷新页面
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
