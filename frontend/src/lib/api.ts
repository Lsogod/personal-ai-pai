const API_BASE = import.meta.env.VITE_API_BASE || "";

type ValidationDetailItem = {
  loc?: Array<string | number>;
  msg?: string;
  type?: string;
};

function translateField(field: string) {
  if (field === "email") return "邮箱";
  if (field === "password") return "密码";
  return field || "参数";
}

function translateDetailMessage(raw: string) {
  const text = (raw || "").toLowerCase();
  if (text.includes("value is not a valid email address")) return "请输入有效的邮箱地址。";
  if (text.includes("field required")) return "该字段不能为空。";
  if (text.includes("email already exists")) return "该邮箱已注册，请直接登录。";
  if (text.includes("invalid credentials")) return "邮箱或密码错误。";
  if (text.includes("invalid bind code format")) return "绑定码格式不正确。";
  return raw || "请求参数有误。";
}

function fallbackMessageByStatus(status: number) {
  if (status === 400) return "请求参数有误，请检查后重试。";
  if (status === 401) return "登录已失效或凭证错误，请重新登录。";
  if (status === 403) return "当前操作无权限。";
  if (status === 404) return "请求的资源不存在。";
  if (status === 409) return "数据冲突，请刷新后重试。";
  if (status === 422) return "输入格式不正确，请检查后重试。";
  if (status === 429) return "请求过于频繁，请稍后再试。";
  if (status >= 500) return "服务器暂时不可用，请稍后重试。";
  return `请求失败（${status}）`;
}

function normalizeBackendError(payload: unknown, status: number) {
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) {
      return translateDetailMessage(detail.trim());
    }
    if (Array.isArray(detail)) {
      const lines = detail
        .map((item) => {
          const row = item as ValidationDetailItem;
          const loc = Array.isArray(row.loc) ? row.loc.map(String) : [];
          const field = translateField(loc[loc.length - 1] || "");
          const msg = translateDetailMessage(row.msg || "");
          return `${field}：${msg}`;
        })
        .filter(Boolean);
      const unique = Array.from(new Set(lines));
      if (unique.length > 0) return unique.join("；");
    }
  }
  if (typeof payload === "string" && payload.trim()) {
    try {
      const parsed = JSON.parse(payload);
      return normalizeBackendError(parsed, status);
    } catch {
      return payload.trim();
    }
  }
  return fallbackMessageByStatus(status);
}

export interface SkillItem {
  slug: string;
  name: string;
  description: string;
  status: string;
  active_version: number;
  source: "builtin" | "user" | string;
  read_only: boolean;
}

export interface SkillDetail extends SkillItem {
  content_md?: string | null;
}

export interface SkillDraftPayload {
  request: string;
  skill_name?: string;
  skill_slug?: string;
}

export interface SkillDraftResponse {
  slug: string;
  version: number;
  status: string;
  content_md: string;
}

export interface SkillRawDraftPayload {
  content_md: string;
  skill_name?: string;
  skill_slug?: string;
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

export interface IdentityItem {
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

export interface ToolPolicyItem {
  source: string;
  name: string;
  description: string;
  enabled: boolean;
}

export interface SkillPolicyItem {
  source: string;
  slug: string;
  name: string;
  description: string;
  enabled: boolean;
}

export interface UserCustomization {
  user_id: number;
  tools: ToolPolicyItem[];
  skills: SkillPolicyItem[];
}

export interface AdminUser {
  id: number;
  uuid: string;
  nickname: string;
  ai_name: string;
  platform: string;
  platform_id: string;
  email?: string | null;
  setup_stage: number;
}

export async function apiRequest(
  path: string,
  options: RequestInit = {},
  token?: string | null
) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers ? (options.headers as Record<string, string>) : {})
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers
    });
  } catch {
    throw new Error("网络连接失败，请检查网络后重试。");
  }

  if (!res.ok) {
    const contentType = res.headers.get("content-type") || "";
    let payload: unknown = null;
    if (contentType.includes("application/json")) {
      payload = await res.json().catch(() => null);
    } else {
      payload = await res.text().catch(() => null);
    }
    throw new Error(normalizeBackendError(payload, res.status));
  }
  return res.json();
}

