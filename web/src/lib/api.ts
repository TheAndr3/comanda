/**
 * Client for the Comanda API (backend `comanda serve`, ADR-0005, ADR-0021).
 *
 * The browser talks to the API directly (ADR-0025): sending a message returns only the replies
 * to the customer, and every trace event arrives through the SSE stream at `eventsUrl`.
 */

// Read as a literal so Next.js inlines it at build time.
const configuredUrl = process.env.NEXT_PUBLIC_COMANDA_API_URL;

export const apiUrl = (configuredUrl && configuredUrl.trim() !== "" ? configuredUrl : "http://127.0.0.1:8000").replace(
  /\/+$/,
  "",
);

export interface CustomerOption {
  id: string;
  name: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** GET without a body; POST with `body` sent as JSON, or with no body when it is omitted. */
async function request<T>(path: string, method: "GET" | "POST" = "GET", body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  let response: Response;
  try {
    response = await fetch(`${apiUrl}${path}`, init);
  } catch {
    throw new ApiError(0, `Não consegui falar com a API em ${apiUrl}. Ela está rodando?`);
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : `A API respondeu ${response.status}.`;
    throw new ApiError(response.status, detail);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export function listCustomers(): Promise<CustomerOption[]> {
  return request("/customers");
}

export async function startSession(customerId: string): Promise<string> {
  const started = await request<{ session_id: string }>("/sessions", "POST", { customer_id: customerId });
  return started.session_id;
}

export async function sendMessage(sessionId: string, text: string): Promise<string[]> {
  const turn = await request<{ replies: string[] }>(`/sessions/${encodeURIComponent(sessionId)}/messages`, "POST", {
    text,
  });
  return turn.replies;
}

export function closeSession(sessionId: string): Promise<void> {
  return request(`/sessions/${encodeURIComponent(sessionId)}/close`, "POST");
}

export function eventsUrl(sessionId: string): string {
  return `${apiUrl}/sessions/${encodeURIComponent(sessionId)}/events`;
}
