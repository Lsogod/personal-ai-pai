import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { apiRequest } from "../../lib/api";
import { useAuthStore } from "../../store/auth";

export function LoginPage() {
  const { setToken } = useAuthStore();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/register";
      const data = await apiRequest(path, {
        method: "POST",
        body: JSON.stringify({ email, password })
      });
      return data.access_token as string;
    },
    onSuccess: (accessToken) => {
      setToken(accessToken);
      setError(null);
    },
    onError: (err: Error) => setError(err.message)
  });

  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900 text-sm font-semibold text-white">
              AI
            </div>
            <div>
              <h1 className="text-xl font-semibold text-slate-900">PAI Chat</h1>
              <p className="text-sm text-slate-500">登录后直接开始对话。</p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Button
              variant={mode === "login" ? "default" : "ghost"}
              onClick={() => setMode("login")}
            >
              登录
            </Button>
            <Button
              variant={mode === "register" ? "default" : "ghost"}
              onClick={() => setMode("register")}
            >
              注册
            </Button>
          </div>
          <Input
            placeholder="邮箱"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Input
            placeholder="密码"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <Button className="w-full" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mode === "login" ? "登录" : "创建账号"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
