import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  adminCreateUserSkillDraft,
  adminGetUserSkillDetail,
  adminDisableUserSkill,
  adminPublishUserSkill,
  adminSaveUserSkillRawDraft,
  fetchAdminCustomization,
  fetchAdminUsers,
  updateAdminSkillPolicy,
  updateAdminToolPolicy,
  type AdminUser,
  type SkillDetail,
  type SkillPolicyItem,
  type ToolPolicyItem,
  type UserCustomization,
} from "../../lib/api";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { Input } from "../../components/ui/input";
import { Bot, RefreshCw, Search, Settings } from "../../components/ui/icons";

const ADMIN_TOKEN_KEY = "pai_admin_token";

function sourceLabel(source: string) {
  if (source === "builtin") return "内置";
  if (source === "mcp") return "MCP";
  if (source === "user") return "用户";
  return source || "未知";
}

function groupBySource<T extends { source: string }>(rows: T[]) {
  const groups = new Map<string, T[]>();
  for (const row of rows) {
    const source = row.source || "unknown";
    groups.set(source, [...(groups.get(source) || []), row]);
  }
  return Array.from(groups.entries());
}

export function AdminPage() {
  const queryClient = useQueryClient();
  const [adminToken, setAdminToken] = useState(localStorage.getItem(ADMIN_TOKEN_KEY) || "");
  const [editingToken, setEditingToken] = useState(adminToken);
  const [keyword, setKeyword] = useState("");
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [feedback, setFeedback] = useState("");
  const [newToolSource, setNewToolSource] = useState("builtin");
  const [newToolName, setNewToolName] = useState("");
  const [newSkillSource, setNewSkillSource] = useState("builtin");
  const [newSkillSlug, setNewSkillSlug] = useState("");
  const [skillName, setSkillName] = useState("");
  const [skillRequest, setSkillRequest] = useState("");
  const [updateSkillSlug, setUpdateSkillSlug] = useState("");
  const [editorSkillSlug, setEditorSkillSlug] = useState("");
  const [editorSkillName, setEditorSkillName] = useState("");
  const [editorSkillContent, setEditorSkillContent] = useState("");

  useEffect(() => {
    if (adminToken) {
      localStorage.setItem(ADMIN_TOKEN_KEY, adminToken);
    } else {
      localStorage.removeItem(ADMIN_TOKEN_KEY);
    }
  }, [adminToken]);

  const usersQuery = useQuery<AdminUser[]>({
    queryKey: ["admin-users", adminToken],
    enabled: !!adminToken,
    queryFn: () => fetchAdminUsers(adminToken),
  });

  const filteredUsers = useMemo(() => {
    const rows = usersQuery.data || [];
    const q = keyword.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) => {
      const hay = `${row.id} ${row.email || ""} ${row.platform}:${row.platform_id} ${row.nickname}`.toLowerCase();
      return hay.includes(q);
    });
  }, [usersQuery.data, keyword]);

  useEffect(() => {
    if (selectedUserId) return;
    if (!filteredUsers.length) return;
    setSelectedUserId(filteredUsers[0].id);
  }, [filteredUsers, selectedUserId]);

  const customizationQuery = useQuery<UserCustomization>({
    queryKey: ["admin-customization", adminToken, selectedUserId],
    enabled: !!adminToken && !!selectedUserId,
    queryFn: () => fetchAdminCustomization(selectedUserId!, adminToken),
  });

  const toolMutation = useMutation({
    mutationFn: (payload: { userId: number; source: string; name: string; enabled: boolean }) =>
      updateAdminToolPolicy(
        payload.userId,
        { source: payload.source, name: payload.name, enabled: payload.enabled },
        adminToken
      ),
    onSuccess: async (_, vars) => {
      setFeedback("工具策略已保存。");
      await queryClient.invalidateQueries({ queryKey: ["admin-customization", adminToken, vars.userId] });
    },
    onError: (err) => setFeedback(err instanceof Error ? err.message : "工具策略保存失败。"),
  });

  const skillMutation = useMutation({
    mutationFn: (payload: { userId: number; source: string; slug: string; enabled: boolean }) =>
      updateAdminSkillPolicy(
        payload.userId,
        { source: payload.source, slug: payload.slug, enabled: payload.enabled },
        adminToken
      ),
    onSuccess: async (_, vars) => {
      setFeedback("技能策略已保存。");
      await queryClient.invalidateQueries({ queryKey: ["admin-customization", adminToken, vars.userId] });
    },
    onError: (err) => setFeedback(err instanceof Error ? err.message : "技能策略保存失败。"),
  });

  const createSkillDraftMutation = useMutation({
    mutationFn: (payload: { userId: number; request: string; skill_name?: string; skill_slug?: string }) =>
      adminCreateUserSkillDraft(
        payload.userId,
        {
          request: payload.request,
          skill_name: payload.skill_name,
          skill_slug: payload.skill_slug,
        },
        adminToken
      ),
    onSuccess: async (res, vars) => {
      setFeedback(`技能草稿已生成：${res.slug} v${res.version}`);
      setUpdateSkillSlug(res.slug);
      await queryClient.invalidateQueries({ queryKey: ["admin-customization", adminToken, vars.userId] });
    },
    onError: (err) => setFeedback(err instanceof Error ? err.message : "技能草稿生成失败。"),
  });

  const publishSkillMutation = useMutation({
    mutationFn: (payload: { userId: number; slug: string }) =>
      adminPublishUserSkill(payload.userId, payload.slug, adminToken),
    onSuccess: async (_res, vars) => {
      setFeedback(`技能已发布：${vars.slug}`);
      await queryClient.invalidateQueries({ queryKey: ["admin-customization", adminToken, vars.userId] });
    },
    onError: (err) => setFeedback(err instanceof Error ? err.message : "技能发布失败。"),
  });

  const disableSkillMutation = useMutation({
    mutationFn: (payload: { userId: number; slug: string }) =>
      adminDisableUserSkill(payload.userId, payload.slug, adminToken),
    onSuccess: async (_res, vars) => {
      setFeedback(`技能已停用：${vars.slug}`);
      await queryClient.invalidateQueries({ queryKey: ["admin-customization", adminToken, vars.userId] });
    },
    onError: (err) => setFeedback(err instanceof Error ? err.message : "技能停用失败。"),
  });

  const loadSkillDetailMutation = useMutation({
    mutationFn: (payload: { userId: number; slug: string }) =>
      adminGetUserSkillDetail(payload.userId, payload.slug, adminToken, "user"),
    onSuccess: (res: SkillDetail) => {
      setEditorSkillSlug(res.slug);
      setEditorSkillName(res.name || "");
      setEditorSkillContent(res.content_md || "");
      setUpdateSkillSlug(res.slug);
      setFeedback(`已加载技能内容：${res.slug}`);
    },
    onError: (err) => setFeedback(err instanceof Error ? err.message : "技能内容加载失败。"),
  });

  const saveRawSkillDraftMutation = useMutation({
    mutationFn: (payload: { userId: number; slug?: string; name?: string; content_md: string }) =>
      adminSaveUserSkillRawDraft(
        payload.userId,
        {
          content_md: payload.content_md,
          skill_slug: payload.slug,
          skill_name: payload.name,
        },
        adminToken
      ),
    onSuccess: async (res, vars) => {
      setEditorSkillSlug(res.slug);
      setUpdateSkillSlug(res.slug);
      setFeedback(`已保存原文草稿：${res.slug} v${res.version}`);
      await queryClient.invalidateQueries({ queryKey: ["admin-customization", adminToken, vars.userId] });
    },
    onError: (err) => setFeedback(err instanceof Error ? err.message : "原文草稿保存失败。"),
  });

  const toolGroups = useMemo(
    () => groupBySource(customizationQuery.data?.tools || []),
    [customizationQuery.data?.tools]
  );
  const skillGroups = useMemo(
    () => groupBySource(customizationQuery.data?.skills || []),
    [customizationQuery.data?.skills]
  );

  return (
    <div className="min-h-screen bg-surface text-content">
      <div className="mx-auto max-w-[1500px] px-4 py-5 lg:px-6">
        <div className="mb-4 flex items-center gap-3 rounded-2xl border border-border bg-surface-card px-4 py-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-content text-surface">
            <Bot size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-lg font-semibold">PAI 管理员控制台</h1>
            <p className="text-sm text-content-tertiary">按用户定制工具与技能可用性</p>
          </div>
          <div className="flex items-center gap-2">
            <Input
              type="password"
              value={editingToken}
              onChange={(e) => setEditingToken(e.target.value)}
              placeholder="输入 X-Admin-Token"
              className="w-[280px]"
            />
            <Button
              onClick={() => {
                setAdminToken(editingToken.trim());
                setSelectedUserId(null);
                setFeedback("");
              }}
            >
              连接
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setAdminToken("");
                setEditingToken("");
                setSelectedUserId(null);
                setFeedback("");
              }}
            >
              清空
            </Button>
          </div>
        </div>

        {!adminToken ? (
          <Card>
            <CardContent className="pt-5">
              <p className="text-sm text-content-secondary">请先输入管理员 Token 以加载用户和策略。</p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[330px_minmax(0,1fr)]">
            <Card className="h-[calc(100vh-140px)]">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold">用户列表</h2>
                  <button
                    onClick={() => queryClient.invalidateQueries({ queryKey: ["admin-users", adminToken] })}
                    className="rounded-lg p-1.5 text-content-tertiary hover:bg-surface-hover hover:text-content"
                  >
                    <RefreshCw size={14} />
                  </button>
                </div>
              </CardHeader>
              <CardContent className="flex h-[calc(100%-48px)] flex-col gap-3">
                <div className="relative">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-content-tertiary" />
                  <Input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="搜索用户"
                    className="pl-8"
                  />
                </div>
                <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
                  {(filteredUsers || []).map((item) => {
                    const active = selectedUserId === item.id;
                    return (
                      <button
                        key={item.id}
                        onClick={() => {
                          setSelectedUserId(item.id);
                          setFeedback("");
                        }}
                        className={`w-full rounded-xl px-3 py-2 text-left transition-colors ${
                          active ? "bg-surface-active text-accent" : "text-content hover:bg-surface-hover"
                        }`}
                      >
                        <p className="text-sm font-medium truncate">#{item.id} · {item.nickname}</p>
                        <p className="text-xs text-content-tertiary truncate">
                          {item.email || `${item.platform}:${item.platform_id}`}
                        </p>
                      </button>
                    );
                  })}
                  {!usersQuery.isLoading && filteredUsers.length === 0 && (
                    <p className="py-8 text-center text-sm text-content-tertiary">暂无可用用户</p>
                  )}
                </div>
              </CardContent>
            </Card>

            <div className="h-[calc(100vh-140px)] overflow-y-auto space-y-4 pr-1">
              {selectedUserId ? (
                <>
                  <Card>
                    <CardHeader>
                      <div className="flex items-center gap-2">
                        <Settings size={16} className="text-accent" />
                        <h2 className="text-sm font-semibold">用户 #{selectedUserId} 定制策略</h2>
                      </div>
                    </CardHeader>
                    <CardContent>
                      <p className="text-xs text-content-tertiary">
                        管理员可按用户开启或关闭工具/技能。变更会实时影响该用户后续对话可用能力。
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
                      <h3 className="text-sm font-semibold">工具策略</h3>
                    </CardHeader>
                    <CardContent>
                      <div className="mb-3 grid gap-2 sm:grid-cols-[120px_minmax(0,1fr)_90px]">
                        <select
                          value={newToolSource}
                          onChange={(e) => setNewToolSource(e.target.value)}
                          className="h-10 rounded-xl border border-border bg-surface-input px-3 text-sm"
                        >
                          <option value="builtin">builtin</option>
                          <option value="mcp">mcp</option>
                        </select>
                        <Input
                          value={newToolName}
                          onChange={(e) => setNewToolName(e.target.value)}
                          placeholder="新增策略项：工具名"
                        />
                        <Button
                          variant="ghost"
                          onClick={() => {
                            if (!selectedUserId || !newToolName.trim()) return;
                            toolMutation.mutate({
                              userId: selectedUserId,
                              source: newToolSource,
                              name: newToolName.trim(),
                              enabled: true,
                            });
                            setNewToolName("");
                          }}
                        >
                          新增
                        </Button>
                      </div>
                      {customizationQuery.isLoading ? (
                        <p className="text-sm text-content-tertiary">加载中...</p>
                      ) : (
                        <div className="space-y-4">
                          {toolGroups.map(([source, rows]) => (
                            <div key={source} className="space-y-2">
                              <p className="text-xs font-medium text-content-tertiary uppercase">{sourceLabel(source)}</p>
                              {rows.map((row: ToolPolicyItem) => (
                                <div key={`${row.source}:${row.name}`} className="flex items-center justify-between rounded-xl border border-border px-3 py-2">
                                  <div className="min-w-0">
                                    <p className="text-sm font-medium">{row.name}</p>
                                    <p className="text-xs text-content-tertiary truncate">{row.description || "无描述"}</p>
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() =>
                                      toolMutation.mutate({
                                        userId: selectedUserId,
                                        source: row.source,
                                        name: row.name,
                                        enabled: !row.enabled,
                                      })
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
                      <h3 className="text-sm font-semibold">技能策略</h3>
                    </CardHeader>
                    <CardContent>
                      <div className="mb-3 grid gap-2 sm:grid-cols-[120px_minmax(0,1fr)_90px]">
                        <select
                          value={newSkillSource}
                          onChange={(e) => setNewSkillSource(e.target.value)}
                          className="h-10 rounded-xl border border-border bg-surface-input px-3 text-sm"
                        >
                          <option value="builtin">builtin</option>
                          <option value="user">user</option>
                        </select>
                        <Input
                          value={newSkillSlug}
                          onChange={(e) => setNewSkillSlug(e.target.value)}
                          placeholder="新增策略项：技能slug"
                        />
                        <Button
                          variant="ghost"
                          onClick={() => {
                            if (!selectedUserId || !newSkillSlug.trim()) return;
                            skillMutation.mutate({
                              userId: selectedUserId,
                              source: newSkillSource,
                              slug: newSkillSlug.trim(),
                              enabled: true,
                            });
                            setNewSkillSlug("");
                          }}
                        >
                          新增
                        </Button>
                      </div>
                      {customizationQuery.isLoading ? (
                        <p className="text-sm text-content-tertiary">加载中...</p>
                      ) : (
                        <div className="space-y-4">
                          {skillGroups.map(([source, rows]) => (
                            <div key={source} className="space-y-2">
                              <p className="text-xs font-medium text-content-tertiary uppercase">{sourceLabel(source)}</p>
                              {rows.map((row: SkillPolicyItem) => (
                                <div key={`${row.source}:${row.slug}`} className="flex items-center justify-between rounded-xl border border-border px-3 py-2">
                                  <div className="min-w-0">
                                    <p className="text-sm font-medium">{row.name}</p>
                                    <p className="text-xs text-content-tertiary truncate">{row.slug} · {row.description || "无描述"}</p>
                                  </div>
                                  <div className="ml-3 flex items-center gap-2">
                                    {row.source === "user" && (
                                      <Button
                                        variant="ghost"
                                        onClick={() =>
                                          loadSkillDetailMutation.mutate({
                                            userId: selectedUserId,
                                            slug: row.slug,
                                          })
                                        }
                                      >
                                        编辑
                                      </Button>
                                    )}
                                    {row.source === "user" && (
                                      <Button
                                        variant="subtle"
                                        onClick={() =>
                                          publishSkillMutation.mutate({
                                            userId: selectedUserId,
                                            slug: row.slug,
                                          })
                                        }
                                      >
                                        发布
                                      </Button>
                                    )}
                                    {row.source === "user" && (
                                      <Button
                                        variant="danger"
                                        onClick={() =>
                                          disableSkillMutation.mutate({
                                            userId: selectedUserId,
                                            slug: row.slug,
                                          })
                                        }
                                      >
                                        停用
                                      </Button>
                                    )}
                                    <button
                                      type="button"
                                      onClick={() =>
                                        skillMutation.mutate({
                                          userId: selectedUserId,
                                          source: row.source,
                                          slug: row.slug,
                                          enabled: !row.enabled,
                                        })
                                      }
                                      className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                                        row.enabled
                                          ? "bg-content text-surface hover:bg-content/90"
                                          : "bg-surface-hover text-content-secondary hover:text-content"
                                      }`}
                                    >
                                      {row.enabled ? "已启用" : "已停用"}
                                    </button>
                                  </div>
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
                      <h3 className="text-sm font-semibold">管理员技能工作台（按用户）</h3>
                    </CardHeader>
                    <CardContent>
                      <div className="space-y-4">
                        <div className="rounded-xl border border-border bg-surface-secondary p-3">
                          <p className="mb-2 text-xs text-content-tertiary">
                            直接编辑技能 Markdown（高定制模式）
                          </p>
                          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_110px]">
                            <Input
                              value={editorSkillSlug}
                              onChange={(e) => setEditorSkillSlug(e.target.value)}
                              placeholder="技能 slug（已有技能可直接填）"
                            />
                            <Button
                              variant="ghost"
                              onClick={() => {
                                if (!selectedUserId || !editorSkillSlug.trim()) return;
                                loadSkillDetailMutation.mutate({
                                  userId: selectedUserId,
                                  slug: editorSkillSlug.trim(),
                                });
                              }}
                              disabled={!editorSkillSlug.trim() || loadSkillDetailMutation.isPending}
                            >
                              加载内容
                            </Button>
                          </div>
                          <div className="mt-2">
                            <Input
                              value={editorSkillName}
                              onChange={(e) => setEditorSkillName(e.target.value)}
                              placeholder="技能名（新建时可选）"
                            />
                          </div>
                          <div className="mt-2">
                            <textarea
                              value={editorSkillContent}
                              onChange={(e) => setEditorSkillContent(e.target.value)}
                              className="min-h-[220px] w-full rounded-xl border border-border bg-surface-input px-3 py-2 text-xs font-mono outline-none focus:ring-2 focus:ring-accent/40"
                              placeholder="在这里粘贴或编辑 SKILL.md 原文。"
                            />
                          </div>
                          <div className="mt-2 flex flex-wrap gap-2">
                            <Button
                              onClick={() => {
                                if (!selectedUserId || !editorSkillContent.trim()) return;
                                saveRawSkillDraftMutation.mutate({
                                  userId: selectedUserId,
                                  slug: editorSkillSlug.trim() || undefined,
                                  name: editorSkillName.trim() || undefined,
                                  content_md: editorSkillContent.trim(),
                                });
                              }}
                              disabled={!editorSkillContent.trim() || saveRawSkillDraftMutation.isPending}
                            >
                              保存原文草稿
                            </Button>
                            <Button
                              variant="subtle"
                              onClick={() => {
                                if (!selectedUserId) return;
                                const target = updateSkillSlug.trim() || editorSkillSlug.trim();
                                if (!target) return;
                                publishSkillMutation.mutate({ userId: selectedUserId, slug: target });
                              }}
                              disabled={!updateSkillSlug.trim() && !editorSkillSlug.trim()}
                            >
                              发布当前技能
                            </Button>
                          </div>
                        </div>
                        <Input
                          value={skillName}
                          onChange={(e) => setSkillName(e.target.value)}
                          placeholder="新技能名（可选）"
                        />
                        <Input
                          value={updateSkillSlug}
                          onChange={(e) => setUpdateSkillSlug(e.target.value)}
                          placeholder="更新已有技能时填 slug（可选）"
                        />
                        <textarea
                          value={skillRequest}
                          onChange={(e) => setSkillRequest(e.target.value)}
                          className="min-h-[120px] w-full resize-none rounded-xl border border-border bg-surface-input px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent/40"
                          placeholder="输入技能需求，例如：为该用户新增邮件总结技能，输出要点和行动项。"
                        />
                        <div className="flex flex-wrap gap-2">
                          <Button
                            onClick={() => {
                              if (!selectedUserId || !skillRequest.trim()) return;
                              createSkillDraftMutation.mutate({
                                userId: selectedUserId,
                                request: skillRequest.trim(),
                                skill_name: skillName.trim() || undefined,
                              });
                            }}
                            disabled={createSkillDraftMutation.isPending || !skillRequest.trim()}
                          >
                            新建草稿
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={() => {
                              if (!selectedUserId || !skillRequest.trim() || !updateSkillSlug.trim()) return;
                              createSkillDraftMutation.mutate({
                                userId: selectedUserId,
                                request: skillRequest.trim(),
                                skill_slug: updateSkillSlug.trim(),
                              });
                            }}
                            disabled={createSkillDraftMutation.isPending || !skillRequest.trim() || !updateSkillSlug.trim()}
                          >
                            更新草稿
                          </Button>
                          <Button
                            variant="subtle"
                            onClick={() => {
                              if (!selectedUserId || !updateSkillSlug.trim()) return;
                              publishSkillMutation.mutate({ userId: selectedUserId, slug: updateSkillSlug.trim() });
                            }}
                            disabled={publishSkillMutation.isPending || !updateSkillSlug.trim()}
                          >
                            发布技能
                          </Button>
                          <Button
                            variant="danger"
                            onClick={() => {
                              if (!selectedUserId || !updateSkillSlug.trim()) return;
                              disableSkillMutation.mutate({ userId: selectedUserId, slug: updateSkillSlug.trim() });
                            }}
                            disabled={disableSkillMutation.isPending || !updateSkillSlug.trim()}
                          >
                            停用技能
                          </Button>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                </>
              ) : (
                <Card>
                  <CardContent className="pt-5">
                    <p className="text-sm text-content-tertiary">请选择一个用户查看策略。</p>
                  </CardContent>
                </Card>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