export async function adminRequest(
  path: string,
  options: RequestInit = {},
  adminToken?: string | null
) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers ? (options.headers as Record<string, string>) : {})
  };
  if (adminToken) {
    headers["X-Admin-Token"] = adminToken;
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers
    });
  } catch {
    throw new Error("网络连接失败，请检查网络后重试。");
  }

  if (!res.ok) {
    const contentType = res.headers.get("content-type") || "";
    let payload: unknown = null;
    if (contentType.includes("application/json")) {
      payload = await res.json().catch(() => null);
    } else {
      payload = await res.text().catch(() => null);
    }
    throw new Error(normalizeBackendError(payload, res.status));
  }
  return res.json();
}

export async function streamSsePost(
  path: string,
  payload: unknown,
  token: string | null | undefined,
  onChunk: (chunk: string) => void
) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json"
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });
  } catch {
    throw new Error("网络连接失败，请检查网络后重试。");
  }
  if (!res.ok) {
    const contentType = res.headers.get("content-type") || "";
    let body: unknown = null;
    if (contentType.includes("application/json")) {
      body = await res.json().catch(() => null);
    } else {
      body = await res.text().catch(() => null);
    }
    throw new Error(normalizeBackendError(body, res.status));
  }
  if (!res.body) {
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const event of events) {
      const lines = event.split("\n");
      for (const line of lines) {
        if (!line.startsWith("data: ")) {
          continue;
        }
        const chunk = line.slice(6);
        if (chunk === "[DONE]") {
          return;
        }
        onChunk(chunk);
      }
    }
  }
}

export function fetchSkills(token: string | null | undefined) {
  return apiRequest("/api/skills", {}, token) as Promise<SkillItem[]>;
}

export function fetchSkillDetail(
  slug: string,
  source: string,
  token: string | null | undefined
) {
  return apiRequest(
    `/api/skills/${slug}?source=${encodeURIComponent(source || "user")}`,
    {},
    token
  ) as Promise<SkillDetail>;
}

export function createSkillDraft(
  payload: SkillDraftPayload,
  token: string | null | undefined
) {
  return apiRequest(
    "/api/skills/draft",
    { method: "POST", body: JSON.stringify(payload) },
    token
  ) as Promise<SkillDraftResponse>;
}

export function publishSkill(slug: string, token: string | null | undefined) {
  return apiRequest(
    `/api/skills/${slug}/publish`,
    { method: "POST" },
    token
  ) as Promise<SkillItem>;
}

export function disableSkill(slug: string, token: string | null | undefined) {
  return apiRequest(
    `/api/skills/${slug}/disable`,
    { method: "POST" },
    token
  ) as Promise<SkillItem>;
}

export function fetchCustomization(token: string | null | undefined) {
  return apiRequest("/api/customization", {}, token) as Promise<UserCustomization>;
}

export function updateCustomizationToolPolicy(
  payload: { source: string; name: string; enabled: boolean },
  token: string | null | undefined
) {
  return apiRequest(
    "/api/customization/tool-policy",
    { method: "POST", body: JSON.stringify(payload) },
    token
  ) as Promise<UserCustomization>;
}

export function updateCustomizationSkillPolicy(
  payload: { source: string; slug: string; enabled: boolean },
  token: string | null | undefined
) {
  return apiRequest(
    "/api/customization/skill-policy",
    { method: "POST", body: JSON.stringify(payload) },
    token
  ) as Promise<UserCustomization>;
}

export function fetchAdminUsers(adminToken: string | null | undefined) {
  return adminRequest("/api/users", {}, adminToken) as Promise<AdminUser[]>;
}

export function fetchAdminCustomization(userId: number, adminToken: string | null | undefined) {
  return adminRequest(`/api/admin/customization/${userId}`, {}, adminToken) as Promise<UserCustomization>;
}

export function updateAdminToolPolicy(
  userId: number,
  payload: { source: string; name: string; enabled: boolean },
  adminToken: string | null | undefined
) {
  return adminRequest(
    `/api/admin/customization/${userId}/tool-policy`,
    { method: "POST", body: JSON.stringify(payload) },
    adminToken
  ) as Promise<UserCustomization>;
}

export function updateAdminSkillPolicy(
  userId: number,
  payload: { source: string; slug: string; enabled: boolean },
  adminToken: string | null | undefined
) {
  return adminRequest(
    `/api/admin/customization/${userId}/skill-policy`,
    { method: "POST", body: JSON.stringify(payload) },
    adminToken
  ) as Promise<UserCustomization>;
}

