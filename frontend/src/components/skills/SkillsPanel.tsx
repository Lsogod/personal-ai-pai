import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";

import {
  createSkillDraft,
  disableSkill,
  fetchSkillDetail,
  fetchSkills,
  publishSkill,
  type SkillDetail,
  type SkillItem
} from "../../lib/api";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Input } from "../ui/input";

interface SkillsPanelProps {
  token: string | null;
}

function prettyApiError(err: unknown): string {
  const fallback = "请求失败，请稍后重试。";
  if (!(err instanceof Error)) {
    return fallback;
  }
  try {
    const parsed = JSON.parse(err.message);
    if (parsed?.detail) {
      return typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    }
  } catch {
    return err.message || fallback;
  }
  return err.message || fallback;
}

export function SkillsPanel({ token }: SkillsPanelProps) {
  const queryClient = useQueryClient();
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [skillName, setSkillName] = useState("");
  const [requestText, setRequestText] = useState("");
  const [draftPreview, setDraftPreview] = useState("");
  const [feedback, setFeedback] = useState("");

  const { data: skills = [] } = useQuery<SkillItem[]>({
    queryKey: ["skills"],
    queryFn: () => fetchSkills(token)
  });

  const selected = useMemo(
    () => skills.find((item) => `${item.source}:${item.slug}` === selectedKey),
    [skills, selectedKey]
  );

  const { data: selectedDetail } = useQuery<SkillDetail>({
    queryKey: ["skill-detail", selected?.source, selected?.slug],
    enabled: !!selected,
    queryFn: () => fetchSkillDetail(selected!.slug, selected!.source, token)
  });

  const draftMutation = useMutation({
    mutationFn: (payload: { update: boolean }) =>
      createSkillDraft(
        {
          request: requestText,
          skill_name: payload.update ? undefined : skillName || undefined,
          skill_slug: payload.update ? selected?.slug : undefined
        },
        token
      ),
    onSuccess: async (res) => {
      setDraftPreview(res.content_md);
      setSelectedKey(`user:${res.slug}`);
      setFeedback(`草稿已保存：${res.slug} v${res.version}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["skill-detail", "user", res.slug] })
      ]);
    },
    onError: (err) => {
      setFeedback(prettyApiError(err));
    }
  });

  const publishMutation = useMutation({
    mutationFn: () => publishSkill(selected!.slug, token),
    onSuccess: async () => {
      setFeedback(`已发布技能：${selected?.slug}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["skill-detail", selected?.source, selected?.slug] })
      ]);
    },
    onError: (err) => {
      setFeedback(prettyApiError(err));
    }
  });

  const disableMutation = useMutation({
    mutationFn: () => disableSkill(selected!.slug, token),
    onSuccess: async () => {
      setFeedback(`已停用技能：${selected?.slug}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["skill-detail", selected?.source, selected?.slug] })
      ]);
    },
    onError: (err) => {
      setFeedback(prettyApiError(err));
    }
  });

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-slate-900">我的技能</h2>
        </CardHeader>
        <CardContent className="space-y-3 pt-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => queryClient.invalidateQueries({ queryKey: ["skills"] })}
          >
            刷新列表
          </Button>
          <div className="space-y-2">
            {skills.length === 0 ? (
              <p className="text-sm text-slate-500">暂无技能。</p>
            ) : (
              skills.map((item) => {
                const itemKey = `${item.source}:${item.slug}`;
                const active = selectedKey === itemKey;
                const sourceLabel = item.source === "builtin" ? "内置" : "用户";
                return (
                  <button
                    key={itemKey}
                    type="button"
                    onClick={() => {
                      setSelectedKey(itemKey);
                      setDraftPreview("");
                      setFeedback("");
                    }}
                    className={[
                      "w-full rounded-lg border px-3 py-2 text-left",
                      active
                        ? "border-slate-900 bg-slate-100"
                        : "border-slate-200 bg-white hover:bg-slate-50"
                    ].join(" ")}
                  >
                    <p className="text-sm font-semibold text-slate-900">{item.name}</p>
                    <p className="mt-1 text-xs text-slate-500">{item.slug}</p>
                    <p className="mt-1 text-xs text-slate-600">
                      {sourceLabel} · {item.status} · v{item.active_version}
                    </p>
                  </button>
                );
              })
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-slate-900">技能工作台</h2>
        </CardHeader>
        <CardContent className="space-y-3 pt-3">
          <Input
            value={skillName}
            onChange={(e) => setSkillName(e.target.value)}
            placeholder="新技能名（可选，例如 translator-pro）"
          />
          <textarea
            value={requestText}
            onChange={(e) => setRequestText(e.target.value)}
            className="min-h-[110px] w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-slate-500"
            placeholder="输入技能需求，例如：用于法律合同翻译，保持术语一致，输出先给译文后给风险提示。"
          />
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => {
                setFeedback("");
                draftMutation.mutate({ update: false });
              }}
              disabled={!requestText.trim() || draftMutation.isPending}
            >
              生成草稿
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setFeedback("");
                draftMutation.mutate({ update: true });
              }}
              disabled={!selected || selected.source !== "user" || !requestText.trim() || draftMutation.isPending}
            >
              更新当前技能
            </Button>
            <Button
              variant="subtle"
              onClick={() => publishMutation.mutate()}
              disabled={!selected || selected.read_only || publishMutation.isPending}
            >
              发布
            </Button>
            <Button
              variant="ghost"
              onClick={() => disableMutation.mutate()}
              disabled={!selected || selected.read_only || disableMutation.isPending}
            >
              停用
            </Button>
          </div>

          {feedback ? (
            <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
              {feedback}
            </p>
          ) : null}

          <div className="rounded-lg border border-slate-200 bg-white px-3 py-3">
            <p className="text-xs text-slate-500">
              预览来源：
              {draftPreview
                ? "本次生成草稿"
                : selected
                  ? `已选技能 ${selected.source}:${selected.slug} 当前版本`
                  : "未选择"}
            </p>
            <div className="prose prose-sm mt-2 max-w-none text-slate-800">
              <ReactMarkdown>
                {draftPreview || selectedDetail?.content_md || "这里会显示技能 Markdown 预览。"}
              </ReactMarkdown>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
