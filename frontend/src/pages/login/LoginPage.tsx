import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { apiRequest } from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { useThemeStore } from "../../store/theme";
import { Bot, Sun, Moon, Sparkles, Eye, EyeOff } from "../../components/ui/icons";

type AuthMode = "login_password" | "login_code" | "register_code" | "reset_password";

function validateEmail(email: string) {
  const trimmedEmail = email.trim();
  if (!trimmedEmail) return "请输入邮箱地址。";
  const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailPattern.test(trimmedEmail)) return "请输入有效的邮箱地址。";
  return null;
}

function validateSubmit(
  mode: AuthMode,
  email: string,
  password: string,
  code: string,
  newPassword: string,
  confirmPassword: string
) {
  const emailError = validateEmail(email);
  if (emailError) return emailError;

  if (mode === "login_password") {
    if (!password.trim()) return "请输入密码。";
    return null;
  }
  if (mode === "login_code") {
    if (!code.trim()) return "请输入验证码。";
    return null;
  }
  if (mode === "register_code") {
    if (!password.trim()) return "请输入登录密码。";
    if (password.trim().length < 6) return "密码至少 6 位。";
    if (!confirmPassword.trim()) return "请再次输入登录密码。";
    if (password !== confirmPassword) return "两次输入的密码不一致。";
    if (!code.trim()) return "请输入验证码。";
    return null;
  }
  if (!code.trim()) return "请输入验证码。";
  if (!newPassword.trim()) return "请输入新密码。";
  if (newPassword.trim().length < 6) return "新密码至少 6 位。";
  if (!confirmPassword.trim()) return "请再次输入新密码。";
  if (newPassword !== confirmPassword) return "两次输入的新密码不一致。";
  return null;
}

