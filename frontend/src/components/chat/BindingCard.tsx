import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  consumeBindCode,
  createBindCode,
  fetchIdentities,
  type IdentityItem,
} from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Input } from "../ui/input";
import { Link2 } from "../ui/icons";

interface BindingCardProps {
  token: string | null;
}

const platformLabelMap: Record<string, string> = {
  web: "🌐 Web",
  wechat: "💬 微信",
  qq: "🐧 QQ",
  telegram: "✈️ Telegram",
  feishu: "🐦 飞书",
};

function platformLabel(platform: string) {
  return platformLabelMap[platform] || platform;
}

export function BindingCard({ token }: BindingCardProps) {
  const queryClient = useQueryClient();
  const { setToken } = useAuthStore();
  const [codeInput, setCodeInput] = useState("");
  const [feedback, setFeedback] = useState<string>("");

  const { data: identities = [] } = useQuery<IdentityItem[]>({
    queryKey: ["identities"],
    enabled: !!token,
    queryFn: () => fetchIdentities(token),
  });

  const createMutation = useMutation({
    mutationFn: () => createBindCode(token, 10),
    onSuccess: (data) => {
      setFeedback(`绑定码：${data.code}（${data.ttl_minutes}分钟有效）`);
      queryClient.invalidateQueries({ queryKey: ["identities"] });
    },
    onError: (err: Error) => setFeedback(err.message),
  });

  const consumeMutation = useMutation({
    mutationFn: () => consumeBindCode(token, codeInput.trim()),
    onSuccess: async (data) => {
      setFeedback(data.message);
      if (data.access_token) setToken(data.access_token);
      setCodeInput("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["identities"] }),
        queryClient.invalidateQueries({ queryKey: ["profile"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] }),
      ]);
    },
    onError: (err: Error) => setFeedback(err.message),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/10 text-accent">
            <Link2 size={20} />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-content">跨平台绑定</h2>
            <p className="text-xs text-content-tertiary">一个账号，多端同步</p>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {/* Bound platforms */}
          <div className="rounded-xl border border-border bg-surface-secondary p-3">
            <p className="text-xs font-semibold text-content-secondary mb-2">已绑定平台</p>
            {identities.length === 0 ? (
              <p className="text-xs text-content-tertiary">暂无绑定身份</p>
            ) : (
              <div className="space-y-1.5">
                {identities.map((item, index) => (
                  <div
                    key={`${item.platform}-${item.platform_id}-${index}`}
                    className="flex items-center justify-between text-sm"
                  >
                    <span className="text-content-secondary">{platformLabel(item.platform)}</span>
                    <span className="text-xs text-content-tertiary font-mono">{item.platform_id}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <Button
            className="w-full"
            variant="ghost"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? "生成中..." : "生成绑定码"}
          </Button>

          <div className="space-y-2">
            <Input
              value={codeInput}
              onChange={(e) => setCodeInput(e.target.value.replace(/\D+/g, "").slice(0, 6))}
              placeholder="输入 6 位绑定码"
            />
            <Button
              className="w-full"
              onClick={() => consumeMutation.mutate()}
              disabled={consumeMutation.isPending || codeInput.trim().length !== 6}
            >
              {consumeMutation.isPending ? "绑定中..." : "绑定账号"}
            </Button>
          </div>

          {feedback && (
            <div className="rounded-xl bg-accent/5 border border-accent/20 px-3 py-2 text-xs text-content-secondary">
              {feedback}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
