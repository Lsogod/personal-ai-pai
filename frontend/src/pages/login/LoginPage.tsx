import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { apiRequest } from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { useThemeStore } from "../../store/theme";
import { Bot, Sun, Moon, Sparkles } from "../../components/ui/icons";

export function LoginPage() {
  const { setToken } = useAuthStore();
  const { theme, toggleTheme } = useThemeStore();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/register";
      const data = await apiRequest(path, {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      return data.access_token as string;
    },
    onSuccess: (accessToken) => {
      setToken(accessToken);
      setError(null);
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="min-h-screen bg-surface flex flex-col items-center justify-center p-6 relative">
      {/* Theme toggle */}
      <button
        onClick={toggleTheme}
        className="absolute top-5 right-5 p-2.5 rounded-xl text-content-secondary hover:text-content hover:bg-surface-hover transition-all duration-200"
      >
        {theme === "dark" ? <Sun size={20} /> : <Moon size={20} />}
      </button>

      {/* Logo & Title */}
      <div className="flex flex-col items-center mb-10 animate-fade-in">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-accent text-white mb-5 shadow-elevated">
          <Bot size={32} />
        </div>
        <h1 className="text-3xl font-bold text-content tracking-tight">PAI</h1>
        <p className="text-sm text-content-secondary mt-2 flex items-center gap-1.5">
          <Sparkles size={14} />
          你的私人 AI 助手
        </p>
      </div>

      {/* Login Card */}
      <div className="w-full max-w-sm animate-fade-in">
        <div className="rounded-2xl border border-border bg-surface-card shadow-card p-6 space-y-5">
          {/* Mode Toggle */}
          <div className="flex rounded-xl bg-surface-secondary p-1 gap-1">
            <button
              onClick={() => setMode("login")}
              className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                mode === "login"
                  ? "bg-surface-card text-content shadow-subtle"
                  : "text-content-secondary hover:text-content"
              }`}
            >
              登录
            </button>
            <button
              onClick={() => setMode("register")}
              className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                mode === "register"
                  ? "bg-surface-card text-content shadow-subtle"
                  : "text-content-secondary hover:text-content"
              }`}
            >
              注册
            </button>
          </div>

          <div className="space-y-3">
            <Input
              placeholder="邮箱"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && mutation.mutate()}
            />
            <Input
              placeholder="密码"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && mutation.mutate()}
            />
          </div>

          {error && (
            <div className="rounded-xl bg-danger/10 px-3 py-2 text-sm text-danger">
              {error}
            </div>
          )}

          <Button
            className="w-full"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? "处理中..." : mode === "login" ? "登录" : "创建账号"}
          </Button>
        </div>

        <p className="text-xs text-content-tertiary text-center mt-5">
          支持 Telegram · 微信 · QQ · 飞书 多平台绑定
        </p>
      </div>
    </div>
  );
}