export function LoginPage() {
  const { setToken } = useAuthStore();
  const { theme, toggleTheme } = useThemeStore();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [mode, setMode] = useState<AuthMode>("login_password");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldownSec, setCooldownSec] = useState(0);
  const [miniappQrOpen, setMiniappQrOpen] = useState(false);
  const [showLoginPassword, setShowLoginPassword] = useState(false);
  const [showLoginConfirmPassword, setShowLoginConfirmPassword] = useState(false);
  const [showResetPassword, setShowResetPassword] = useState(false);
  const [showResetConfirmPassword, setShowResetConfirmPassword] = useState(false);
  const footerRef = useRef<HTMLDivElement | null>(null);
  const miniappQrUrl = (import.meta.env.VITE_MINIAPP_QR_URL as string | undefined) || "/miniapp-qr.jpg";

  useEffect(() => {
    if (cooldownSec <= 0) return;
    const timer = window.setInterval(() => {
      setCooldownSec((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [cooldownSec]);

  useEffect(() => {
    if (!miniappQrOpen) return;
    const onClickOutside = (event: MouseEvent) => {
      if (!footerRef.current) return;
      if (footerRef.current.contains(event.target as Node)) return;
      setMiniappQrOpen(false);
    };
    window.addEventListener("mousedown", onClickOutside);
    return () => window.removeEventListener("mousedown", onClickOutside);
  }, [miniappQrOpen]);

  const sendCodeMutation = useMutation({
    mutationFn: async () => {
      const emailError = validateEmail(email);
      if (emailError) throw new Error(emailError);

      const purpose =
        mode === "register_code" ? "register" : mode === "reset_password" ? "reset_password" : "login";
      return apiRequest("/api/auth/email/send-code", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), purpose }),
      });
    },
    onSuccess: (data: { cooldown_seconds?: number; expire_seconds?: number }) => {
      const cooldown = Number(data?.cooldown_seconds || 60);
      const expireSec = Number(data?.expire_seconds || 600);
      setCooldownSec(Number.isFinite(cooldown) && cooldown > 0 ? cooldown : 60);
      setNotice(`验证码已发送，有效期约 ${Math.max(1, Math.floor(expireSec / 60))} 分钟。`);
      setError(null);
    },
    onError: (err: Error) => {
      setError(err.message);
      setNotice(null);
    },
  });

  const mutation = useMutation({
    mutationFn: async () => {
      const validationError = validateSubmit(mode, email, password, code, newPassword, confirmPassword);
      if (validationError) {
        throw new Error(validationError);
      }
      const trimmedEmail = email.trim();
      if (mode === "login_password") {
        const data = await apiRequest("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email: trimmedEmail, password }),
        });
        return { kind: "token", token: data.access_token as string };
      }
      if (mode === "login_code") {
        const data = await apiRequest("/api/auth/login/code", {
          method: "POST",
          body: JSON.stringify({ email: trimmedEmail, code: code.trim() }),
        });
        return { kind: "token", token: data.access_token as string };
      }
      if (mode === "register_code") {
        const data = await apiRequest("/api/auth/register/code", {
          method: "POST",
          body: JSON.stringify({ email: trimmedEmail, password, confirm_password: confirmPassword, code: code.trim() }),
        });
        return { kind: "token", token: data.access_token as string };
      }
      const data = await apiRequest("/api/auth/password/reset", {
        method: "POST",
        body: JSON.stringify({
          email: trimmedEmail,
          code: code.trim(),
          new_password: newPassword,
          confirm_password: confirmPassword,
        }),
      });
      return { kind: "message", message: String(data.message || "密码已重置，请重新登录。") };
    },
    onSuccess: (result: { kind: "token" | "message"; token?: string; message?: string }) => {
      if (result.kind === "token" && result.token) {
        setToken(result.token);
        setError(null);
        return;
      }
      setMode("login_password");
      setCode("");
      setNewPassword("");
      setConfirmPassword("");
      setPassword("");
      setNotice(result.message || "密码已重置，请重新登录。");
      setError(null);
    },
    onError: (err: Error) => {
      setError(err.message);
      setNotice(null);
    },
  });

  const showPassword = mode === "login_password" || mode === "register_code";
  const showPasswordConfirmation = mode === "register_code";
  const showCode = mode === "login_code" || mode === "register_code" || mode === "reset_password";
  const showNewPassword = mode === "reset_password";
  const showNewPasswordConfirmation = mode === "reset_password";
  const submitLabel =
    mode === "login_password"
      ? "登录"
      : mode === "login_code"
        ? "验证码登录"
        : mode === "register_code"
          ? "注册并登录"
          : "重置密码";
  const isLoginPrimaryMode = mode === "login_password" || mode === "login_code" || mode === "reset_password";

  const setModeSafe = (next: AuthMode) => {
    setMode(next);
    setError(null);
    setNotice(null);
    setCode("");
    setNewPassword("");
    setConfirmPassword("");
    setShowLoginPassword(false);
    setShowLoginConfirmPassword(false);
    setShowResetPassword(false);
    setShowResetConfirmPassword(false);
    if (next !== "register_code") setPassword("");
  };

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
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-content text-surface mb-5 shadow-elevated">
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
          <div className="flex rounded-xl bg-surface-secondary p-1 gap-1">
            <button
              onClick={() => setModeSafe("login_password")}
              className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                isLoginPrimaryMode
                  ? "bg-surface-card border border-border text-content shadow-subtle"
                  : "text-content-secondary hover:text-content"
              }`}
            >
              登录
            </button>
            <button
              onClick={() => setModeSafe("register_code")}
              className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                mode === "register_code"
                  ? "bg-surface-card border border-border text-content shadow-subtle"
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
              onChange={(e) => {
                setEmail(e.target.value);
                if (error) setError(null);
              }}
              onKeyDown={(e) => {
                const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
                if (native.isComposing || native.keyCode === 229) return;
                if (e.key === "Enter") mutation.mutate();
              }}
            />
            {showPassword && (
              <div className="relative">
                <Input
                  className="pr-10"
                  placeholder={mode === "register_code" ? "设置登录密码（至少6位）" : "密码"}
                  type={showLoginPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (error) setError(null);
                  }}
                  onKeyDown={(e) => {
                    const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
                    if (native.isComposing || native.keyCode === 229) return;
                    if (e.key === "Enter") mutation.mutate();
                  }}
                />
                <button
                  type="button"
                  aria-label={showLoginPassword ? "隐藏密码" : "显示密码"}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-content-secondary hover:text-content transition-colors"
                  onClick={() => setShowLoginPassword((prev) => !prev)}
                >
                  {showLoginPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            )}
            {showPasswordConfirmation && (
              <div className="relative">
                <Input
                  className="pr-10"
                  placeholder="确认登录密码"
                  type={showLoginConfirmPassword ? "text" : "password"}
                  value={confirmPassword}
                  onChange={(e) => {
                    setConfirmPassword(e.target.value);
                    if (error) setError(null);
                  }}
                  onKeyDown={(e) => {
                    const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
                    if (native.isComposing || native.keyCode === 229) return;
                    if (e.key === "Enter") mutation.mutate();
                  }}
                />
                <button
                  type="button"
                  aria-label={showLoginConfirmPassword ? "隐藏确认密码" : "显示确认密码"}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-content-secondary hover:text-content transition-colors"
                  onClick={() => setShowLoginConfirmPassword((prev) => !prev)}
                >
                  {showLoginConfirmPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            )}
            {showCode && (
              <div className="flex items-center gap-2">
                <Input
                  className="min-w-0 flex-1"
                  placeholder="验证码"
                  value={code}
                  onChange={(e) => {
                    setCode(e.target.value);
                    if (error) setError(null);
                  }}
                  onKeyDown={(e) => {
                    const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
                    if (native.isComposing || native.keyCode === 229) return;
                    if (e.key === "Enter") mutation.mutate();
                  }}
                />
                <Button
                  type="button"
                  size="sm"
                  className="h-10 shrink-0 whitespace-nowrap px-3"
                  disabled={sendCodeMutation.isPending || cooldownSec > 0}
                  onClick={() => sendCodeMutation.mutate()}
                >
                  {cooldownSec > 0 ? `${cooldownSec}s` : sendCodeMutation.isPending ? "发送中..." : "发验证码"}
                </Button>
              </div>
            )}
            {showNewPassword && (
              <div className="relative">
                <Input
                  className="pr-10"
                  placeholder="新密码（至少6位）"
                  type={showResetPassword ? "text" : "password"}
                  value={newPassword}
                  onChange={(e) => {
                    setNewPassword(e.target.value);
                    if (error) setError(null);
                  }}
                  onKeyDown={(e) => {
                    const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
                    if (native.isComposing || native.keyCode === 229) return;
                    if (e.key === "Enter") mutation.mutate();
                  }}
                />
                <button
                  type="button"
                  aria-label={showResetPassword ? "隐藏新密码" : "显示新密码"}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-content-secondary hover:text-content transition-colors"
                  onClick={() => setShowResetPassword((prev) => !prev)}
                >
                  {showResetPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            )}
            {showNewPasswordConfirmation && (
              <div className="relative">
                <Input
                  className="pr-10"
                  placeholder="确认新密码"
                  type={showResetConfirmPassword ? "text" : "password"}
                  value={confirmPassword}
                  onChange={(e) => {
                    setConfirmPassword(e.target.value);
                    if (error) setError(null);
                  }}
                  onKeyDown={(e) => {
                    const native = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
                    if (native.isComposing || native.keyCode === 229) return;
                    if (e.key === "Enter") mutation.mutate();
                  }}
                />
                <button
                  type="button"
                  aria-label={showResetConfirmPassword ? "隐藏确认新密码" : "显示确认新密码"}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-content-secondary hover:text-content transition-colors"
                  onClick={() => setShowResetConfirmPassword((prev) => !prev)}
                >
                  {showResetConfirmPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            )}
          </div>

          {notice && !error && (
            <div className="rounded-xl bg-success/10 px-3 py-2 text-sm text-success">
              {notice}
            </div>
          )}
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
            {mutation.isPending ? "处理中..." : submitLabel}
          </Button>

          {isLoginPrimaryMode && (
            <div className="flex items-center justify-between text-xs">
              {mode === "login_password" ? (
                <button
                  type="button"
                  className="text-content-secondary hover:text-content transition-colors"
                  onClick={() => setModeSafe("login_code")}
                >
                  使用验证码登录
                </button>
              ) : mode === "login_code" ? (
                <button
                  type="button"
                  className="text-content-secondary hover:text-content transition-colors"
                  onClick={() => setModeSafe("login_password")}
                >
                  使用密码登录
                </button>
              ) : (
                <button
                  type="button"
                  className="text-content-secondary hover:text-content transition-colors"
                  onClick={() => setModeSafe("login_password")}
                >
                  返回登录
                </button>
              )}

              {mode !== "reset_password" ? (
                <button
                  type="button"
                  className="text-content-secondary hover:text-content transition-colors"
                  onClick={() => setModeSafe("reset_password")}
                >
                  忘记密码
                </button>
              ) : (
                <span className="text-content-tertiary">通过邮箱验证码重置</span>
              )}
            </div>
          )}

          <div className="text-xs text-center text-content-secondary">
            {mode === "register_code" ? (
              <>
                已有账号？
                <button
                  type="button"
                  className="ml-1 text-content hover:text-content-secondary transition-colors"
                  onClick={() => setModeSafe("login_password")}
                >
                  点击登录
                </button>
              </>
            ) : (
              <>
                没有账号？
                <button
                  type="button"
                  className="ml-1 text-content hover:text-content-secondary transition-colors"
                  onClick={() => setModeSafe("register_code")}
                >
                  点击注册
                </button>
              </>
            )}
          </div>
        </div>

        <div ref={footerRef} className="relative mt-5 text-xs text-content-tertiary text-center">
          支持 Web · Telegram ·{" "}
          <button
            type="button"
            className="text-content-secondary hover:text-content transition-colors"
            onClick={() => setMiniappQrOpen((prev) => !prev)}
          >
            微信小程序
          </button>{" "}
          · 微信 · QQ · 飞书

          {miniappQrOpen && (
            <div className="absolute left-1/2 bottom-full z-20 mb-3 w-56 -translate-x-1/2 rounded-xl border border-border bg-surface-card p-3 shadow-elevated">
              <img
                src={miniappQrUrl}
                alt="微信小程序二维码"
                className="h-44 w-44 mx-auto rounded-lg border border-border object-cover bg-surface"
              />
              <p className="mt-2 text-[11px] text-content-secondary">微信扫码体验小程序</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
