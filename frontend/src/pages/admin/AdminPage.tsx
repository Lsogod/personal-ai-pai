import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  adminDisableSkill,
  adminSetToolSwitch,
  adminSetUserBlock,
  adminSetUserQuota,
  fetchAdminAudit,
  fetchAdminConversationMessages,
  fetchAdminConversations,
  fetchAdminConversationStats,
  fetchAdminDashboard,
  fetchAdminMiniappHomePopup,
  fetchAdminScheduleDelivery,
  fetchAdminSkills,
  fetchAdminTools,
  fetchAdminUsers,
  saveAdminMiniappHomePopup,
  type AdminAuditItem,
  type AdminConversationItem,
  type AdminConversationMessageItem,
  type AdminMiniappHomePopupConfig,
  type AdminSkillsItem,
  type AdminToolItem,
  type AdminUserItem,
} from "../../lib/api";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { Input } from "../../components/ui/input";

const ADMIN_TOKEN_KEY = "pai_admin_token";
const PIE_COLORS = [
  "#2563eb",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#14b8a6",
  "#f97316",
  "#64748b",
  "#ec4899",
  "#22c55e",
];
const SERIES_COLORS = {
  newUsers: "#111827",
  messages: "#0ea5e9",
  promptTokens: "#f59e0b",
  completionTokens: "#10b981",
  userMessages: "#6366f1",
  assistantMessages: "#334155",
};

/** 数字格式化：百万级 → 1.23M，千级 → 12.3K，小数字用千分位 */
function fmtNum(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString("en-US");
}

function toDatetimeLocalValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

function fromDatetimeLocalValue(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString();
}

type SectionKey =
  | "dashboard"
  | "users"
  | "conversations"
  | "skills"
  | "tools"
  | "delivery"
  | "miniappPopup"
  | "audit";

const SECTION_ITEMS: Array<{ key: SectionKey; label: string }> = [
  { key: "dashboard", label: "运营看板" },
  { key: "users", label: "用户管理" },
  { key: "conversations", label: "会话回放" },
  { key: "skills", label: "技能管理" },
  { key: "tools", label: "工具管理" },
  { key: "delivery", label: "提醒投递" },
  { key: "miniappPopup", label: "首页弹窗" },
  { key: "audit", label: "审计日志" },
];

const USER_PLATFORM_OPTIONS = ["web", "telegram", "feishu", "wechat", "qq", "miniapp"];

function skillStatusLabel(status: string) {
  const key = String(status || "").toUpperCase();
  if (key === "DRAFT") return "草稿";
  if (key === "PUBLISHED") return "已发布";
  if (key === "DISABLED") return "已停用";
  return status || "未知";
}

function useAdminToken() {
  const [token, setToken] = useState<string>(() => localStorage.getItem(ADMIN_TOKEN_KEY) || "");
  const save = (value: string) => {
    const next = (value || "").trim();
    setToken(next);
    if (next) localStorage.setItem(ADMIN_TOKEN_KEY, next);
    else localStorage.removeItem(ADMIN_TOKEN_KEY);
  };
  return { token, setToken: save };
}

