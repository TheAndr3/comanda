import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import {
  approveRefund,
  DecisionError,
  fetchQueue,
  fetchRecordedTrace,
  rejectRefund,
  resolveTicket,
} from "./queue";

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** Stands in for the API: records each call and answers with the next canned response. */
function fakeApi(...responses: Response[]): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    calls.push({
      url,
      method: init.method ?? "GET",
      body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
    });
    const next = responses.shift();
    if (!next) throw new Error(`unexpected call to ${url}`);
    return next;
  });
  return calls;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// As `GET /queue` returns it: a pending refund from an Atendimento and a ticket opened by the CLI.
const apiQueue = {
  pending_refunds: [
    {
      id: "RF-1A2B3C4D",
      order_id: "O-10004",
      customer_id: "C003",
      amount_cents: 10979,
      issue: "not_delivered",
      status: "pending_approval",
      rule_id: "R-LOST",
      item_ids: [],
      created_at: "2026-11-10T20:30:00+00:00",
      decided_by: null,
      decided_at: null,
      rejection_reason: null,
      session_id: "S-3C51213C0DA9",
    },
  ],
  open_tickets: [
    {
      id: "TK-9F8E7D6C",
      customer_id: "C006",
      order_id: null,
      priority: "alta",
      summary: "Quer falar com um humano.",
      created_at: "2026-11-10T20:31:00+00:00",
      status: "open",
      session_id: null,
    },
  ],
};

describe("fetchQueue", () => {
  it("reads pending refunds and open tickets with the Atendimento they came from", async () => {
    const calls = fakeApi(json(apiQueue));

    const queue = await fetchQueue();

    expect(calls).toEqual([{ url: "http://127.0.0.1:8000/queue", method: "GET", body: undefined }]);
    expect(queue.pendingRefunds).toEqual([
      {
        id: "RF-1A2B3C4D",
        orderId: "O-10004",
        customerId: "C003",
        amountCents: 10979,
        issue: "not_delivered",
        ruleId: "R-LOST",
        createdAt: "2026-11-10T20:30:00+00:00",
        sessionId: "S-3C51213C0DA9",
      },
    ]);
    expect(queue.openTickets).toEqual([
      {
        id: "TK-9F8E7D6C",
        customerId: "C006",
        orderId: null,
        priority: "alta",
        summary: "Quer falar com um humano.",
        createdAt: "2026-11-10T20:31:00+00:00",
        sessionId: null,
      },
    ]);
  });

  it("surfaces the API's message when the request fails", async () => {
    fakeApi(json({ detail: "Banco indisponível." }, 500));

    await expect(fetchQueue()).rejects.toEqual(new ApiError(500, "Banco indisponível."));
  });
});

describe("rejectRefund", () => {
  it("cannot reject without a reason, and never reaches the API", async () => {
    const calls = fakeApi();

    await expect(rejectRefund("RF-1A2B3C4D", "Marina", "   ")).rejects.toEqual(
      new DecisionError("Informe o motivo da recusa."),
    );
    expect(calls).toEqual([]);
  });

  it("sends who decided and the reason, trimmed, and nothing about the amount", async () => {
    const calls = fakeApi(json({ id: "RF-1A2B3C4D", status: "rejected" }));

    await rejectRefund("RF-1A2B3C4D", " Marina ", "  Entrega confirmada pelo entregador. ");

    expect(calls).toEqual([
      {
        url: "http://127.0.0.1:8000/queue/refunds/RF-1A2B3C4D/reject",
        method: "POST",
        body: { decided_by: "Marina", reason: "Entrega confirmada pelo entregador." },
      },
    ]);
  });
});

describe("approveRefund", () => {
  it("sends who decided, and only that", async () => {
    const calls = fakeApi(json({ id: "RF-1A2B3C4D", status: "approved" }));

    await approveRefund("RF-1A2B3C4D", "Marina");

    expect(calls).toEqual([
      { url: "http://127.0.0.1:8000/queue/refunds/RF-1A2B3C4D/approve", method: "POST", body: { decided_by: "Marina" } },
    ]);
  });

  it("cannot approve without saying who decides", async () => {
    const calls = fakeApi();

    await expect(approveRefund("RF-1A2B3C4D", " ")).rejects.toEqual(new DecisionError("Informe quem decide."));
    expect(calls).toEqual([]);
  });

  it("reports a refund someone else already decided", async () => {
    fakeApi(json({ detail: "Reembolso RF-1A2B3C4D já foi decidido." }, 409));

    await expect(approveRefund("RF-1A2B3C4D", "Marina")).rejects.toEqual(
      new ApiError(409, "Reembolso RF-1A2B3C4D já foi decidido."),
    );
  });
});

describe("resolveTicket", () => {
  it("marks the ticket as resolved", async () => {
    const calls = fakeApi(json({ id: "TK-9F8E7D6C", status: "resolved" }));

    await resolveTicket("TK-9F8E7D6C");

    expect(calls).toEqual([
      { url: "http://127.0.0.1:8000/queue/tickets/TK-9F8E7D6C/resolve", method: "POST", body: undefined },
    ]);
  });
});

describe("fetchRecordedTrace", () => {
  it("reads the recorded trace of an Atendimento as the backend wrote it", async () => {
    const jsonl = '{"v": 1, "seq": 0}\r\n{"v": 1, "seq": 1}\r\n';
    const calls = fakeApi(new Response(jsonl, { headers: { "Content-Type": "application/x-ndjson" } }));

    expect(await fetchRecordedTrace("S-3C51213C0DA9")).toBe(jsonl);
    expect(calls).toEqual([
      { url: "http://127.0.0.1:8000/sessions/S-3C51213C0DA9/trace", method: "GET", body: undefined },
    ]);
  });
});
