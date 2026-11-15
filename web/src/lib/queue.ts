/**
 * Client for the Fila: the pending refunds and open tickets waiting for an Atendente humano
 * (ADR-0007). The panel decides through the same API routes the CLI's functions sit behind.
 *
 * The amount comes from the rule and is never edited (ADR-0001): nothing here sends one.
 */

import { request, requestText } from "./api";

export interface PendingRefund {
  id: string;
  orderId: string;
  customerId: string;
  /** Integer cents, as computed by the rule. */
  amountCents: number;
  issue: string;
  ruleId: string;
  createdAt: string;
  /** The Atendimento that issued the refund; null when it was created outside one. */
  sessionId: string | null;
}

export interface OpenTicket {
  id: string;
  customerId: string;
  orderId: string | null;
  priority: string;
  summary: string;
  createdAt: string;
  sessionId: string | null;
}

export interface Queue {
  pendingRefunds: PendingRefund[];
  openTickets: OpenTicket[];
}

interface ApiRefund {
  id: string;
  order_id: string;
  customer_id: string;
  amount_cents: number;
  issue: string;
  rule_id: string;
  created_at: string;
  session_id: string | null;
}

interface ApiTicket {
  id: string;
  customer_id: string;
  order_id: string | null;
  priority: string;
  summary: string;
  created_at: string;
  session_id: string | null;
}

export async function fetchQueue(): Promise<Queue> {
  const queue = await request<{ pending_refunds: ApiRefund[]; open_tickets: ApiTicket[] }>("/queue");
  return {
    pendingRefunds: queue.pending_refunds.map((refund) => ({
      id: refund.id,
      orderId: refund.order_id,
      customerId: refund.customer_id,
      amountCents: refund.amount_cents,
      issue: refund.issue,
      ruleId: refund.rule_id,
      createdAt: refund.created_at,
      sessionId: refund.session_id ?? null,
    })),
    openTickets: queue.open_tickets.map((ticket) => ({
      id: ticket.id,
      customerId: ticket.customer_id,
      orderId: ticket.order_id,
      priority: ticket.priority,
      summary: ticket.summary,
      createdAt: ticket.created_at,
      sessionId: ticket.session_id ?? null,
    })),
  };
}

/** A decision the panel refuses to send: the message is for the Atendente humano. */
export class DecisionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DecisionError";
  }
}

/** Why an approval cannot be sent yet, or null when it can. */
export function approvalProblem(decidedBy: string): string | null {
  return decidedBy.trim() === "" ? "Informe quem decide." : null;
}

/** Why a rejection reason is not enough, or null when it is: blank or only spaces is no reason. */
export function reasonProblem(reason: string): string | null {
  return reason.trim() === "" ? "Informe o motivo da recusa." : null;
}

/** Why a rejection cannot be sent yet, or null when it can: the reason is mandatory. */
export function rejectionProblem(decidedBy: string, reason: string): string | null {
  return approvalProblem(decidedBy) ?? reasonProblem(reason);
}

function refundPath(refundId: string, action: "approve" | "reject"): string {
  return `/queue/refunds/${encodeURIComponent(refundId)}/${action}`;
}

export async function approveRefund(refundId: string, decidedBy: string): Promise<void> {
  const problem = approvalProblem(decidedBy);
  if (problem) throw new DecisionError(problem);
  await request(refundPath(refundId, "approve"), "POST", { decided_by: decidedBy.trim() });
}

export async function resolveTicket(ticketId: string): Promise<void> {
  await request(`/queue/tickets/${encodeURIComponent(ticketId)}/resolve`, "POST");
}

/**
 * The recorded trace of an Atendimento, live or closed, as JSONL lines for `readTrace`. The
 * SSE only serves Atendimentos the API still holds; this reads the file the backend wrote.
 */
export function fetchRecordedTrace(sessionId: string): Promise<string> {
  return requestText(`/sessions/${encodeURIComponent(sessionId)}/trace`);
}

export async function rejectRefund(refundId: string, decidedBy: string, reason: string): Promise<void> {
  const problem = rejectionProblem(decidedBy, reason);
  if (problem) throw new DecisionError(problem);
  await request(refundPath(refundId, "reject"), "POST", { decided_by: decidedBy.trim(), reason: reason.trim() });
}
