export interface TokenResponse {
  access_token: string;
}

export interface Profile {
  uuid: string;
  nickname: string;
  ai_name: string;
  ai_emoji: string;
  platform: string;
  email?: string | null;
  setup_stage: number;
  binding_stage?: number;
}

export interface ChatMessage {
  role: string;
  content: string;
  created_at: string;
  image_urls?: string[];
}

export interface ChatSendResponse {
  responses: string[];
  debug?: Record<string, unknown> | null;
}

export interface ConversationItem {
  id: number;
  title: string;
  summary: string;
  last_message_at: string;
  active: boolean;
}

export interface ConversationDeleteResponse {
  ok: boolean;
  deleted_id: number;
  deleted_title: string;
  active_conversation: ConversationItem;
}

export interface UserIdentity {
  platform: string;
  platform_id: string;
}

export interface BindCodeCreateResponse {
  code: string;
  expires_at: string;
  ttl_minutes: number;
}

export interface BindCodeConsumeResponse {
  ok: boolean;
  message: string;
  canonical_user_id?: number | null;
  access_token?: string | null;
}

export interface FeedbackCreateResponse {
  ok: boolean;
  id: number;
  created_at: string;
}

export interface SkillItem {
  slug: string;
  name: string;
  description: string;
  status: string;
  active_version: number;
  source: string;
  read_only: boolean;
}

export interface LedgerStats {
  total: number;
  count: number;
}

export interface LedgerItem {
  id: number;
  amount: number;
  currency: string;
  category: string;
  item: string;
  transaction_date: string;
  created_at: string;
}

export interface CalendarLedgerItem {
  id: number;
  amount: number;
  currency: string;
  category: string;
  item: string;
  transaction_date: string;
}

export interface CalendarScheduleItem {
  id: number;
  content: string;
  trigger_time: string;
  status: string;
}

export interface CalendarDay {
  date: string;
  ledger_total: number;
  ledger_count: number;
  schedule_count: number;
  ledgers: CalendarLedgerItem[];
  schedules: CalendarScheduleItem[];
}

export interface CalendarResponse {
  start_date: string;
  end_date: string;
  days: CalendarDay[];
}

export interface ScheduleItem {
  id: number;
  content: string;
  trigger_time: string;
  status: string;
  created_at: string;
}

const RAW_API_BASE = String(process.env.EXPO_PUBLIC_API_BASE_URL || "").trim();
export const API_BASE = RAW_API_BASE.replace(/\/+$/, "");
export const API_BASE_HELP =
  "未配置 EXPO_PUBLIC_API_BASE_URL。请在 mobile/.env 中设置后端地址，例如 http://192.168.1.10:8000";

function ensureApiBase() {
  if (!API_BASE) {
    throw new Error(API_BASE_HELP);
  }
  return API_BASE;
}

function translateDetail(detail: string) {
  const text = String(detail || "").toLowerCase();
  if (text.includes("invalid credentials")) return "邮箱或密码错误。";
  if (text.includes("missing token")) return "登录状态缺失，请重新登录。";
  if (text.includes("invalid token")) return "登录状态已失效，请重新登录。";
  if (text.includes("password too short")) return "密码至少 6 位。";
  if (text.includes("email not registered")) return "该邮箱尚未注册。";
  if (text.includes("email already exists")) return "该邮箱已注册，请直接登录。";
  if (text.includes("invalid bind code format")) return "请输入 6 位数字绑定码。";
  if (text.includes("feedback content too short")) return "反馈内容至少 4 个字。";
  return detail || "请求失败。";
}

function fallbackMessage(status: number) {
  if (status === 400) return "请求参数不正确。";
  if (status === 401) return "登录状态已失效，请重新登录。";
  if (status === 403) return "当前操作无权限。";
  if (status === 404) return "请求的资源不存在。";
  if (status >= 500) return "服务器暂时不可用，请稍后重试。";
  return `请求失败（${status}）`;
}

