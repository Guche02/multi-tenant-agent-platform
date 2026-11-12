// The backend API. Requests go to /api/* on this origin (next.config.ts passes them to FastAPI),
// with the httpOnly session cookies. On a 401 the client refreshes the session once and retries.

import type { BookingDetails, Conversation, Org, OrderDetails, OrgSummary, StoredMessage, User } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return String(body.detail[0].msg).replace(/^Value error, /, "");
  } catch {
    // not JSON
  }
  return res.status >= 500 ? "The server had a problem. Please try again." : `Request failed (${res.status}).`;
}

let refreshing: Promise<boolean> | null = null;

/** One refresh at a time, shared by every request that got a 401 at once. */
function refreshSession(): Promise<boolean> {
  refreshing ??= fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => setTimeout(() => (refreshing = null), 0));
  return refreshing;
}

export async function request(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    ...init,
    headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  if (res.status === 401 && retry && !path.startsWith("/auth/") && (await refreshSession())) {
    return request(path, init, false);
  }
  return res;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await request(path, init);
  if (!res.ok) throw new ApiError(res.status, await errorText(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const body = (data: unknown): RequestInit => ({ body: JSON.stringify(data) });

export const api = {
  orgs: () => json<OrgSummary[]>("/orgs"),
  org: (org: string) => json<Org>(`/orgs/${org}`),
  me: (org: string) => json<{ user: User }>(`/orgs/${org}/me`),
  login: (org: string, email: string, password: string) =>
    json<{ user: User }>(`/orgs/${org}/auth/login`, { method: "POST", ...body({ email, password }) }),
  signup: (org: string, email: string, password: string) =>
    json<{ user: User }>(`/orgs/${org}/auth/signup`, { method: "POST", ...body({ email, password }) }),
  logout: () => json<void>("/auth/logout", { method: "POST" }),
  conversations: (org: string) => json<Conversation[]>(`/orgs/${org}/conversations`),
  messages: (org: string, id: string) => json<StoredMessage[]>(`/orgs/${org}/conversations/${id}/messages`),
  rename: (org: string, id: string, title: string) =>
    json<Conversation>(`/orgs/${org}/conversations/${id}`, { method: "PATCH", ...body({ title }) }),
  remove: (org: string, id: string) => json<void>(`/orgs/${org}/conversations/${id}`, { method: "DELETE" }),
  orders: (org: string) => json<{ currency: string; orders: OrderDetails[] }>(`/orgs/${org}/orders`),
  bookings: (org: string) =>
    json<{ currency: string; timezone: string; upcoming: BookingDetails[]; past_and_cancelled: BookingDetails[] }>(
      `/orgs/${org}/bookings`,
    ),
};

export type ChatEventHandler = (event: string, data: any) => void;

/**
 * Sends a chat message and calls `onEvent` for each Server-Sent Event of the reply:
 * conversation, tool, tool_result, token, done, error. Abort `signal` to stop the reply.
 * (EventSource can't POST, so this reads the response stream itself.)
 */
export async function streamChat(
  org: string,
  message: string,
  conversationId: string | null,
  signal: AbortSignal,
  onEvent: ChatEventHandler,
): Promise<void> {
  const res = await request(`/orgs/${org}/chat`, {
    method: "POST",
    signal,
    ...body(conversationId ? { message, conversation_id: conversationId } : { message }),
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, await errorText(res));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let end: number;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let name = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent(name, JSON.parse(data.join("\n")));
    }
  }
}