function Pager({
  page,
  size,
  total,
  onPageChange,
}: {
  page: number;
  size: number;
  total: number;
  onPageChange: (next: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="flex items-center justify-between text-xs text-content-secondary">
      <span>
        第 {page}/{totalPages} 页，共 {total} 条
      </span>
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          上一页
        </Button>
        <Button size="sm" variant="ghost" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
          下一页
        </Button>
      </div>
    </div>
  );
}

export function AdminPage() {
  const { token, setToken } = useAdminToken();
  const queryClient = useQueryClient();

  const [draftToken, setDraftToken] = useState(token);
  const [activeSection, setActiveSection] = useState<SectionKey>("dashboard");
  const [days, setDays] = useState(30);

  const [userKeyword, setUserKeyword] = useState("");
  const [userPlatform, setUserPlatform] = useState("");
  const [userBlocked, setUserBlocked] = useState("all");
  const [userPage, setUserPage] = useState(1);
  const userSize = 20;

  const [conversationKeyword, setConversationKeyword] = useState("");
  const [conversationUserId, setConversationUserId] = useState("");
  const [conversationPage, setConversationPage] = useState(1);
  const conversationSize = 20;
  const [selectedConversationId, setSelectedConversationId] = useState<number | null>(null);

  const [skillKeyword, setSkillKeyword] = useState("");
  const [skillStatus, setSkillStatus] = useState("");
  const [skillPage, setSkillPage] = useState(1);
  const skillSize = 20;

  const [auditKeyword, setAuditKeyword] = useState("");
  const [auditAction, setAuditAction] = useState("");
  const [auditUserId, setAuditUserId] = useState("");
  const [auditPage, setAuditPage] = useState(1);
  const auditSize = 30;
  const [popupDraft, setPopupDraft] = useState<AdminMiniappHomePopupConfig | null>(null);
  const [popupSaveMessage, setPopupSaveMessage] = useState("");

  const dashboard = useQuery({
    queryKey: ["admin", "dashboard", token, days],
    queryFn: () => fetchAdminDashboard(token, days),
    enabled: !!token,
    refetchInterval: token ? 30000 : false,
  });

  const conversationStats = useQuery({
    queryKey: ["admin", "conversation-stats", token, days],
    queryFn: () => fetchAdminConversationStats(token, days),
    enabled: !!token,
    refetchInterval: token ? 30000 : false,
  });

  const users = useQuery({
    queryKey: ["admin", "users", token, userPage, userSize, userKeyword, userPlatform, userBlocked],
    queryFn: () =>
      fetchAdminUsers(token, {
        page: userPage,
        size: userSize,
        q: userKeyword || undefined,
        platform: userPlatform || undefined,
        blocked: userBlocked === "all" ? undefined : userBlocked === "true",
      }),
    enabled: !!token,
  });

  const conversations = useQuery({
    queryKey: [
      "admin",
      "conversations",
      token,
      conversationPage,
      conversationSize,
      conversationKeyword,
      conversationUserId,
    ],
    queryFn: () =>
      fetchAdminConversations(token, {
        page: conversationPage,
        size: conversationSize,
        q: conversationKeyword || undefined,
        user_id: conversationUserId ? Number(conversationUserId) : undefined,
      }),
    enabled: !!token,
  });

  const conversationMessages = useQuery({
    queryKey: ["admin", "conversation-messages", token, selectedConversationId],
    queryFn: () =>
      fetchAdminConversationMessages(token, selectedConversationId as number, {
        page: 1,
        size: 300,
      }),
    enabled: !!token && !!selectedConversationId,
  });

  const tools = useQuery({
    queryKey: ["admin", "tools", token, days],
    queryFn: () => fetchAdminTools(token, days),
    enabled: !!token,
  });

  const skills = useQuery({
    queryKey: ["admin", "skills", token, skillPage, skillSize, skillKeyword, skillStatus],
    queryFn: () =>
      fetchAdminSkills(token, {
        page: skillPage,
        size: skillSize,
        q: skillKeyword || undefined,
        status: skillStatus || undefined,
      }),
    enabled: !!token,
  });

  const delivery = useQuery({
    queryKey: ["admin", "delivery", token, days],
    queryFn: () => fetchAdminScheduleDelivery(token, days),
    enabled: !!token,
  });

  const audit = useQuery({
    queryKey: ["admin", "audit", token, auditPage, auditSize, auditKeyword, auditAction, auditUserId],
    queryFn: () =>
      fetchAdminAudit(token, {
        page: auditPage,
        size: auditSize,
        q: auditKeyword || undefined,
        action: auditAction || undefined,
        user_id: auditUserId ? Number(auditUserId) : undefined,
      }),
    enabled: !!token,
  });

  const miniappPopup = useQuery({
    queryKey: ["admin", "miniapp-home-popup", token],
    queryFn: () => fetchAdminMiniappHomePopup(token),
    enabled: !!token,
  });

  useEffect(() => {
    if (activeSection !== "conversations") return;
    if (selectedConversationId) return;
    const first = conversations.data?.items?.[0]?.id;
    if (first) setSelectedConversationId(first);
  }, [activeSection, conversations.data, selectedConversationId]);

  useEffect(() => {
    if (!miniappPopup.data) return;
    setPopupDraft(miniappPopup.data);
    setPopupSaveMessage("");
  }, [miniappPopup.data]);

  const blockMutation = useMutation({
    mutationFn: (payload: { userId: number; blocked: boolean; reason?: string }) =>
      adminSetUserBlock(token, payload.userId, {
        is_blocked: payload.blocked,
        reason: payload.reason || "",
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", token] });
    },
  });

  const quotaMutation = useMutation({
    mutationFn: (payload: { userId: number; dailyLimit?: number; monthlyLimit?: number }) =>
      adminSetUserQuota(token, payload.userId, {
        ...(payload.dailyLimit !== undefined
          ? { daily_message_limit: payload.dailyLimit }
          : {}),
        ...(payload.monthlyLimit !== undefined
          ? { monthly_message_limit: payload.monthlyLimit }
          : {}),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", token] });
    },
  });

  const toolMutation = useMutation({
    mutationFn: (payload: { tool: AdminToolItem; enabled: boolean }) =>
      adminSetToolSwitch(token, payload.tool.source, payload.tool.name, payload.enabled),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["admin", "tools", token] });
    },
  });

  const disableSkillMutation = useMutation({
    mutationFn: (skillId: number) => adminDisableSkill(token, skillId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["admin", "skills", token] });
    },
  });

  const savePopupMutation = useMutation({
    mutationFn: (payload: AdminMiniappHomePopupConfig) => saveAdminMiniappHomePopup(token, payload),
    onSuccess: async (res) => {
      setPopupDraft(res.config);
      setPopupSaveMessage(`保存成功：${new Date(res.updated_at).toLocaleString()}`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "miniapp-home-popup", token] });
    },
    onError: (error) => {
      if (error instanceof Error) setPopupSaveMessage(`保存失败：${error.message}`);
      else setPopupSaveMessage("保存失败");
    },
  });

  const tokenError = useMemo(
    () =>
      dashboard.error instanceof Error
        ? dashboard.error.message
        : users.error instanceof Error
          ? users.error.message
          : "",
    [dashboard.error, users.error]
  );

  if (!token) {
    return (
      <div className="min-h-screen bg-surface p-6 flex items-center justify-center">
        <Card className="w-full max-w-xl">
          <CardHeader className="text-2xl font-semibold">PAI Admin</CardHeader>
          <CardContent className="space-y-4">
            <p className="text-content-secondary">请输入 `X-Admin-Token` 进入管理后台。</p>
            <Input
              value={draftToken}
              onChange={(e) => setDraftToken(e.target.value)}
              placeholder="粘贴 ADMIN_TOKEN"
            />
            <div className="flex gap-3">
              <Button onClick={() => setToken(draftToken)} disabled={!draftToken.trim()}>
                进入后台
              </Button>
              <Button variant="ghost" onClick={() => (window.location.href = "/")}>
                返回聊天
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const cards = dashboard.data?.cards;

  return (
    <div className="min-h-screen bg-surface p-5 space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">PAI Admin V1</h1>
          <select
            className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm"
            value={String(days)}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            <option value="7">近7天</option>
            <option value="30">近30天</option>
            <option value="60">近60天</option>
          </select>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => (window.location.href = "/")}>
            返回聊天
          </Button>
          <Button variant="danger" onClick={() => setToken("")}>
            退出后台
          </Button>
        </div>
      </div>

      {tokenError ? (
        <Card>
          <CardContent className="text-danger py-4">{tokenError}</CardContent>
        </Card>
      ) : null}

      <div className="grid lg:grid-cols-[220px_minmax(0,1fr)] gap-4">
        <Card className="h-fit lg:sticky lg:top-4">
          <CardHeader className="text-lg font-semibold">功能区</CardHeader>
          <CardContent className="space-y-2">
            {SECTION_ITEMS.map((item) => (
              <Button
                key={item.key}
                variant={activeSection === item.key ? "default" : "ghost"}
                className="w-full justify-start"
                onClick={() => setActiveSection(item.key)}
              >
                {item.label}
              </Button>
            ))}
          </CardContent>
        </Card>

        <div className="space-y-4">
          {activeSection === "dashboard" ? (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Card><CardContent className="py-4"><div className="text-xs text-content-secondary">用户总数</div><div className="text-2xl font-semibold">{fmtNum(cards?.total_users)}</div></CardContent></Card>
                <Card><CardContent className="py-4"><div className="text-xs text-content-secondary">今日新增</div><div className="text-2xl font-semibold">{fmtNum(cards?.new_users_today)}</div></CardContent></Card>
                <Card><CardContent className="py-4"><div className="text-xs text-content-secondary">DAU</div><div className="text-2xl font-semibold">{fmtNum(cards?.dau_today)}</div></CardContent></Card>
                <Card><CardContent className="py-4"><div className="text-xs text-content-secondary">消息量（近{days}天 / 总）</div><div className="text-2xl font-semibold">{fmtNum(cards?.window_messages)}</div><div className="text-xs text-content-secondary mt-1">总 {fmtNum(cards?.total_messages)}</div></CardContent></Card>
                <Card><CardContent className="py-4"><div className="text-xs text-content-secondary">输入 Token</div><div className="text-2xl font-semibold">{fmtNum(cards?.total_prompt_tokens)}</div></CardContent></Card>
                <Card><CardContent className="py-4"><div className="text-xs text-content-secondary">输出 Token</div><div className="text-2xl font-semibold">{fmtNum(cards?.total_completion_tokens)}</div></CardContent></Card>
                <Card><CardContent className="py-4"><div className="text-xs text-content-secondary">Token 总量</div><div className="text-2xl font-semibold">{fmtNum(cards?.total_tokens)}</div></CardContent></Card>
                <Card><CardContent className="py-4"><div className="text-xs text-content-secondary">可计量调用</div><div className="text-2xl font-semibold">{cards ? `${fmtNum(cards.metered_calls)}/${fmtNum(cards.llm_calls)}` : "-"}</div><div className="text-xs text-content-secondary mt-1">未计量 {fmtNum(cards?.unmetered_calls)}</div></CardContent></Card>
              </div>

              <div className="grid xl:grid-cols-2 gap-4">
                <Card>
                  <CardHeader className="text-lg font-semibold">趋势图（用户/消息/输入输出Token）</CardHeader>
                  <CardContent className="h-[320px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={dashboard.data?.trend || []}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} tickFormatter={(v: number) => fmtNum(v)} />
                        <Tooltip formatter={(v: number) => fmtNum(v)} />
                        <Legend />
                        <Line yAxisId="left" type="monotone" dataKey="new_users" stroke={SERIES_COLORS.newUsers} name="新增用户" strokeWidth={2} dot={false} />
                        <Line yAxisId="left" type="monotone" dataKey="messages" stroke={SERIES_COLORS.messages} name="消息量" strokeWidth={2} dot={false} />
                        <Line yAxisId="right" type="monotone" dataKey="prompt_tokens" stroke={SERIES_COLORS.promptTokens} name="输入Token" strokeWidth={2} dot={false} />
                        <Line yAxisId="right" type="monotone" dataKey="completion_tokens" stroke={SERIES_COLORS.completionTokens} name="输出Token" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="text-lg font-semibold">会话消息趋势（user/assistant）</CardHeader>
                  <CardContent className="h-[320px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={conversationStats.data?.by_day || []}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="date" />
                        <YAxis />
                        <Tooltip />
                        <Legend />
                        <Bar dataKey="user" stackId="a" fill={SERIES_COLORS.userMessages} name="用户消息" />
                        <Bar dataKey="assistant" stackId="a" fill={SERIES_COLORS.assistantMessages} name="助手消息" />
                      </BarChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>
              </div>

              <div className="grid xl:grid-cols-2 gap-4">
                <Card>
                  <CardHeader className="text-lg font-semibold">意图分布</CardHeader>
                  <CardContent className="space-y-3">
                    <div className="h-[240px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie data={dashboard.data?.intent_distribution || []} dataKey="count" nameKey="name" outerRadius={90} labelLine={false} label={(entry: { count: number }) => fmtNum(entry.count)}>
                            {(dashboard.data?.intent_distribution || []).map((_, i) => (
                              <Cell key={`intent-${i}`} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                            ))}
                          </Pie>
                          <Tooltip formatter={(value: number) => fmtNum(value)} />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
                      {(dashboard.data?.intent_distribution || []).map((item, i) => (
                        <div key={`intent-legend-${item.name}-${i}`} className="flex items-center justify-between rounded-lg border border-border px-2 py-1.5">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }} />
                            <span className="truncate">{item.name}</span>
                          </div>
                          <span className="text-content-secondary">{fmtNum(item.count)}</span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="text-lg font-semibold">平台分布</CardHeader>
                  <CardContent className="space-y-3">
                    <div className="h-[240px]">
                      <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie data={dashboard.data?.platform_distribution || []} dataKey="count" nameKey="platform" outerRadius={90} labelLine={false} label={(entry: { count: number }) => fmtNum(entry.count)}>
                          {(dashboard.data?.platform_distribution || []).map((_, i) => (
                            <Cell key={`platform-${i}`} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(value: number) => fmtNum(value)} />
                      </PieChart>
                    </ResponsiveContainer>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
                      {(dashboard.data?.platform_distribution || []).map((item, i) => (
                        <div key={`platform-legend-${item.platform}-${i}`} className="flex items-center justify-between rounded-lg border border-border px-2 py-1.5">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }} />
                            <span className="truncate">{item.platform}</span>
                          </div>
                          <span className="text-content-secondary">{fmtNum(item.count)}</span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </div>
            </>
          ) : null}

          {activeSection === "users" ? (
            <Card>
              <CardHeader className="text-lg font-semibold">用户管理</CardHeader>
              <CardContent className="space-y-3">
                <div className="grid md:grid-cols-4 gap-2">
                  <Input
                    placeholder="搜索昵称/邮箱/平台ID"
                    value={userKeyword}
                    onChange={(e) => {
                      setUserKeyword(e.target.value);
                      setUserPage(1);
                    }}
                  />
                  <select
                    className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm"
                    value={userPlatform}
                    onChange={(e) => {
                      setUserPlatform(e.target.value);
                      setUserPage(1);
                    }}
                  >
                    <option value="">全部平台</option>
                    {USER_PLATFORM_OPTIONS.map((platform) => (
                      <option key={platform} value={platform}>
                        {platform}
                      </option>
                    ))}
                  </select>
                  <select
                    className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm"
                    value={userBlocked}
                    onChange={(e) => {
                      setUserBlocked(e.target.value);
                      setUserPage(1);
                    }}
                  >
                    <option value="all">全部状态</option>
                    <option value="false">未封禁</option>
                    <option value="true">已封禁</option>
                  </select>
                </div>
                <div className="overflow-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-content-secondary border-b border-border">
                        <th className="py-2 pr-2">ID</th>
                        <th className="py-2 pr-2">昵称</th>
                        <th className="py-2 pr-2">平台</th>
                        <th className="py-2 pr-2">消息</th>
                        <th className="py-2 pr-2">账单</th>
                        <th className="py-2 pr-2">技能</th>
                        <th className="py-2 pr-2">日/月配额</th>
                        <th className="py-2 pr-2">状态</th>
                        <th className="py-2 pr-2">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(users.data?.items || []).map((row: AdminUserItem) => (
                        <tr key={row.id} className="border-b border-border/60">
                          <td className="py-2 pr-2">#{row.id}</td>
                          <td className="py-2 pr-2">{row.nickname}</td>
                          <td className="py-2 pr-2">{row.platform}</td>
                          <td className="py-2 pr-2">{row.message_count}</td>
                          <td className="py-2 pr-2">{row.ledger_count}</td>
                          <td className="py-2 pr-2">{row.skill_count}</td>
                          <td className="py-2 pr-2">
                            <div className="flex items-center gap-2">
                              <Input
                                className="h-8 w-20"
                                defaultValue={String(row.daily_message_limit || 0)}
                                title="每日配额"
                                onBlur={(e) => {
                                  const value = Number(e.target.value || 0);
                                  if (!Number.isFinite(value)) return;
                                  quotaMutation.mutate({
                                    userId: row.id,
                                    dailyLimit: Math.max(0, Math.floor(value)),
                                  });
                                }}
                              />
                              <span className="text-content-secondary text-xs">/</span>
                              <Input
                                className="h-8 w-20"
                                defaultValue={String(row.monthly_message_limit || 0)}
                                title="每月配额（0=不限）"
                                onBlur={(e) => {
                                  const value = Number(e.target.value || 0);
                                  if (!Number.isFinite(value)) return;
                                  quotaMutation.mutate({
                                    userId: row.id,
                                    monthlyLimit: Math.max(0, Math.floor(value)),
                                  });
                                }}
                              />
                            </div>
                          </td>
                          <td className="py-2 pr-2">{row.is_blocked ? "已封禁" : "正常"}</td>
                          <td className="py-2 pr-2">
                            <Button
                              size="sm"
                              variant={row.is_blocked ? "default" : "danger"}
                              onClick={() =>
                                blockMutation.mutate({
                                  userId: row.id,
                                  blocked: !row.is_blocked,
                                  reason: !row.is_blocked ? "admin block" : "",
                                })
                              }
                            >
                              {row.is_blocked ? "解封" : "封禁"}
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pager
                  page={users.data?.page || 1}
                  size={users.data?.size || userSize}
                  total={users.data?.total || 0}
                  onPageChange={setUserPage}
                />
              </CardContent>
            </Card>
          ) : null}

          {activeSection === "conversations" ? (
            <div className="grid xl:grid-cols-2 gap-4">
              <Card>
                <CardHeader className="text-lg font-semibold">会话列表</CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid md:grid-cols-2 gap-2">
                    <Input
                      placeholder="会话标题搜索"
                      value={conversationKeyword}
                      onChange={(e) => {
                        setConversationKeyword(e.target.value);
                        setConversationPage(1);
                      }}
                    />
                    <Input
                      placeholder="用户ID筛选"
                      value={conversationUserId}
                      onChange={(e) => {
                        setConversationUserId(e.target.value);
                        setConversationPage(1);
                      }}
                    />
                  </div>
                  <div className="overflow-auto max-h-[420px]">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-content-secondary border-b border-border">
                          <th className="py-2 pr-2">会话</th>
                          <th className="py-2 pr-2">用户</th>
                          <th className="py-2 pr-2">消息数</th>
                          <th className="py-2 pr-2">更新时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(conversations.data?.items || []).map((row: AdminConversationItem) => (
                          <tr
                            key={row.id}
                            className={`border-b border-border/60 cursor-pointer ${selectedConversationId === row.id ? "bg-surface-hover" : ""}`}
                            onClick={() => setSelectedConversationId(row.id)}
                          >
                            <td className="py-2 pr-2 max-w-[220px] truncate">#{row.id} {row.title}</td>
                            <td className="py-2 pr-2">{row.user_nickname || row.user_id}</td>
                            <td className="py-2 pr-2">{row.message_count}</td>
                            <td className="py-2 pr-2">{new Date(row.last_message_at).toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Pager
                    page={conversations.data?.page || 1}
                    size={conversations.data?.size || conversationSize}
                    total={conversations.data?.total || 0}
                    onPageChange={setConversationPage}
                  />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="text-lg font-semibold">会话回放</CardHeader>
                <CardContent className="space-y-2 max-h-[560px] overflow-auto">
                  {!selectedConversationId ? (
                    <div className="text-content-secondary text-sm">从左侧选择一个会话查看完整消息流。</div>
                  ) : (
                    (conversationMessages.data?.items || []).map((msg: AdminConversationMessageItem) => (
                      <div
                        key={msg.id}
                        className={`rounded-xl border px-3 py-2 text-sm ${msg.role === "user" ? "border-border bg-surface" : "border-border bg-surface-card"}`}
                      >
                        <div className="text-xs text-content-secondary mb-1">
                          {msg.role} · {msg.platform} · {new Date(msg.created_at).toLocaleString()}
                        </div>
                        <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>
            </div>
          ) : null}

          {activeSection === "skills" ? (
            <Card>
              <CardHeader className="text-lg font-semibold">技能管理</CardHeader>
              <CardContent className="space-y-3">
                <div className="grid md:grid-cols-3 gap-2">
                  <Input
                    placeholder="技能名/slug搜索"
                    value={skillKeyword}
                    onChange={(e) => {
                      setSkillKeyword(e.target.value);
                      setSkillPage(1);
                    }}
                  />
                  <select
                    className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm"
                    value={skillStatus}
                    onChange={(e) => {
                      setSkillStatus(e.target.value);
                      setSkillPage(1);
                    }}
                  >
                    <option value="">全部状态</option>
                    <option value="DRAFT">草稿</option>
                    <option value="PUBLISHED">已发布</option>
                    <option value="DISABLED">已停用</option>
                  </select>
                </div>
                <div className="overflow-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-content-secondary border-b border-border">
                        <th className="py-2 pr-2">ID</th>
                        <th className="py-2 pr-2">技能</th>
                        <th className="py-2 pr-2">用户</th>
                        <th className="py-2 pr-2">状态</th>
                        <th className="py-2 pr-2">版本</th>
                        <th className="py-2 pr-2">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(skills.data?.items || []).map((skill: AdminSkillsItem) => (
                        <tr key={skill.id} className="border-b border-border/60">
                          <td className="py-2 pr-2">#{skill.id}</td>
                          <td className="py-2 pr-2">{skill.name}</td>
                          <td className="py-2 pr-2">{skill.user_nickname || skill.user_id}</td>
                          <td className="py-2 pr-2">{skillStatusLabel(skill.status)}</td>
                          <td className="py-2 pr-2">v{skill.active_version}</td>
                          <td className="py-2 pr-2">
                            <Button
                              size="sm"
                              variant="danger"
                              disabled={skill.status === "DISABLED"}
                              onClick={() => disableSkillMutation.mutate(skill.id)}
                            >
                              强制停用
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Pager
                  page={skills.data?.page || 1}
                  size={skills.data?.size || skillSize}
                  total={skills.data?.total || 0}
                  onPageChange={setSkillPage}
                />
              </CardContent>
            </Card>
          ) : null}

          {activeSection === "tools" ? (
            <Card>
              <CardHeader className="text-lg font-semibold">工具管理</CardHeader>
              <CardContent className="overflow-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-content-secondary border-b border-border">
                      <th className="py-2 pr-2">来源</th>
                      <th className="py-2 pr-2">名称</th>
                      <th className="py-2 pr-2">调用</th>
                      <th className="py-2 pr-2">成功率</th>
                      <th className="py-2 pr-2">平均耗时</th>
                      <th className="py-2 pr-2">开关</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(tools.data?.items || []).map((tool: AdminToolItem) => (
                      <tr key={`${tool.source}:${tool.name}`} className="border-b border-border/60">
                        <td className="py-2 pr-2">{tool.source}</td>
                        <td className="py-2 pr-2">{tool.name}</td>
                        <td className="py-2 pr-2">{tool.calls}</td>
                        <td className="py-2 pr-2">{(tool.success_rate * 100).toFixed(1)}%</td>
                        <td className="py-2 pr-2">{tool.avg_latency_ms}ms</td>
                        <td className="py-2 pr-2">
                          <Button
                            size="sm"
                            variant={tool.enabled ? "danger" : "default"}
                            onClick={() => toolMutation.mutate({ tool, enabled: !tool.enabled })}
                          >
                            {tool.enabled ? "停用" : "启用"}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          ) : null}

          {activeSection === "delivery" ? (
            <Card>
              <CardHeader className="text-lg font-semibold">提醒投递统计</CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2 text-sm">
                  {(delivery.data?.items || []).map((row) => (
                    <div key={row.platform} className="rounded-xl border border-border px-3 py-2 flex justify-between">
                      <span>{row.platform}</span>
                      <span>
                        总{row.total} / 成功{row.delivered} / 失败{row.failed} / 成功率{(row.success_rate * 100).toFixed(1)}%
                      </span>
                    </div>
                  ))}
                </div>
                <div className="h-[300px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={delivery.data?.items || []}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="platform" />
                      <YAxis />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="delivered" fill="#0b1220" name="成功" />
                      <Bar dataKey="failed" fill="#8bb0e8" name="失败" />
                      <Bar dataKey="pending" fill="#c3d9ff" name="待处理" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {activeSection === "miniappPopup" ? (
            <Card>
              <CardHeader className="text-lg font-semibold">首页弹窗配置</CardHeader>
              <CardContent className="space-y-4">
                {miniappPopup.error instanceof Error ? (
                  <div className="rounded-xl border border-danger/40 bg-danger/5 px-3 py-2 text-sm text-danger">
                    读取失败：{miniappPopup.error.message}
                  </div>
                ) : null}

                {popupSaveMessage ? (
                  <div className="rounded-xl border border-border px-3 py-2 text-sm">{popupSaveMessage}</div>
                ) : null}

                {!popupDraft ? (
                  <div className="text-sm text-content-secondary">
                    {miniappPopup.isLoading ? "加载配置中..." : "暂无配置"}
                  </div>
                ) : (
                  <>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={popupDraft.enabled}
                        onChange={(e) =>
                          setPopupDraft((prev) => (prev ? { ...prev, enabled: e.target.checked } : prev))
                        }
                      />
                      启用首页弹窗
                    </label>

                    <div className="grid md:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <div className="text-xs text-content-secondary">标题</div>
                        <Input
                          value={popupDraft.title}
                          onChange={(e) =>
                            setPopupDraft((prev) => (prev ? { ...prev, title: e.target.value } : prev))
                          }
                          placeholder="例如：版本更新说明"
                        />
                      </div>
                      <div className="space-y-1">
                        <div className="text-xs text-content-secondary">按钮文案</div>
                        <Input
                          value={popupDraft.primary_button_text}
                          onChange={(e) =>
                            setPopupDraft((prev) =>
                              prev ? { ...prev, primary_button_text: e.target.value } : prev
                            )
                          }
                          placeholder="我知道了"
                        />
                      </div>
                    </div>

                    <div className="space-y-1">
                      <div className="text-xs text-content-secondary">内容</div>
                      <textarea
                        className="w-full min-h-[120px] rounded-xl border border-border bg-surface-card px-3 py-2 text-sm"
                        value={popupDraft.content}
                        onChange={(e) =>
                          setPopupDraft((prev) => (prev ? { ...prev, content: e.target.value } : prev))
                        }
                        placeholder="支持多行文案"
                      />
                    </div>

                    <div className="grid md:grid-cols-3 gap-3">
                      <div className="space-y-1">
                        <div className="text-xs text-content-secondary">展示策略</div>
                        <select
                          className="h-10 w-full rounded-xl border border-border bg-surface-card px-3 text-sm"
                          value={popupDraft.show_mode}
                          onChange={(e) =>
                            setPopupDraft((prev) =>
                              prev
                                ? {
                                    ...prev,
                                    show_mode: e.target.value as AdminMiniappHomePopupConfig["show_mode"],
                                  }
                                : prev
                            )
                          }
                        >
                          <option value="always">每次都弹</option>
                          <option value="once_per_day">每天一次</option>
                          <option value="once_per_version">每版本一次</option>
                        </select>
                      </div>
                      <div className="space-y-1">
                        <div className="text-xs text-content-secondary">版本号</div>
                        <Input
                          type="number"
                          min={1}
                          value={String(popupDraft.version)}
                          onChange={(e) =>
                            setPopupDraft((prev) =>
                              prev ? { ...prev, version: Math.max(1, Number(e.target.value || 1)) } : prev
                            )
                          }
                        />
                      </div>
                      <div className="space-y-1">
                        <div className="text-xs text-content-secondary">状态</div>
                        <div className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm flex items-center">
                          {popupDraft.enabled ? "已启用" : "已停用"}
                        </div>
                      </div>
                    </div>

                    <div className="grid md:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <div className="text-xs text-content-secondary">开始时间（可选）</div>
                        <Input
                          type="datetime-local"
                          value={toDatetimeLocalValue(popupDraft.start_at)}
                          onChange={(e) =>
                            setPopupDraft((prev) =>
                              prev ? { ...prev, start_at: fromDatetimeLocalValue(e.target.value) } : prev
                            )
                          }
                        />
                      </div>
                      <div className="space-y-1">
                        <div className="text-xs text-content-secondary">结束时间（可选）</div>
                        <Input
                          type="datetime-local"
                          value={toDatetimeLocalValue(popupDraft.end_at)}
                          onChange={(e) =>
                            setPopupDraft((prev) =>
                              prev ? { ...prev, end_at: fromDatetimeLocalValue(e.target.value) } : prev
                            )
                          }
                        />
                      </div>
                    </div>

                    <div className="flex gap-2">
                      <Button
                        onClick={() => popupDraft && savePopupMutation.mutate(popupDraft)}
                        disabled={!popupDraft || savePopupMutation.isPending}
                      >
                        {savePopupMutation.isPending ? "保存中..." : "保存配置"}
                      </Button>
                      <Button variant="ghost" onClick={() => miniappPopup.refetch()} disabled={miniappPopup.isLoading}>
                        刷新
                      </Button>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          ) : null}

          {activeSection === "audit" ? (
            <Card>
              <CardHeader className="text-lg font-semibold">审计日志</CardHeader>
              <CardContent className="space-y-3">
                <div className="grid md:grid-cols-3 gap-2">
                  <Input
                    placeholder="action 筛选"
                    value={auditAction}
                    onChange={(e) => {
                      setAuditAction(e.target.value);
                      setAuditPage(1);
                    }}
                  />
                  <Input
                    placeholder="user_id 筛选"
                    value={auditUserId}
                    onChange={(e) => {
                      setAuditUserId(e.target.value);
                      setAuditPage(1);
                    }}
                  />
                  <Input
                    placeholder="detail 搜索"
                    value={auditKeyword}
                    onChange={(e) => {
                      setAuditKeyword(e.target.value);
                      setAuditPage(1);
                    }}
                  />
                </div>
                <div className="space-y-2 max-h-[520px] overflow-auto text-xs">
                  {(audit.data?.items || []).map((row: AdminAuditItem) => (
                    <div key={row.id} className="rounded-xl border border-border px-3 py-2">
                      <div className="font-medium">{row.action}</div>
                      <div className="text-content-secondary">
                        user={row.user_id || "-"} platform={row.platform} {new Date(row.created_at).toLocaleString()}
                      </div>
                      <pre className="mt-2 whitespace-pre-wrap break-all text-[11px] text-content-secondary bg-surface rounded-lg p-2">
                        {typeof row.detail === "string" ? row.detail : JSON.stringify(row.detail, null, 2)}
                      </pre>
                    </div>
                  ))}
                </div>
                <Pager
                  page={audit.data?.page || 1}
                  size={audit.data?.size || auditSize}
                  total={audit.data?.total || 0}
                  onPageChange={setAuditPage}
                />
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
