import { useCallback, useEffect, useMemo, useState } from "react";
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
  adminConsolidateAllMemories,
  adminConsolidateUserMemories,
  adminDeleteAllMemories,
  adminDeleteUserMemories,
  adminDisableSkill,
  adminSetToolSwitch,
  adminSetUserBlock,
  adminSetUserQuota,
  fetchAdminAudit,
  fetchAdminConversationMessages,
  fetchAdminConversations,
  fetchAdminConversationStats,
  fetchAdminDashboard,
  fetchAdminFeedbacks,
  fetchAdminMiniappHomePopup,
  fetchAdminUserDetail,
  fetchAdminScheduleDelivery,
  fetchAdminSkills,
  fetchAdminTools,
  fetchAdminUsers,
  saveAdminMiniappHomePopup,
  type AdminAuditItem,
  type AdminFeedbackItem,
  type AdminMemoryConsolidateAllResponse,
  type AdminMemoryPurgeAllResponse,
  type AdminConversationItem,
  type AdminConversationMessageItem,
  type AdminMiniappHomePopupConfig,
  type AdminSkillsItem,
  type AdminToolItem,
  type AdminUserDetail,
  type AdminUserItem,
} from "../../lib/api";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardHeader } from "../../components/ui/card";
import { Input } from "../../components/ui/input";
import { ConfirmDialog } from "../../components/ui/confirm-dialog";
import { SectionLoading, SectionError, EmptyState, Spinner } from "../../components/ui/spinner";
import { getAdminShowExecutionPanel, setAdminShowExecutionPanel } from "../../lib/adminPrefs";
import { formatYmdHmLocal, parseServerDate } from "../../lib/datetime";

/* ─── Constants ─── */

const ADMIN_TOKEN_KEY = "pai_admin_token";
const PIE_COLORS = [
  "#2563eb", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#14b8a6", "#f97316", "#64748b", "#ec4899", "#22c55e",
];
const SERIES_COLORS = {
  newUsers: "#111827",
  messages: "#0ea5e9",
  promptTokens: "#f59e0b",
  completionTokens: "#10b981",
  userMessages: "#6366f1",
  assistantMessages: "#334155",
};

/* ─── Helpers ─── */

function fmtNum(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString("en-US");
}

function fmtDateTime(value: string | null | undefined): string {
  if (!value) return "-";
  const date = parseServerDate(value);
  if (Number.isNaN(date.getTime())) return "-";
  return formatYmdHmLocal(value);
}

function toDatetimeLocalValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = parseServerDate(value);
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

/* ─── Section Config ─── */

type SectionKey =
  | "dashboard"
  | "users"
  | "conversations"
  | "skills"
  | "tools"
  | "delivery"
  | "miniappPopup"
  | "feedback"
  | "audit";

const SECTION_ITEMS: Array<{ key: SectionKey; label: string; icon: string }> = [
  { key: "dashboard", label: "运营看板", icon: "📊" },
  { key: "users", label: "用户管理", icon: "👥" },
  { key: "conversations", label: "会话回放", icon: "💬" },
  { key: "skills", label: "技能管理", icon: "⚡" },
  { key: "tools", label: "工具管理", icon: "🔧" },
  { key: "delivery", label: "提醒投递", icon: "📨" },
  { key: "miniappPopup", label: "首页弹窗", icon: "🪟" },
  { key: "feedback", label: "问题反馈", icon: "📝" },
  { key: "audit", label: "审计日志", icon: "📋" },
];

const USER_PLATFORM_OPTIONS = ["web", "telegram", "feishu", "wechat", "qq", "miniapp"];

function skillStatusLabel(status: string) {
  const key = String(status || "").toUpperCase();
  if (key === "DRAFT") return "草稿";
  if (key === "PUBLISHED") return "已发布";
  if (key === "DISABLED") return "已停用";
  return status || "未知";
}

function skillStatusBadge(status: string) {
  const key = String(status || "").toUpperCase();
  if (key === "PUBLISHED") return "bg-green-100 text-green-700";
  if (key === "DISABLED") return "bg-red-100 text-red-600";
  return "bg-gray-100 text-gray-600";
}

/* ─── Hooks ─── */

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

interface ConfirmState {
  open: boolean;
  title: string;
  description: string;
  variant: "danger" | "default";
  onConfirm: () => void;
}

function useConfirm() {
  const [state, setState] = useState<ConfirmState>({
    open: false,
    title: "",
    description: "",
    variant: "default",
    onConfirm: () => {},
  });

  const confirm = useCallback(
    (opts: { title: string; description: string; variant?: "danger" | "default"; onConfirm: () => void }) => {
      setState({
        open: true,
        title: opts.title,
        description: opts.description,
        variant: opts.variant || "default",
        onConfirm: opts.onConfirm,
      });
    },
    []
  );

  const close = useCallback(() => setState((s) => ({ ...s, open: false })), []);

  return { state, confirm, close };
}

/* ─── Status Toast ─── */

function StatusToast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 8000);
    return () => clearTimeout(timer);
  }, [message, onClose]);

  return (
    <div className="fixed bottom-6 right-6 z-50 max-w-sm animate-in slide-in-from-bottom-4 fade-in duration-300">
      <div className="rounded-xl border border-border bg-surface-card shadow-lg px-4 py-3 flex items-start gap-3">
        <span className="text-sm flex-1">{message}</span>
        <button className="text-content-secondary hover:text-content text-lg leading-none" onClick={onClose}>
          &times;
        </button>
      </div>
    </div>
  );
}

/* ─── Pager ─── */

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
    <div className="flex items-center justify-between text-xs text-content-secondary pt-2">
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

/* ─── Stat Card ─── */

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <CardContent className="py-4">
        <div className="text-xs text-content-secondary">{label}</div>
        <div className="text-2xl font-semibold mt-1">{value}</div>
        {sub ? <div className="text-xs text-content-secondary mt-1">{sub}</div> : null}
      </CardContent>
    </Card>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   Main Component
   ═══════════════════════════════════════════════════════════════════ */