export function adminCreateUserSkillDraft(
  userId: number,
  payload: SkillDraftPayload,
  adminToken: string | null | undefined
) {
  return adminRequest(
    `/api/admin/users/${userId}/skills/draft`,
    { method: "POST", body: JSON.stringify(payload) },
    adminToken
  ) as Promise<SkillDraftResponse>;
}

export function adminGetUserSkillDetail(
  userId: number,
  slug: string,
  adminToken: string | null | undefined,
  source = "user"
) {
  return adminRequest(
    `/api/admin/users/${userId}/skills/${encodeURIComponent(slug)}?source=${encodeURIComponent(source)}`,
    {},
    adminToken
  ) as Promise<SkillDetail>;
}

export function adminSaveUserSkillRawDraft(
  userId: number,
  payload: SkillRawDraftPayload,
  adminToken: string | null | undefined
) {
  return adminRequest(
    `/api/admin/users/${userId}/skills/raw-draft`,
    { method: "POST", body: JSON.stringify(payload) },
    adminToken
  ) as Promise<SkillDraftResponse>;
}

export function adminPublishUserSkill(
  userId: number,
  slug: string,
  adminToken: string | null | undefined
) {
  return adminRequest(
    `/api/admin/users/${userId}/skills/${encodeURIComponent(slug)}/publish`,
    { method: "POST" },
    adminToken
  ) as Promise<SkillItem>;
}

export function adminDisableUserSkill(
  userId: number,
  slug: string,
  adminToken: string | null | undefined
) {
  return adminRequest(
    `/api/admin/users/${userId}/skills/${encodeURIComponent(slug)}/disable`,
    { method: "POST" },
    adminToken
  ) as Promise<SkillItem>;
}

export function fetchConversations(token: string | null | undefined) {
  return apiRequest("/api/conversations", {}, token) as Promise<ConversationItem[]>;
}

export function createConversation(
  payload: { title?: string },
  token: string | null | undefined
) {
  return apiRequest(
    "/api/conversations",
    { method: "POST", body: JSON.stringify(payload) },
    token
  ) as Promise<ConversationItem>;
}

export function switchConversation(conversationId: number, token: string | null | undefined) {
  return apiRequest(
    `/api/conversations/${conversationId}/switch`,
    { method: "POST" },
    token
  ) as Promise<ConversationItem>;
}

export function renameConversation(
  conversationId: number,
  title: string,
  token: string | null | undefined
) {
  return apiRequest(
    `/api/conversations/${conversationId}`,
    { method: "PATCH", body: JSON.stringify({ title }) },
    token
  ) as Promise<ConversationItem>;
}

export function deleteConversation(
  conversationId: number,
  token: string | null | undefined
) {
  return apiRequest(
    `/api/conversations/${conversationId}`,
    { method: "DELETE" },
    token
  ) as Promise<ConversationDeleteResponse>;
}

export function fetchLedgers(
  token: string | null | undefined,
  limit = 30
) {
  return apiRequest(`/api/ledgers?limit=${limit}`, {}, token) as Promise<LedgerItem[]>;
}

export function updateLedger(
  ledgerId: number,
  payload: { amount?: number; category?: string; item?: string },
  token: string | null | undefined
) {
  return apiRequest(
    `/api/ledgers/${ledgerId}`,
    { method: "PATCH", body: JSON.stringify(payload) },
    token
  ) as Promise<LedgerItem>;
}

export function deleteLedger(
  ledgerId: number,
  token: string | null | undefined
) {
  return apiRequest(
    `/api/ledgers/${ledgerId}`,
    { method: "DELETE" },
    token
  ) as Promise<{ ok: boolean; id: number }>;
}

export function fetchCalendar(
  token: string | null | undefined,
  startDate: string,
  endDate: string
) {
  return apiRequest(
    `/api/calendar?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`,
    {},
    token
  ) as Promise<CalendarResponse>;
}

export function fetchIdentities(token: string | null | undefined) {
  return apiRequest("/api/user/identities", {}, token) as Promise<IdentityItem[]>;
}

export function createBindCode(
  token: string | null | undefined,
  ttlMinutes = 10
) {
  return apiRequest(
    "/api/user/bind-code",
    { method: "POST", body: JSON.stringify({ ttl_minutes: ttlMinutes }) },
    token
  ) as Promise<BindCodeCreateResponse>;
}

export function consumeBindCode(
  token: string | null | undefined,
  code: string
) {
  return apiRequest(
    "/api/user/bind-consume",
    { method: "POST", body: JSON.stringify({ code }) },
    token
  ) as Promise<BindCodeConsumeResponse>;
}