async function parseError(res: Response) {
  const contentType = res.headers.get("content-type") || "";
  let payload: unknown = null;
  try {
    payload = contentType.includes("application/json") ? await res.json() : await res.text();
  } catch {
    payload = null;
  }

  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) {
      return translateDetail(detail.trim());
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (first?.msg) return translateDetail(first.msg);
    }
  }

  if (typeof payload === "string" && payload.trim()) {
    return translateDetail(payload.trim());
  }

  return fallbackMessage(res.status);
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  token?: string | null
): Promise<T> {
  const base = ensureApiBase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers ? (options.headers as Record<string, string>) : {}),
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...options,
      headers,
    });
  } catch {
    throw new Error("网络连接失败，请检查手机与后端是否可互通。");
  }

  if (!res.ok) {
    throw new Error(await parseError(res));
  }

  return (await res.json()) as T;
}

export function loginWithPassword(email: string, password: string) {
  return apiRequest<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({
      email: email.trim(),
      password,
    }),
  });
}

export function fetchProfile(token: string) {
  return apiRequest<Profile>("/api/user/profile", {}, token);
}

export function fetchIdentities(token: string) {
  return apiRequest<UserIdentity[]>("/api/user/identities", {}, token);
}

export function fetchHistory(token: string) {
  return apiRequest<ChatMessage[]>("/api/chat/history", {}, token);
}

export function fetchConversations(token: string) {
  return apiRequest<ConversationItem[]>("/api/conversations", {}, token);
}

export function createBindCode(ttlMinutes: number, token: string) {
  return apiRequest<BindCodeCreateResponse>(
    "/api/user/bind-code",
    {
      method: "POST",
      body: JSON.stringify({ ttl_minutes: ttlMinutes }),
    },
    token
  );
}

export function consumeBindCode(code: string, token: string) {
  return apiRequest<BindCodeConsumeResponse>(
    "/api/user/bind-consume",
    {
      method: "POST",
      body: JSON.stringify({ code: code.trim() }),
    },
    token
  );
}

export function submitUserFeedback(
  payload: {
    content: string;
    app_version?: string;
    env_version?: string;
    client_page?: string;
  },
  token: string
) {
  return apiRequest<FeedbackCreateResponse>(
    "/api/user/feedback",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    token
  );
}

export function createConversation(title: string | undefined, token: string) {
  return apiRequest<ConversationItem>(
    "/api/conversations",
    {
      method: "POST",
      body: JSON.stringify(title ? { title } : {}),
    },
    token
  );
}

export function switchConversation(conversationId: number, token: string) {
  return apiRequest<ConversationItem>(
    `/api/conversations/${conversationId}/switch`,
    {
      method: "POST",
    },
    token
  );
}

export function deleteConversation(conversationId: number, token: string) {
  return apiRequest<ConversationDeleteResponse>(
    `/api/conversations/${conversationId}`,
    {
      method: "DELETE",
    },
    token
  );
}

export function sendChat(content: string, token: string) {
  return apiRequest<ChatSendResponse>(
    "/api/chat/send",
    {
      method: "POST",
      body: JSON.stringify({
        content,
        image_urls: [],
        // Backend currently only recognizes web / miniapp.
        source_platform: "web",
      }),
    },
    token
  );
}

export function fetchLedgerStats(token: string, scope: "day" | "week" | "month" = "month") {
  return apiRequest<LedgerStats>(`/api/stats/ledger?scope=${scope}`, {}, token);
}

export function fetchLedgers(token: string, limit = 30, beforeId?: number) {
  const safeLimit = Math.max(1, Math.min(200, Math.floor(Number(limit) || 30)));
  let path = `/api/ledgers?limit=${safeLimit}`;
  if (Number.isFinite(beforeId) && Number(beforeId) > 0) {
    path += `&before_id=${Math.floor(Number(beforeId))}`;
  }
  return apiRequest<LedgerItem[]>(path, {}, token);
}

export function fetchCalendar(token: string, startDate: string, endDate: string) {
  return apiRequest<CalendarResponse>(
    `/api/calendar?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`,
    {},
    token
  );
}

export function fetchSchedules(token: string, limit = 50) {
  return apiRequest<ScheduleItem[]>(`/api/schedules?limit=${limit}`, {}, token);
}

export function fetchSkills(token: string) {
  return apiRequest<SkillItem[]>("/api/skills", {}, token);
}

export function getNotificationsWsUrl(token: string) {
  const base = ensureApiBase();
  return `${base.replace(/^http:/, "ws:").replace(/^https:/, "wss:")}/api/notifications/ws?token=${encodeURIComponent(token)}`;
}
