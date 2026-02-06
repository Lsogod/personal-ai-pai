const API_BASE = import.meta.env.VITE_API_BASE || "";

export interface SkillItem {
  slug: string;
  name: string;
  description: string;
  status: string;
  active_version: number;
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

export interface ConversationItem {
  id: number;
  title: string;
  summary: string;
  last_message_at: string;
  active: boolean;
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

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `请求失败: ${res.status}`);
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

  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `请求失败: ${res.status}`);
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

export function fetchSkillDetail(slug: string, token: string | null | undefined) {
  return apiRequest(`/api/skills/${slug}`, {}, token) as Promise<SkillDetail>;
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
