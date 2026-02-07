import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  consumeBindCode,
  createBindCode,
  fetchIdentities,
  type IdentityItem
} from "../../lib/api";
import { useAuthStore } from "../../store/auth";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Input } from "../ui/input";

interface BindingCardProps {
  token: string | null;
}

const platformLabelMap: Record<string, string> = {
  web: "Web",
  wechat: "微信",
  qq: "QQ",
  telegram: "Telegram",
  feishu: "飞书"
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
    queryFn: () => fetchIdentities(token)
  });

  const createMutation = useMutation({
    mutationFn: () => createBindCode(token, 10),
    onSuccess: (data) => {
      setFeedback(`绑定码：${data.code}（${data.ttl_minutes}分钟有效）`);
      queryClient.invalidateQueries({ queryKey: ["identities"] });
    },
    onError: (err: Error) => setFeedback(err.message)
  });

  const consumeMutation = useMutation({
    mutationFn: () => consumeBindCode(token, codeInput.trim()),
    onSuccess: async (data) => {
      setFeedback(data.message);
      if (data.access_token) {
        setToken(data.access_token);
      }
      setCodeInput("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["identities"] }),
        queryClient.invalidateQueries({ queryKey: ["profile"] }),
        queryClient.invalidateQueries({ queryKey: ["history"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["stats"] })
      ]);
    },
    onError: (err: Error) => setFeedback(err.message)
  });

  return (
    <Card>
      <CardHeader>
        <h2 className="text-sm font-semibold text-slate-900">跨平台绑定</h2>
      </CardHeader>
      <CardContent className="space-y-3 pt-3">
        <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-2">
          <p className="text-xs font-semibold text-slate-700">已绑定平台</p>
          {identities.length === 0 ? (
            <p className="text-xs text-slate-500">暂无绑定身份</p>
          ) : (
            identities.map((item, index) => (
              <p key={`${item.platform}-${item.platform_id}-${index}`} className="text-xs text-slate-700">
                {platformLabel(item.platform)}: {item.platform_id}
              </p>
            ))
          )}
        </div>

        <Button
          className="w-full"
          variant="ghost"
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending}
        >
          {createMutation.isPending ? "生成中..." : "生成绑定码 (/bind new)"}
        </Button>

        <div className="space-y-2">
          <Input
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value.replace(/\D+/g, "").slice(0, 6))}
            placeholder="输入6位绑定码"
          />
          <Button
            className="w-full"
            onClick={() => consumeMutation.mutate()}
            disabled={consumeMutation.isPending || codeInput.trim().length !== 6}
          >
            {consumeMutation.isPending ? "绑定中..." : "绑定到已有账号 (/bind <code>)"}
          </Button>
        </div>

        {feedback ? <p className="text-xs text-slate-600">{feedback}</p> : null}
      </CardContent>
    </Card>
  );
}
