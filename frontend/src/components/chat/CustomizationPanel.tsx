import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchCustomization,
  updateCustomizationSkillPolicy,
  updateCustomizationToolPolicy,
  type SkillPolicyItem,
  type ToolPolicyItem,
  type UserCustomization,
} from "../../lib/api";
import { Card, CardContent, CardHeader } from "../ui/card";
import { Settings, RefreshCw } from "../ui/icons";

interface CustomizationPanelProps {
  token: string | null;
}

function sourceLabel(source: string) {
  if (source === "builtin") return "内置";
  if (source === "mcp") return "MCP";
  if (source === "user") return "用户";
  return source || "未知";
}

function groupBySource<T extends { source: string }>(rows: T[]) {
  const groups = new Map<string, T[]>();
  for (const item of rows) {
    const key = item.source || "unknown";
    groups.set(key, [...(groups.get(key) || []), item]);
  }
  return Array.from(groups.entries());
}

export function CustomizationPanel({ token }: CustomizationPanelProps) {
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState("");

  const { data, isLoading } = useQuery<UserCustomization>({
    queryKey: ["customization"],
    queryFn: () => fetchCustomization(token),
  });

  const toolMutation = useMutation({
    mutationFn: (payload: { source: string; name: string; enabled: boolean }) =>
      updateCustomizationToolPolicy(payload, token),
    onSuccess: async () => {
      setFeedback("工具策略已更新。");
      await queryClient.invalidateQueries({ queryKey: ["customization"] });
    },
    onError: (err) => {
      setFeedback(err instanceof Error ? err.message : "工具策略更新失败。");
    },
  });

  const skillMutation = useMutation({
    mutationFn: (payload: { source: string; slug: string; enabled: boolean }) =>
      updateCustomizationSkillPolicy(payload, token),
    onSuccess: async () => {
      setFeedback("技能策略已更新。");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["customization"] }),
        queryClient.invalidateQueries({ queryKey: ["skills"] }),
      ]);
    },
    onError: (err) => {
      setFeedback(err instanceof Error ? err.message : "技能策略更新失败。");
    },
  });

  const toolGroups = useMemo(() => groupBySource(data?.tools || []), [data?.tools]);
  const skillGroups = useMemo(() => groupBySource(data?.skills || []), [data?.skills]);

  return (
    <div className="space-y-4 max-w-2xl mx-auto">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Settings size={16} className="text-accent" />
              <h2 className="text-sm font-semibold text-content">能力定制</h2>
            </div>
            <button
              onClick={() => queryClient.invalidateQueries({ queryKey: ["customization"] })}
              className="p-1.5 rounded-lg text-content-tertiary hover:text-content hover:bg-surface-hover transition-colors"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-content-tertiary">
            你可以按账号维度启用/禁用工具和技能。关闭后，助手不会在该账号下调用对应能力。
          </p>
          {feedback && (
            <div className="mt-3 rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm text-content-secondary">
              {feedback}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <h3 className="text-sm font-semibold text-content">工具开关</h3>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-content-tertiary">加载中...</p>
          ) : toolGroups.length === 0 ? (
            <p className="text-sm text-content-tertiary">暂无可配置工具。</p>
          ) : (
            <div className="space-y-4">
              {toolGroups.map(([source, rows]) => (
                <div key={source} className="space-y-2">
                  <p className="text-xs font-medium text-content-tertiary uppercase">{sourceLabel(source)}</p>
                  {rows.map((row: ToolPolicyItem) => (
                    <div
                      key={`${row.source}:${row.name}`}
                      className="flex items-center justify-between rounded-xl border border-border px-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-content">{row.name}</p>
                        <p className="text-xs text-content-tertiary truncate">{row.description || "无描述"}</p>
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          toolMutation.mutate({ source: row.source, name: row.name, enabled: !row.enabled })
                        }
                        className={`ml-3 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                          row.enabled
                            ? "bg-content text-surface hover:bg-content/90"
                            : "bg-surface-hover text-content-secondary hover:text-content"
                        }`}
                      >
                        {row.enabled ? "已启用" : "已停用"}
                      </button>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <h3 className="text-sm font-semibold text-content">技能开关</h3>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-sm text-content-tertiary">加载中...</p>
          ) : skillGroups.length === 0 ? (
            <p className="text-sm text-content-tertiary">暂无可配置技能。</p>
          ) : (
            <div className="space-y-4">
              {skillGroups.map(([source, rows]) => (
                <div key={source} className="space-y-2">
                  <p className="text-xs font-medium text-content-tertiary uppercase">{sourceLabel(source)}</p>
                  {rows.map((row: SkillPolicyItem) => (
                    <div
                      key={`${row.source}:${row.slug}`}
                      className="flex items-center justify-between rounded-xl border border-border px-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-content">{row.name}</p>
                        <p className="text-xs text-content-tertiary truncate">
                          {row.slug} · {row.description || "无描述"}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          skillMutation.mutate({ source: row.source, slug: row.slug, enabled: !row.enabled })
                        }
                        className={`ml-3 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                          row.enabled
                            ? "bg-content text-surface hover:bg-content/90"
                            : "bg-surface-hover text-content-secondary hover:text-content"
                        }`}
                      >
                        {row.enabled ? "已启用" : "已停用"}
                      </button>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

