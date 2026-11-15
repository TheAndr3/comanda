import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { applyLine, emptyTraceView, readTrace } from "./index";

// A real trace written by the backend with the scripted runtime: C002 reports a missing soda,
// the refund is blocked once by the eligibility guardrail, then issued; the case is escalated.
const sample = readFileSync(new URL("./fixtures/missing-item-session.jsonl", import.meta.url), "utf8");

describe("readTrace", () => {
  it("turns a recorded session into steps in trace order", () => {
    const view = readTrace(sample);

    expect(view.steps.map((step) => step.kind)).toEqual([
      "session_started",
      "message",
      "delegation",
      "tool",
      "tool",
      "usage",
      "specialist_returned",
      "delegation",
      "guardrail_blocked",
      "tool",
      "usage",
      "specialist_returned",
      "usage",
      "quota",
      "message",
      "message",
      "delegation",
      "tool",
      "specialist_returned",
      "usage",
      "message",
      "message",
      "guardrail_blocked",
      "tool",
      "escalation",
      "message",
      "session_ended",
    ]);
    expect(view.ended).toBe(true);
  });

  it("adds up tokens and cost per model, leaving unknown costs out of the total", () => {
    const { usage } = readTrace(sample);

    // Worked out by hand from the sample: two sonnet calls (one without a known cost) and two haiku calls.
    expect(usage.byModel.sonnet).toEqual({
      inputTokens: 4300,
      outputTokens: 280,
      cacheReadTokens: 1800,
      cacheWriteTokens: 0,
      costUsd: expect.closeTo(0.0096, 6),
    });
    expect(usage.byModel.haiku).toEqual({
      inputTokens: 2700,
      outputTokens: 140,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
      costUsd: expect.closeTo(0.0034, 6),
    });
    expect(usage.costUsd).toBeCloseTo(0.013, 6);
    expect(usage.unpricedModels).toEqual(["sonnet"]);
  });
});

interface LineFields {
  seq: number;
  type: string;
  agent?: string;
  data?: Record<string, unknown>;
  v?: unknown;
}

function line({ seq, type, agent = "system", data = {}, v = 1 }: LineFields): string {
  return JSON.stringify({ v, session_id: "S-TEST", seq, ts: "2026-11-12T00:00:00+00:00", agent, type, data });
}

function fold(...lines: string[]) {
  return lines.reduce(applyLine, emptyTraceView);
}

describe("applyLine", () => {
  const call = line({
    seq: 3,
    type: "tool_call",
    agent: "orders",
    data: { call_id: "call-1", tool: "track_delivery", args: { order_id: "O-10001" } },
  });

  it("shows a tool as running until its result arrives", () => {
    expect(fold(call).steps).toEqual([
      expect.objectContaining({ kind: "tool", tool: "track_delivery", status: "running" }),
    ]);

    const result = line({
      seq: 4,
      type: "tool_result",
      agent: "orders",
      data: { call_id: "call-1", tool: "track_delivery", result: { error: "Pedido não encontrado." }, is_error: true },
    });
    expect(fold(call, result).steps).toEqual([
      expect.objectContaining({
        kind: "tool",
        agent: "orders",
        args: { order_id: "O-10001" },
        status: "error",
        result: { error: "Pedido não encontrado." },
      }),
    ]);
  });

  it("does not interpret an event from an unknown schema version and keeps reading", () => {
    const future = line({ seq: 1, v: 2, type: "customer_message", agent: "customer", data: { body: "Oi" } });
    const view = fold(
      line({ seq: 0, type: "session_started", data: { customer_id: "C001" } }),
      future,
      line({ seq: 2, type: "agent_message", agent: "reception", data: { text: "Olá!" } }),
    );

    expect(view.steps).toEqual([
      expect.objectContaining({ kind: "session_started", customerId: "C001" }),
      { kind: "unsupported_version", seq: 1, version: 2, raw: future },
      expect.objectContaining({ kind: "message", from: "reception", text: "Olá!" }),
    ]);
  });

  it("reads an event delivered twice only once", () => {
    const started = line({ seq: 0, type: "session_started", data: { customer_id: "C001" } });
    const usage = line({
      seq: 1,
      type: "usage",
      agent: "reception",
      data: { model: "haiku", input_tokens: 100, output_tokens: 10, cost_usd: 0.001 },
    });

    const view = fold(started, usage, started, usage);

    expect(view.steps.map((step) => step.kind)).toEqual(["session_started", "usage"]);
    expect(view.usage.byModel.haiku?.inputTokens).toBe(100);
  });

  it("shows an event type it does not know yet without hiding it", () => {
    const view = fold(line({ seq: 0, type: "handoff_note", agent: "orders", data: { note: "x" } }));

    expect(view.steps).toEqual([
      expect.objectContaining({ kind: "other", type: "handoff_note", agent: "orders", data: { note: "x" } }),
    ]);
  });

  it("turns a line it cannot read into an invalid step instead of throwing", () => {
    const truncated = '{"v": 1, "seq": 5, "type": "tool_c';
    const missingField = line({ seq: 6, type: "delegation", agent: "reception", data: { specialist: "orders" } });

    const view = fold(truncated, missingField);

    expect(view.steps).toEqual([
      expect.objectContaining({ kind: "invalid", raw: truncated }),
      expect.objectContaining({ kind: "invalid", raw: missingField, reason: expect.stringContaining("task") }),
    ]);
  });
});
