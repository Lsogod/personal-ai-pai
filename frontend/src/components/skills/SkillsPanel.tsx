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
  type SkillItem,
} from "../../lib/api";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Input } from "../ui/input";
import { Zap, RefreshCw, Sparkles } from "../ui/icons";

interface SkillsPanelProps {
  token: string | null;
}

function skillStatusLabel(status: string) {
  const key = String(status || "").toUpperCase();
  if (key === "BUILTIN") return "内置";
  if (key === "DRAFT") return "草稿";
  if (key === "PUBLISHED") return "已发布";
  if (key === "DISABLED") return "已停用";
  return status || "未知";
}

function prettyApiError(err: unknown): string {
  const fallback = "请求失败，请稍后重试。";
  if (!(err instanceof Error)) return fallback;
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
    queryFn: () => fetchSkills(token),
  });

  const selected = useMemo(
    () => skills.find((item) => `${item.source}:${item.slug}` === selectedKey),
    [skills, selectedKey]
  );

  const { data: selectedDetail } = useQuery<SkillDetail>({
    queryKey: ["skill-detail", selected?.source, selected?.slug],
    enabled: !!selected,
    queryFn: () => fetchSkillDetail(selected!.slug, selected!.source, token),
  });

  const draftMutation = useMutation({
    mutationFn: (payload: { update: boolean }) =>
      createSkillDraft(
        {
          request: requestText,
          skill_name: payload.update ? undefined : skillName || undefined,
          skill_slug: payload.update ? selected?.slug : undefined,
        },
        token
      ),
    onSuccess: async (res) => {
      setDraftPreview(res.content_md);
      setSelectedKey(`user:${res.slug}`);
      setFeedback(`草稿已保存：${res.slug} v${res.version}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["skill-detail", "user", res.slug] }),
      ]);
    },
    onError: (err) => setFeedback(prettyApiError(err)),
  });

  const publishMutation = useMutation({
    mutationFn: () => publishSkill(selected!.slug, token),
    onSuccess: async () => {
      setFeedback(`已发布技能：${selected?.slug}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["skill-detail", selected?.source, selected?.slug] }),
      ]);
    },
    onError: (err) => setFeedback(prettyApiError(err)),
  });

  const disableMutation = useMutation({
    mutationFn: () => disableSkill(selected!.slug, token),
    onSuccess: async () => {
      setFeedback(`已停用技能：${selected?.slug}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
        queryClient.invalidateQueries({ queryKey: ["skill-detail", selected?.source, selected?.slug] }),
      ]);
    },
    onError: (err) => setFeedback(prettyApiError(err)),
  });

  return (
    <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      {/* Skills list */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Zap size={16} className="text-accent" />
              <h2 className="text-sm font-semibold text-content">我的技能</h2>
            </div>
            <button
              onClick={() => queryClient.invalidateQueries({ queryKey: ["skills"] })}
              className="p-1.5 rounded-lg text-content-tertiary hover:text-content hover:bg-surface-hover transition-colors"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-1">
            {skills.length === 0 ? (
              <p className="text-sm text-content-tertiary text-center py-8">暂无技能</p>
            ) : (
              skills.map((item) => {
                const itemKey = `${item.source}:${item.slug}`;
                const active = selectedKey === itemKey;
                const isBuiltin = item.source === "builtin";
                return (
                  <button
                    key={itemKey}
                    type="button"
                    onClick={() => {
                      setSelectedKey(itemKey);
                      setDraftPreview("");
                      setFeedback("");
                    }}
                    className={`
                      w-full rounded-xl px-3 py-3 text-left transition-all duration-200
                      ${active
                        ? "bg-surface-active text-accent"
                        : "hover:bg-surface-hover text-content"
                      }
                    `}
                  >
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium">{item.name}</p>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-md ${
                        isBuiltin
                          ? "bg-surface-secondary text-content-tertiary"
                          : "bg-surface-active text-accent"
                      }`}>
                        {isBuiltin ? "内置" : "用户"}
                      </span>
                    </div>
                    <p className="text-xs text-content-tertiary mt-0.5">{item.slug}</p>
                    <p className="text-xs text-content-tertiary mt-0.5">
                      {skillStatusLabel(item.status)} · v{item.active_version}
                    </p>
                  </button>
                );
              })
            )}
          </div>
        </CardContent>
      </Card>

      {/* Workspace */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-accent" />
            <h2 className="text-sm font-semibold text-content">技能工作台</h2>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <Input
              value={skillName}
              onChange={(e) => setSkillName(e.target.value)}
              placeholder="新技能名（可选，例如 translator-pro）"
            />
            <textarea
              value={requestText}
              onChange={(e) => setRequestText(e.target.value)}
              className="min-h-[120px] w-full rounded-xl border border-border bg-surface-input px-4 py-3 text-sm text-content placeholder:text-content-tertiary outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent/50 transition-all resize-none"
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
                <Sparkles size={14} />
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
                variant="danger"
                onClick={() => disableMutation.mutate()}
                disabled={!selected || selected.read_only || disableMutation.isPending}
              >
                停用
              </Button>
            </div>

            {feedback && (
              <div className="rounded-xl bg-accent/5 border border-accent/20 px-4 py-2.5 text-sm text-content-secondary">
                {feedback}
              </div>
            )}

            <div className="rounded-xl border border-border bg-surface-secondary px-4 py-4">
              <p className="text-xs text-content-tertiary mb-3">
                预览：
                {draftPreview
                  ? "本次生成草稿"
                  : selected
                    ? `${selected.source}:${selected.slug}`
                    : "未选择"}
              </p>
              <div className="prose prose-sm max-w-none text-content">
                <ReactMarkdown>
                  {draftPreview || selectedDetail?.content_md || "选择或创建一个技能来查看预览。"}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