export function AdminPage() {
  const { token, setToken } = useAdminToken();
  const queryClient = useQueryClient();
  const { state: confirmState, confirm, close: closeConfirm } = useConfirm();

  const [draftToken, setDraftToken] = useState(token);
  const [activeSection, setActiveSection] = useState<SectionKey>("dashboard");
  const [days, setDays] = useState(30);
  const [statusMessage, setStatusMessage] = useState("");

  // -- Users --
  const [userKeyword, setUserKeyword] = useState("");
  const [userPlatform, setUserPlatform] = useState("");
  const [userBlocked, setUserBlocked] = useState("all");
  const [userPage, setUserPage] = useState(1);
  const userSize = 20;
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);

  // -- Conversations --
  const [conversationKeyword, setConversationKeyword] = useState("");
  const [conversationUserId, setConversationUserId] = useState("");
  const [conversationPage, setConversationPage] = useState(1);
  const conversationSize = 20;
  const [selectedConversationId, setSelectedConversationId] = useState<number | null>(null);

  // -- Skills --
  const [skillKeyword, setSkillKeyword] = useState("");
  const [skillStatus, setSkillStatus] = useState("");
  const [skillPage, setSkillPage] = useState(1);
  const skillSize = 20;

  // -- Audit --
  const [auditKeyword, setAuditKeyword] = useState("");
  const [auditAction, setAuditAction] = useState("");
  const [auditUserId, setAuditUserId] = useState("");
  const [auditPage, setAuditPage] = useState(1);
  const auditSize = 30;

  // -- Feedback --
  const [feedbackKeyword, setFeedbackKeyword] = useState("");
  const [feedbackPlatform, setFeedbackPlatform] = useState("");
  const [feedbackUserId, setFeedbackUserId] = useState("");
  const [feedbackPage, setFeedbackPage] = useState(1);
  const feedbackSize = 30;

  // -- MiniApp Popup --
  const [popupDraft, setPopupDraft] = useState<AdminMiniappHomePopupConfig | null>(null);

  // -- Admin prefs --
  const [showExecutionPanel, setShowExecutionPanel] = useState<boolean>(() => getAdminShowExecutionPanel());

  /* ─── Queries ─── */

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

  const userDetail = useQuery<AdminUserDetail>({
    queryKey: ["admin", "user-detail", token, selectedUserId],
    queryFn: () => fetchAdminUserDetail(token, selectedUserId as number, 120),
    enabled: !!token && !!selectedUserId,
  });

  const conversations = useQuery({
    queryKey: ["admin", "conversations", token, conversationPage, conversationSize, conversationKeyword, conversationUserId],
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
      fetchAdminConversationMessages(token, selectedConversationId as number, { page: 1, size: 300 }),
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

  const feedbacks = useQuery({
    queryKey: ["admin", "feedbacks", token, feedbackPage, feedbackSize, feedbackKeyword, feedbackPlatform, feedbackUserId],
    queryFn: () =>
      fetchAdminFeedbacks(token, {
        page: feedbackPage,
        size: feedbackSize,
        q: feedbackKeyword || undefined,
        platform: feedbackPlatform || undefined,
        user_id: feedbackUserId ? Number(feedbackUserId) : undefined,
      }),
    enabled: !!token,
  });

  const miniappPopup = useQuery({
    queryKey: ["admin", "miniapp-home-popup", token],
    queryFn: () => fetchAdminMiniappHomePopup(token),
    enabled: !!token,
  });

  /* ─── Auto-select effects ─── */

  useEffect(() => {
    if (activeSection !== "conversations" || selectedConversationId) return;
    const first = conversations.data?.items?.[0]?.id;
    if (first) setSelectedConversationId(first);
  }, [activeSection, conversations.data, selectedConversationId]);

  useEffect(() => {
    if (activeSection !== "users" || selectedUserId) return;
    const first = users.data?.items?.[0]?.id;
    if (first) setSelectedUserId(first);
  }, [activeSection, users.data, selectedUserId]);

  useEffect(() => {
    if (!miniappPopup.data) return;
    setPopupDraft(miniappPopup.data);
  }, [miniappPopup.data]);

  /* ─── Mutations ─── */

  const blockMutation = useMutation({
    mutationFn: (payload: { userId: number; blocked: boolean; reason?: string }) =>
      adminSetUserBlock(token, payload.userId, { is_blocked: payload.blocked, reason: payload.reason || "" }),
    onSuccess: async (_, variables) => {
      setStatusMessage(variables.blocked ? `用户 #${variables.userId} 已封禁` : `用户 #${variables.userId} 已解封`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", token] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "user-detail", token] });
    },
    onError: (err) => setStatusMessage(`操作失败：${err instanceof Error ? err.message : "未知错误"}`),
  });

  const quotaMutation = useMutation({
    mutationFn: (payload: { userId: number; dailyLimit?: number; monthlyLimit?: number }) =>
      adminSetUserQuota(token, payload.userId, {
        ...(payload.dailyLimit !== undefined ? { daily_message_limit: payload.dailyLimit } : {}),
        ...(payload.monthlyLimit !== undefined ? { monthly_message_limit: payload.monthlyLimit } : {}),
      }),
    onSuccess: async (_, variables) => {
      setStatusMessage(`用户 #${variables.userId} 配额已更新`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", token] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "user-detail", token] });
    },
    onError: (err) => setStatusMessage(`配额更新失败：${err instanceof Error ? err.message : "未知错误"}`),
  });

  const consolidateUserMemoriesMutation = useMutation({
    mutationFn: (payload: { userId: number; maxScan?: number }) =>
      adminConsolidateUserMemories(token, payload.userId, payload.maxScan ?? 200),
    onSuccess: async (res) => {
      setStatusMessage(
        `用户 #${res.user_id} 清洗完成：reviewed=${res.reviewed}, updated=${res.updated}, deleted=${res.deleted}, merged=${res.merged}`
      );
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", token] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "user-detail", token] });
    },
    onError: (err) => setStatusMessage(`清洗失败：${err instanceof Error ? err.message : "未知错误"}`),
  });

  const consolidateAllMemoriesMutation = useMutation({
    mutationFn: (payload: { limitUsers?: number; maxScan?: number }) =>
      adminConsolidateAllMemories(token, { limit_users: payload.limitUsers ?? 200, max_scan: payload.maxScan ?? 200 }),
    onSuccess: async (res: AdminMemoryConsolidateAllResponse) => {
      setStatusMessage(
        `全量清洗完成：users=${res.scanned_users}, reviewed=${res.totals.reviewed}, updated=${res.totals.updated}, deleted=${res.totals.deleted}, merged=${res.totals.merged}`
      );
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", token] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "user-detail", token] });
    },
    onError: (err) => setStatusMessage(`全量清洗失败：${err instanceof Error ? err.message : "未知错误"}`),
  });

  const deleteUserMemoriesMutation = useMutation({
    mutationFn: (payload: { userId: number }) => adminDeleteUserMemories(token, payload.userId),
    onSuccess: async (res) => {
      setStatusMessage(`用户 #${res.user_id} 长期记忆已物理删除 ${res.deleted} 条`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", token] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "user-detail", token] });
    },
    onError: (err) => setStatusMessage(`物理删除失败：${err instanceof Error ? err.message : "未知错误"}`),
  });

  const deleteAllMemoriesMutation = useMutation({
    mutationFn: (payload: { limitUsers?: number }) =>
      adminDeleteAllMemories(token, { limit_users: payload.limitUsers ?? 5000 }),
    onSuccess: async (res: AdminMemoryPurgeAllResponse) => {
      setStatusMessage(`全量长期记忆已物理删除 ${res.deleted} 条（用户数=${res.scanned_users}）`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", token] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "user-detail", token] });
    },
    onError: (err) => setStatusMessage(`全量物理删除失败：${err instanceof Error ? err.message : "未知错误"}`),
  });

  const toolMutation = useMutation({
    mutationFn: (payload: { tool: AdminToolItem; enabled: boolean }) =>
      adminSetToolSwitch(token, payload.tool.source, payload.tool.name, payload.enabled),
    onSuccess: async (_, variables) => {
      setStatusMessage(`工具 ${variables.tool.name} 已${variables.enabled ? "启用" : "停用"}`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "tools", token] });
    },
    onError: (err) => setStatusMessage(`工具操作失败：${err instanceof Error ? err.message : "未知错误"}`),
  });

  const disableSkillMutation = useMutation({
    mutationFn: (skillId: number) => adminDisableSkill(token, skillId),
    onSuccess: async (_, skillId) => {
      setStatusMessage(`技能 #${skillId} 已停用`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "skills", token] });
    },
    onError: (err) => setStatusMessage(`技能操作失败：${err instanceof Error ? err.message : "未知错误"}`),
  });

  const savePopupMutation = useMutation({
    mutationFn: (payload: AdminMiniappHomePopupConfig) => saveAdminMiniappHomePopup(token, payload),
    onSuccess: async (res) => {
      setPopupDraft(res.config);
      setStatusMessage(`弹窗配置已保存（${fmtDateTime(res.updated_at)}）`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "miniapp-home-popup", token] });
    },
    onError: (err) => setStatusMessage(`弹窗保存失败：${err instanceof Error ? err.message : "未知错误"}`),
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

  /* ─── Login Screen ─── */

  if (!token) {
    return (
      <div className="min-h-screen bg-surface p-6 flex items-center justify-center">
        <Card className="w-full max-w-md">
          <CardHeader className="text-2xl font-semibold text-center">PAI Admin</CardHeader>
          <CardContent className="space-y-4">
            <p className="text-content-secondary text-sm text-center">请输入管理员令牌进入后台</p>
            <Input
              value={draftToken}
              onChange={(e) => setDraftToken(e.target.value)}
              placeholder="粘贴 ADMIN_TOKEN"
              onKeyDown={(e) => e.key === "Enter" && draftToken.trim() && setToken(draftToken)}
            />
            <div className="flex gap-3">
              <Button className="flex-1" onClick={() => setToken(draftToken)} disabled={!draftToken.trim()}>
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

  /* ─── Main Layout ─── */

  return (
    <div className="min-h-screen bg-surface p-4 md:p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <h1 className="text-xl md:text-2xl font-semibold">PAI Admin</h1>
          <select
            className="h-9 rounded-xl border border-border bg-surface-card px-3 text-sm"
            value={String(days)}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            <option value="7">近7天</option>
            <option value="30">近30天</option>
            <option value="60">近60天</option>
          </select>
          {dashboard.isFetching ? <Spinner size="sm" /> : null}
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={() => (window.location.href = "/")}>
            返回聊天
          </Button>
          <Button variant="danger" size="sm" onClick={() => setToken("")}>
            退出后台
          </Button>
        </div>
      </div>

      {tokenError ? (
        <Card>
          <CardContent className="text-danger py-4">{tokenError}</CardContent>
        </Card>
      ) : null}

      <div className="grid lg:grid-cols-[200px_minmax(0,1fr)] gap-4">
        {/* Sidebar */}
        <Card className="h-fit lg:sticky lg:top-4">
          <CardContent className="py-3 px-3 space-y-1">
            {SECTION_ITEMS.map((item) => (
              <button
                key={item.key}
                className={`w-full text-left px-3 py-2 rounded-xl text-sm transition-colors flex items-center gap-2 ${
                  activeSection === item.key
                    ? "bg-content text-surface font-medium"
                    : "text-content hover:bg-surface-hover"
                }`}
                onClick={() => setActiveSection(item.key)}
              >
                <span className="text-base">{item.icon}</span>
                {item.label}
              </button>
            ))}
          </CardContent>
        </Card>

        {/* Content Area */}
        <div className="space-y-4 min-w-0">

          {/* ═══ Dashboard ═══ */}
          {activeSection === "dashboard" ? (
            <>
              <Card>
                <CardHeader className="text-base font-semibold">Web 聊天显示设置</CardHeader>
                <CardContent className="space-y-2">
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox"
                      checked={showExecutionPanel}
                      onChange={(e) => {
                        const next = e.target.checked;
                        setShowExecutionPanel(next);
                        setAdminShowExecutionPanel(next);
                      }}
                    />
                    显示聊天页右侧"执行"标签
                  </label>
                  <div className="text-xs text-content-secondary">
                    此开关仅控制前端展示，关闭后聊天页不再显示执行摘要标签。
                  </div>
                </CardContent>
              </Card>

              {dashboard.isLoading ? (
                <SectionLoading text="加载运营数据..." />
              ) : dashboard.error ? (
                <SectionError
                  message={dashboard.error instanceof Error ? dashboard.error.message : "加载失败"}
                  onRetry={() => dashboard.refetch()}
                />
              ) : (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <StatCard label="用户总数" value={fmtNum(cards?.total_users)} />
                    <StatCard label="今日新增" value={fmtNum(cards?.new_users_today)} />
                    <StatCard label="DAU" value={fmtNum(cards?.dau_today)} />
                    <StatCard
                      label={`消息量（近${days}天 / 总）`}
                      value={fmtNum(cards?.window_messages)}
                      sub={`总 ${fmtNum(cards?.total_messages)}`}
                    />
                    <StatCard label="输入 Token" value={fmtNum(cards?.total_prompt_tokens)} />
                    <StatCard label="输出 Token" value={fmtNum(cards?.total_completion_tokens)} />
                    <StatCard label="Token 总量" value={fmtNum(cards?.total_tokens)} />
                    <StatCard
                      label="可计量调用"
                      value={cards ? `${fmtNum(cards.metered_calls)}/${fmtNum(cards.llm_calls)}` : "-"}
                      sub={`未计量 ${fmtNum(cards?.unmetered_calls)}`}
                    />
                  </div>

                  <div className="grid xl:grid-cols-2 gap-4">
                    <Card>
                      <CardHeader className="text-base font-semibold">趋势图（用户/消息/Token）</CardHeader>
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
                      <CardHeader className="text-base font-semibold">会话消息趋势（user/assistant）</CardHeader>
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
                      <CardHeader className="text-base font-semibold">意图分布</CardHeader>
                      <CardContent className="space-y-3">
                        <div className="h-[240px]">
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie
                                data={dashboard.data?.intent_distribution || []}
                                dataKey="count"
                                nameKey="name"
                                outerRadius={90}
                                labelLine={false}
                                label={(entry: { count: number }) => fmtNum(entry.count)}
                              >
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
                      <CardHeader className="text-base font-semibold">平台分布</CardHeader>
                      <CardContent className="space-y-3">
                        <div className="h-[240px]">
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie
                                data={dashboard.data?.platform_distribution || []}
                                dataKey="count"
                                nameKey="platform"
                                outerRadius={90}
                                labelLine={false}
                                label={(entry: { count: number }) => fmtNum(entry.count)}
                              >
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
              )}
            </>
          ) : null}

          {/* ═══ Users ═══ */}
          {activeSection === "users" ? (
            <Card>
              <CardHeader className="text-base font-semibold">用户管理</CardHeader>
              <CardContent className="space-y-3">
                <div className="grid md:grid-cols-4 gap-2">
                  <Input
                    placeholder="搜索昵称/邮箱/平台ID"
                    value={userKeyword}
                    onChange={(e) => { setUserKeyword(e.target.value); setUserPage(1); }}
                  />
                  <select
                    className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm"
                    value={userPlatform}
                    onChange={(e) => { setUserPlatform(e.target.value); setUserPage(1); }}
                  >
                    <option value="">全部平台</option>
                    {USER_PLATFORM_OPTIONS.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                  <select
                    className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm"
                    value={userBlocked}
                    onChange={(e) => { setUserBlocked(e.target.value); setUserPage(1); }}
                  >
                    <option value="all">全部状态</option>
                    <option value="false">未封禁</option>
                    <option value="true">已封禁</option>
                  </select>
                </div>

                {/* Batch memory operations */}
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    variant="default"
                    disabled={consolidateAllMemoriesMutation.isPending}
                    onClick={() =>
                      confirm({
                        title: "全量长期记忆清洗",
                        description: "该操作会去重并下线低价值记忆，最多扫描 500 个用户、每用户 200 条。确认执行？",
                        onConfirm: () => {
                          closeConfirm();
                          consolidateAllMemoriesMutation.mutate({ limitUsers: 500, maxScan: 200 });
                        },
                      })
                    }
                  >
                    {consolidateAllMemoriesMutation.isPending ? (
                      <><Spinner size="sm" /> 清洗中...</>
                    ) : "一键清洗全量长期记忆"}
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={deleteAllMemoriesMutation.isPending}
                    onClick={() =>
                      confirm({
                        title: "物理删除全量长期记忆",
                        description: "该操作不可恢复！将删除所有用户的长期记忆数据。确认执行？",
                        variant: "danger",
                        onConfirm: () => {
                          closeConfirm();
                          deleteAllMemoriesMutation.mutate({ limitUsers: 5000 });
                        },
                      })
                    }
                  >
                    {deleteAllMemoriesMutation.isPending ? (
                      <><Spinner size="sm" /> 删除中...</>
                    ) : "物理删除全量长期记忆"}
                  </Button>
                  <span className="text-xs text-content-secondary">
                    批量策略：最多 500 用户，每用户最多扫描 200 条活跃记忆
                  </span>
                </div>

                {/* User Table */}
                {users.isLoading ? (
                  <SectionLoading text="加载用户列表..." />
                ) : users.error ? (
                  <SectionError
                    message={users.error instanceof Error ? users.error.message : "加载失败"}
                    onRetry={() => users.refetch()}
                  />
                ) : !users.data?.items?.length ? (
                  <EmptyState text="暂无匹配用户" />
                ) : (
                  <div className="overflow-auto">
                    <table className="w-full text-sm min-w-[900px]">
                      <thead>
                        <tr className="text-left text-content-secondary border-b border-border">
                          <th className="py-2 pr-2">ID</th>
                          <th className="py-2 pr-2">昵称</th>
                          <th className="py-2 pr-2">AI</th>
                          <th className="py-2 pr-2">平台</th>
                          <th className="py-2 pr-2">创建时间</th>
                          <th className="py-2 pr-2">消息</th>
                          <th className="py-2 pr-2">账单</th>
                          <th className="py-2 pr-2">技能</th>
                          <th className="py-2 pr-2">日/月配额</th>
                          <th className="py-2 pr-2">状态</th>
                          <th className="py-2 pr-2">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {users.data.items.map((row: AdminUserItem) => (
                          <tr
                            key={row.id}
                            className={`border-b border-border/60 hover:bg-surface-hover/50 transition-colors ${selectedUserId === row.id ? "bg-surface-hover" : ""}`}
                          >
                            <td className="py-2 pr-2 font-mono text-xs">#{row.id}</td>
                            <td className="py-2 pr-2">{row.nickname}</td>
                            <td className="py-2 pr-2">{`${row.ai_name || "PAI"} ${row.ai_emoji || ""}`}</td>
                            <td className="py-2 pr-2">
                              <span className="inline-block px-1.5 py-0.5 rounded-md bg-surface-hover text-xs">{row.platform}</span>
                            </td>
                            <td className="py-2 pr-2 text-xs">{fmtDateTime(row.created_at)}</td>
                            <td className="py-2 pr-2">{row.message_count}</td>
                            <td className="py-2 pr-2">{row.ledger_count}</td>
                            <td className="py-2 pr-2">{row.skill_count}</td>
                            <td className="py-2 pr-2">
                              <div className="flex items-center gap-1">
                                <Input
                                  className="h-7 w-16 text-xs"
                                  defaultValue={String(row.daily_message_limit || 0)}
                                  title="每日配额"
                                  onBlur={(e) => {
                                    const v = Number(e.target.value || 0);
                                    if (!Number.isFinite(v)) return;
                                    quotaMutation.mutate({ userId: row.id, dailyLimit: Math.max(0, Math.floor(v)) });
                                  }}
                                />
                                <span className="text-content-secondary text-xs">/</span>
                                <Input
                                  className="h-7 w-16 text-xs"
                                  defaultValue={String(row.monthly_message_limit || 0)}
                                  title="每月配额（0=不限）"
                                  onBlur={(e) => {
                                    const v = Number(e.target.value || 0);
                                    if (!Number.isFinite(v)) return;
                                    quotaMutation.mutate({ userId: row.id, monthlyLimit: Math.max(0, Math.floor(v)) });
                                  }}
                                />
                              </div>
                            </td>
                            <td className="py-2 pr-2">
                              <span className={`inline-block px-2 py-0.5 rounded-md text-xs ${row.is_blocked ? "bg-red-100 text-red-600" : "bg-green-100 text-green-700"}`}>
                                {row.is_blocked ? "已封禁" : "正常"}
                              </span>
                            </td>
                            <td className="py-2 pr-2">
                              <div className="flex gap-1">
                                <Button size="sm" variant="ghost" onClick={() => setSelectedUserId(row.id)}>
                                  详情
                                </Button>
                                <Button
                                  size="sm"
                                  variant={row.is_blocked ? "default" : "danger"}
                                  disabled={blockMutation.isPending}
                                  onClick={() => {
                                    if (row.is_blocked) {
                                      blockMutation.mutate({ userId: row.id, blocked: false });
                                    } else {
                                      confirm({
                                        title: `封禁用户 #${row.id}`,
                                        description: `确认封禁用户「${row.nickname}」？封禁后该用户无法使用系统。`,
                                        variant: "danger",
                                        onConfirm: () => {
                                          closeConfirm();
                                          blockMutation.mutate({ userId: row.id, blocked: true, reason: "admin block" });
                                        },
                                      });
                                    }
                                  }}
                                >
                                  {row.is_blocked ? "解封" : "封禁"}
                                </Button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <Pager
                  page={users.data?.page || 1}
                  size={users.data?.size || userSize}
                  total={users.data?.total || 0}
                  onPageChange={setUserPage}
                />

                {/* User Detail Panel */}
                {selectedUserId ? (
                  <div className="rounded-xl border border-border bg-surface-card p-4 space-y-3">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                      <h3 className="text-sm font-semibold">用户档案与长期记忆 · #{selectedUserId}</h3>
                      <div className="flex items-center gap-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={consolidateUserMemoriesMutation.isPending}
                          onClick={() =>
                            consolidateUserMemoriesMutation.mutate({ userId: selectedUserId, maxScan: 300 })
                          }
                        >
                          {consolidateUserMemoriesMutation.isPending ? (
                            <><Spinner size="sm" /> 清洗中</>
                          ) : "清洗当前用户记忆"}
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={deleteUserMemoriesMutation.isPending}
                          onClick={() =>
                            confirm({
                              title: `物理删除用户 #${selectedUserId} 记忆`,
                              description: "该操作不可恢复！将永久删除该用户的所有长期记忆。",
                              variant: "danger",
                              onConfirm: () => {
                                closeConfirm();
                                deleteUserMemoriesMutation.mutate({ userId: selectedUserId });
                              },
                            })
                          }
                        >
                          {deleteUserMemoriesMutation.isPending ? (
                            <><Spinner size="sm" /> 删除中</>
                          ) : "物理删除"}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setSelectedUserId(null)}>
                          关闭
                        </Button>
                      </div>
                    </div>

                    {userDetail.isLoading ? (
                      <SectionLoading text="加载用户详情..." />
                    ) : userDetail.error ? (
                      <SectionError
                        message={userDetail.error instanceof Error ? userDetail.error.message : "加载失败"}
                        onRetry={() => userDetail.refetch()}
                      />
                    ) : userDetail.data ? (
                      <div className="space-y-3">
                        <div className="grid md:grid-cols-3 gap-2 text-xs">
                          <div className="rounded-lg border border-border p-2">昵称: {userDetail.data.profile?.nickname || "-"}</div>
                          <div className="rounded-lg border border-border p-2">AI: {userDetail.data.profile?.ai_name || "PAI"} {userDetail.data.profile?.ai_emoji || ""}</div>
                          <div className="rounded-lg border border-border p-2">平台: {userDetail.data.profile?.platform || "-"}</div>
                          <div className="rounded-lg border border-border p-2">创建时间: {fmtDateTime(userDetail.data.created_at)}</div>
                          <div className="rounded-lg border border-border p-2">最后活跃: {fmtDateTime(userDetail.data.last_active_at)}</div>
                          <div className="rounded-lg border border-border p-2">长期记忆数: {userDetail.data.stats?.memories ?? 0}</div>
                        </div>
                        <div className="overflow-auto max-h-[320px]">
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-left text-content-secondary border-b border-border">
                                <th className="py-2 pr-2">归属键</th>
                                <th className="py-2 pr-2">类型</th>
                                <th className="py-2 pr-2">值</th>
                                <th className="py-2 pr-2">重要性</th>
                                <th className="py-2 pr-2">置信度</th>
                                <th className="py-2 pr-2">更新时间</th>
                              </tr>
                            </thead>
                            <tbody>
                              {(userDetail.data.memories || []).map((memory) => (
                                <tr key={memory.id} className="border-b border-border/60 align-top">
                                  <td className="py-2 pr-2 font-mono text-[11px]">{memory.memory_key || "-"}</td>
                                  <td className="py-2 pr-2">{memory.memory_type}</td>
                                  <td className="py-2 pr-2 whitespace-pre-wrap break-words max-w-[300px]">{memory.content}</td>
                                  <td className="py-2 pr-2">{memory.importance}</td>
                                  <td className="py-2 pr-2">{Number(memory.confidence || 0).toFixed(2)}</td>
                                  <td className="py-2 pr-2">{fmtDateTime(memory.updated_at)}</td>
                                </tr>
                              ))}
                              {(!userDetail.data.memories || userDetail.data.memories.length === 0) ? (
                                <tr>
                                  <td colSpan={6} className="py-3 text-content-secondary text-center">暂无长期记忆</td>
                                </tr>
                              ) : null}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    ) : (
                      <EmptyState text="未找到用户详情" />
                    )}
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

          {/* ═══ Conversations ═══ */}
          {activeSection === "conversations" ? (
            <div className="grid xl:grid-cols-2 gap-4">
              <Card>
                <CardHeader className="text-base font-semibold">会话列表</CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid md:grid-cols-2 gap-2">
                    <Input
                      placeholder="会话标题搜索"
                      value={conversationKeyword}
                      onChange={(e) => { setConversationKeyword(e.target.value); setConversationPage(1); }}
                    />
                    <Input
                      placeholder="用户ID筛选"
                      value={conversationUserId}
                      onChange={(e) => { setConversationUserId(e.target.value); setConversationPage(1); }}
                    />
                  </div>

                  {conversations.isLoading ? (
                    <SectionLoading text="加载会话列表..." />
                  ) : conversations.error ? (
                    <SectionError
                      message={conversations.error instanceof Error ? conversations.error.message : "加载失败"}
                      onRetry={() => conversations.refetch()}
                    />
                  ) : !conversations.data?.items?.length ? (
                    <EmptyState text="暂无会话记录" />
                  ) : (
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
                          {conversations.data.items.map((row: AdminConversationItem) => (
                            <tr
                              key={row.id}
                              className={`border-b border-border/60 cursor-pointer transition-colors hover:bg-surface-hover/50 ${selectedConversationId === row.id ? "bg-surface-hover" : ""}`}
                              onClick={() => setSelectedConversationId(row.id)}
                            >
                              <td className="py-2 pr-2 max-w-[220px] truncate">#{row.id} {row.title}</td>
                              <td className="py-2 pr-2">{row.user_nickname || row.user_id}</td>
                              <td className="py-2 pr-2">{row.message_count}</td>
                              <td className="py-2 pr-2 text-xs">{fmtDateTime(row.last_message_at)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <Pager
                    page={conversations.data?.page || 1}
                    size={conversations.data?.size || conversationSize}
                    total={conversations.data?.total || 0}
                    onPageChange={setConversationPage}
                  />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="text-base font-semibold">
                  会话回放
                  {selectedConversationId ? <span className="text-xs text-content-secondary ml-2">#{selectedConversationId}</span> : null}
                </CardHeader>
                <CardContent className="space-y-2 max-h-[560px] overflow-auto">
                  {!selectedConversationId ? (
                    <EmptyState text="从左侧选择一个会话查看完整消息流" />
                  ) : conversationMessages.isLoading ? (
                    <SectionLoading text="加载消息..." />
                  ) : conversationMessages.error ? (
                    <SectionError
                      message={conversationMessages.error instanceof Error ? conversationMessages.error.message : "加载失败"}
                      onRetry={() => conversationMessages.refetch()}
                    />
                  ) : !conversationMessages.data?.items?.length ? (
                    <EmptyState text="该会话暂无消息" />
                  ) : (
                    conversationMessages.data.items.map((msg: AdminConversationMessageItem) => (
                      <div
                        key={msg.id}
                        className={`rounded-xl border px-3 py-2 text-sm ${
                          msg.role === "user"
                            ? "border-blue-200 bg-blue-50/30"
                            : "border-border bg-surface-card"
                        }`}
                      >
                        <div className="text-xs text-content-secondary mb-1 flex items-center gap-2">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            msg.role === "user" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600"
                          }`}>
                            {msg.role}
                          </span>
                          <span>{msg.platform}</span>
                          <span>{fmtDateTime(msg.created_at)}</span>
                        </div>
                        <div className="whitespace-pre-wrap break-words">{msg.content}</div>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>
            </div>
          ) : null}

          {/* ═══ Skills ═══ */}
          {activeSection === "skills" ? (
            <Card>
              <CardHeader className="text-base font-semibold">技能管理</CardHeader>
              <CardContent className="space-y-3">
                <div className="grid md:grid-cols-3 gap-2">
                  <Input
                    placeholder="技能名/slug搜索"
                    value={skillKeyword}
                    onChange={(e) => { setSkillKeyword(e.target.value); setSkillPage(1); }}
                  />
                  <select
                    className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm"
                    value={skillStatus}
                    onChange={(e) => { setSkillStatus(e.target.value); setSkillPage(1); }}
                  >
                    <option value="">全部状态</option>
                    <option value="DRAFT">草稿</option>
                    <option value="PUBLISHED">已发布</option>
                    <option value="DISABLED">已停用</option>
                  </select>
                </div>

                {skills.isLoading ? (
                  <SectionLoading text="加载技能列表..." />
                ) : skills.error ? (
                  <SectionError
                    message={skills.error instanceof Error ? skills.error.message : "加载失败"}
                    onRetry={() => skills.refetch()}
                  />
                ) : !skills.data?.items?.length ? (
                  <EmptyState text="暂无技能数据" />
                ) : (
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
                        {skills.data.items.map((skill: AdminSkillsItem) => (
                          <tr key={skill.id} className="border-b border-border/60 hover:bg-surface-hover/50 transition-colors">
                            <td className="py-2 pr-2 font-mono text-xs">#{skill.id}</td>
                            <td className="py-2 pr-2 font-medium">{skill.name}</td>
                            <td className="py-2 pr-2">{skill.user_nickname || skill.user_id}</td>
                            <td className="py-2 pr-2">
                              <span className={`inline-block px-2 py-0.5 rounded-md text-xs ${skillStatusBadge(skill.status)}`}>
                                {skillStatusLabel(skill.status)}
                              </span>
                            </td>
                            <td className="py-2 pr-2">v{skill.active_version}</td>
                            <td className="py-2 pr-2">
                              <Button
                                size="sm"
                                variant="danger"
                                disabled={skill.status === "DISABLED" || disableSkillMutation.isPending}
                                onClick={() =>
                                  confirm({
                                    title: `停用技能 #${skill.id}`,
                                    description: `确认强制停用技能「${skill.name}」？停用后所有用户无法使用该技能。`,
                                    variant: "danger",
                                    onConfirm: () => {
                                      closeConfirm();
                                      disableSkillMutation.mutate(skill.id);
                                    },
                                  })
                                }
                              >
                                强制停用
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <Pager
                  page={skills.data?.page || 1}
                  size={skills.data?.size || skillSize}
                  total={skills.data?.total || 0}
                  onPageChange={setSkillPage}
                />
              </CardContent>
            </Card>
          ) : null}

          {/* ═══ Tools ═══ */}
          {activeSection === "tools" ? (
            <Card>
              <CardHeader className="text-base font-semibold">
                工具管理
                {tools.isFetching ? <Spinner size="sm" className="inline-block ml-2" /> : null}
              </CardHeader>
              <CardContent>
                {tools.isLoading ? (
                  <SectionLoading text="加载工具列表..." />
                ) : tools.error ? (
                  <SectionError
                    message={tools.error instanceof Error ? tools.error.message : "加载失败"}
                    onRetry={() => tools.refetch()}
                  />
                ) : !tools.data?.items?.length ? (
                  <EmptyState text="暂无工具数据" />
                ) : (
                  <div className="overflow-auto">
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
                        {tools.data.items.map((tool: AdminToolItem) => (
                          <tr key={`${tool.source}:${tool.name}`} className="border-b border-border/60 hover:bg-surface-hover/50 transition-colors">
                            <td className="py-2 pr-2">
                              <span className="inline-block px-1.5 py-0.5 rounded-md bg-surface-hover text-xs">{tool.source}</span>
                            </td>
                            <td className="py-2 pr-2 font-mono text-xs">{tool.name}</td>
                            <td className="py-2 pr-2">{tool.calls}</td>
                            <td className="py-2 pr-2">
                              <span className={`${tool.success_rate >= 0.95 ? "text-green-600" : tool.success_rate >= 0.8 ? "text-yellow-600" : "text-red-600"}`}>
                                {(tool.success_rate * 100).toFixed(1)}%
                              </span>
                            </td>
                            <td className="py-2 pr-2">{tool.avg_latency_ms}ms</td>
                            <td className="py-2 pr-2">
                              <Button
                                size="sm"
                                variant={tool.enabled ? "danger" : "default"}
                                disabled={toolMutation.isPending}
                                onClick={() => toolMutation.mutate({ tool, enabled: !tool.enabled })}
                              >
                                {tool.enabled ? "停用" : "启用"}
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>
          ) : null}

          {/* ═══ Delivery ═══ */}
          {activeSection === "delivery" ? (
            <Card>
              <CardHeader className="text-base font-semibold">提醒投递统计</CardHeader>
              <CardContent className="space-y-4">
                {delivery.isLoading ? (
                  <SectionLoading text="加载投递数据..." />
                ) : delivery.error ? (
                  <SectionError
                    message={delivery.error instanceof Error ? delivery.error.message : "加载失败"}
                    onRetry={() => delivery.refetch()}
                  />
                ) : !delivery.data?.items?.length ? (
                  <EmptyState text="暂无投递数据" />
                ) : (
                  <>
                    <div className="space-y-2 text-sm">
                      {delivery.data.items.map((row) => (
                        <div key={row.platform} className="rounded-xl border border-border px-3 py-2 flex justify-between items-center">
                          <span className="font-medium">{row.platform}</span>
                          <span className="text-content-secondary">
                            总{row.total} / 成功{row.delivered} / 失败{row.failed} / 成功率
                            <span className={`ml-1 ${row.success_rate >= 0.95 ? "text-green-600" : "text-red-600"}`}>
                              {(row.success_rate * 100).toFixed(1)}%
                            </span>
                          </span>
                        </div>
                      ))}
                    </div>
                    <div className="h-[300px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={delivery.data.items}>
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
                  </>
                )}
              </CardContent>
            </Card>
          ) : null}

          {/* ═══ MiniApp Popup ═══ */}
          {activeSection === "miniappPopup" ? (
            <Card>
              <CardHeader className="text-base font-semibold">首页弹窗配置</CardHeader>
              <CardContent className="space-y-4">
                {miniappPopup.error instanceof Error ? (
                  <SectionError
                    message={miniappPopup.error.message}
                    onRetry={() => miniappPopup.refetch()}
                  />
                ) : null}

                {miniappPopup.isLoading ? (
                  <SectionLoading text="加载弹窗配置..." />
                ) : !popupDraft ? (
                  <EmptyState text="暂无配置" />
                ) : (
                  <>
                    <label className="flex items-center gap-2 text-sm cursor-pointer">
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
                        className="w-full min-h-[120px] rounded-xl border border-border bg-surface-card px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent/50"
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
                                ? { ...prev, show_mode: e.target.value as AdminMiniappHomePopupConfig["show_mode"] }
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
                          <span className={`inline-block px-2 py-0.5 rounded-md text-xs ${popupDraft.enabled ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"}`}>
                            {popupDraft.enabled ? "已启用" : "已停用"}
                          </span>
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
                        {savePopupMutation.isPending ? <><Spinner size="sm" /> 保存中...</> : "保存配置"}
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

          {/* ═══ Feedback ═══ */}
          {activeSection === "feedback" ? (
            <Card>
              <CardHeader className="text-base font-semibold">问题反馈</CardHeader>
              <CardContent className="space-y-3">
                <div className="grid md:grid-cols-3 gap-2">
                  <Input
                    placeholder="内容/页面/版本搜索"
                    value={feedbackKeyword}
                    onChange={(e) => { setFeedbackKeyword(e.target.value); setFeedbackPage(1); }}
                  />
                  <Input
                    placeholder="user_id 筛选"
                    value={feedbackUserId}
                    onChange={(e) => { setFeedbackUserId(e.target.value); setFeedbackPage(1); }}
                  />
                  <select
                    className="h-10 rounded-xl border border-border bg-surface-card px-3 text-sm"
                    value={feedbackPlatform}
                    onChange={(e) => { setFeedbackPlatform(e.target.value); setFeedbackPage(1); }}
                  >
                    <option value="">全部平台</option>
                    {USER_PLATFORM_OPTIONS.map((p) => (
                      <option key={`feedback-platform-${p}`} value={p}>{p}</option>
                    ))}
                  </select>
                </div>

                {feedbacks.isLoading ? (
                  <SectionLoading text="加载反馈列表..." />
                ) : feedbacks.error ? (
                  <SectionError
                    message={feedbacks.error instanceof Error ? feedbacks.error.message : "加载失败"}
                    onRetry={() => feedbacks.refetch()}
                  />
                ) : !feedbacks.data?.items?.length ? (
                  <EmptyState text="暂无反馈数据" />
                ) : (
                  <div className="overflow-auto max-h-[560px]">
                    <table className="w-full text-sm min-w-[700px]">
                      <thead>
                        <tr className="text-left text-content-secondary border-b border-border">
                          <th className="py-2 pr-2">ID</th>
                          <th className="py-2 pr-2">用户</th>
                          <th className="py-2 pr-2">平台</th>
                          <th className="py-2 pr-2">反馈内容</th>
                          <th className="py-2 pr-2">页面</th>
                          <th className="py-2 pr-2">版本</th>
                          <th className="py-2 pr-2">时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {feedbacks.data.items.map((row: AdminFeedbackItem) => (
                          <tr key={row.id} className="border-b border-border/60 align-top hover:bg-surface-hover/50 transition-colors">
                            <td className="py-2 pr-2 font-mono text-xs">#{row.id}</td>
                            <td className="py-2 pr-2">{row.user_nickname || `#${row.user_id}`}</td>
                            <td className="py-2 pr-2">
                              <span className="inline-block px-1.5 py-0.5 rounded-md bg-surface-hover text-xs">{row.platform || "-"}</span>
                            </td>
                            <td className="py-2 pr-2 whitespace-pre-wrap break-words min-w-[260px]">{row.content}</td>
                            <td className="py-2 pr-2 break-all text-xs">{row.client_page || "-"}</td>
                            <td className="py-2 pr-2 text-xs">
                              <div>{row.app_version || "-"}</div>
                              <div className="text-content-secondary">{row.env_version || "-"}</div>
                            </td>
                            <td className="py-2 pr-2 text-xs">{fmtDateTime(row.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <Pager
                  page={feedbacks.data?.page || 1}
                  size={feedbacks.data?.size || feedbackSize}
                  total={feedbacks.data?.total || 0}
                  onPageChange={setFeedbackPage}
                />
              </CardContent>
            </Card>
          ) : null}

          {/* ═══ Audit ═══ */}
          {activeSection === "audit" ? (
            <Card>
              <CardHeader className="text-base font-semibold">审计日志</CardHeader>
              <CardContent className="space-y-3">
                <div className="grid md:grid-cols-3 gap-2">
                  <Input
                    placeholder="action 筛选"
                    value={auditAction}
                    onChange={(e) => { setAuditAction(e.target.value); setAuditPage(1); }}
                  />
                  <Input
                    placeholder="user_id 筛选"
                    value={auditUserId}
                    onChange={(e) => { setAuditUserId(e.target.value); setAuditPage(1); }}
                  />
                  <Input
                    placeholder="detail 搜索"
                    value={auditKeyword}
                    onChange={(e) => { setAuditKeyword(e.target.value); setAuditPage(1); }}
                  />
                </div>

                {audit.isLoading ? (
                  <SectionLoading text="加载审计日志..." />
                ) : audit.error ? (
                  <SectionError
                    message={audit.error instanceof Error ? audit.error.message : "加载失败"}
                    onRetry={() => audit.refetch()}
                  />
                ) : !audit.data?.items?.length ? (
                  <EmptyState text="暂无审计数据" />
                ) : (
                  <div className="space-y-2 max-h-[520px] overflow-auto text-xs">
                    {audit.data.items.map((row: AdminAuditItem) => (
                      <div key={row.id} className="rounded-xl border border-border px-3 py-2 hover:bg-surface-hover/30 transition-colors">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium text-sm">{row.action}</span>
                          <span className="text-content-secondary shrink-0">{fmtDateTime(row.created_at)}</span>
                        </div>
                        <div className="text-content-secondary mt-1">
                          user={row.user_id || "-"} platform={row.platform}
                        </div>
                        <pre className="mt-2 whitespace-pre-wrap break-all text-[11px] text-content-secondary bg-surface rounded-lg p-2">
                          {typeof row.detail === "string" ? row.detail : JSON.stringify(row.detail, null, 2)}
                        </pre>
                      </div>
                    ))}
                  </div>
                )}

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

      {/* Confirm Dialog */}
      <ConfirmDialog
        open={confirmState.open}
        title={confirmState.title}
        description={confirmState.description}
        variant={confirmState.variant}
        onConfirm={confirmState.onConfirm}
        onCancel={closeConfirm}
      />

      {/* Status Toast */}
      {statusMessage ? (
        <StatusToast message={statusMessage} onClose={() => setStatusMessage("")} />
      ) : null}
    </div>
  );
}
