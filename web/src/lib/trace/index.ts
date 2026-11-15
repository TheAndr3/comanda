/**
 * Reading the Rastro (trace): the ordered record of every step of an Atendimento (ADR-0010).
 *
 * The backend writes one JSON event per line, with a schema version in `v`. The same line is
 * the `data` of each SSE event (ADR-0021) and of each line of a replay file (ADR-0012), so the
 * panel reads all three through this module. Reading never throws: a line the panel cannot
 * interpret becomes a step that says so, and the rest of the trace keeps flowing.
 */

/** The schema version this panel understands. */
export const SCHEMA_VERSION = 1;

export type Agent = "reception" | "orders" | "refunds";
export type Specialist = Exclude<Agent, "reception">;
export type Actor = Agent | "customer" | "system";

interface StepBase {
  seq: number;
  ts: string;
}

export type Step =
  | (StepBase & { kind: "session_started"; customerId: string })
  | (StepBase & { kind: "message"; from: Actor; text: string })
  | (StepBase & { kind: "delegation"; specialist: string; task: string })
  | (StepBase & { kind: "specialist_returned"; specialist: string; result: string })
  | (StepBase & {
      kind: "tool";
      agent: Actor;
      callId: string;
      tool: string;
      args: unknown;
      status: "running" | "ok" | "error";
      result?: unknown;
    })
  | (StepBase & {
      kind: "guardrail_blocked";
      agent: Actor;
      guardrail: string;
      tool: string;
      args: unknown;
      reason: string;
    })
  | (StepBase & {
      kind: "escalation";
      agent: Actor;
      ticketId: string;
      priority: string;
      orderId: string | null;
    })
  | (StepBase & {
      kind: "usage";
      agent: Actor;
      model: string;
      inputTokens: number;
      outputTokens: number;
      cacheReadTokens: number;
      cacheWriteTokens: number;
      costUsd: number | null;
    })
  | (StepBase & {
      kind: "quota";
      window: string;
      status: string;
      utilization: number | null;
      resetsAt: number | null;
    })
  | (StepBase & { kind: "session_ended" })
  // A v1 event type added after this panel was written: shown as it came.
  | (StepBase & { kind: "other"; type: string; agent: Actor; data: Record<string, unknown> })
  // An event written with a schema this panel does not know: shown, never interpreted.
  | { kind: "unsupported_version"; seq: number | null; version: unknown; raw: string }
  // A line that is not valid JSON or misses a field its type requires.
  | { kind: "invalid"; reason: string; raw: string };

export interface ModelUsage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  costUsd: number;
}

/**
 * Tokens per model and the API-equivalent cost, an estimate in dollars (ADR-0014). A call
 * whose cost is unknown still counts its tokens, stays out of `costUsd` and marks its model
 * in `unpricedModels`, like the CLI summary.
 */
export interface UsageTotals {
  byModel: Record<string, ModelUsage>;
  costUsd: number;
  unpricedModels: string[];
}

export interface TraceView {
  steps: Step[];
  usage: UsageTotals;
  ended: boolean;
  /** Position of the last event read; an event at or before it was already read. */
  lastSeq: number;
}

export const emptyTraceView: TraceView = {
  steps: [],
  usage: { byModel: {}, costUsd: 0, unpricedModels: [] },
  ended: false,
  lastSeq: -1,
};

function addUsage(totals: UsageTotals, step: Extract<Step, { kind: "usage" }>): UsageTotals {
  const previous = totals.byModel[step.model] ?? {
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheWriteTokens: 0,
    costUsd: 0,
  };
  const cost = step.costUsd ?? 0;
  const unpriced =
    step.costUsd === null && !totals.unpricedModels.includes(step.model)
      ? [...totals.unpricedModels, step.model].sort()
      : totals.unpricedModels;
  return {
    byModel: {
      ...totals.byModel,
      [step.model]: {
        inputTokens: previous.inputTokens + step.inputTokens,
        outputTokens: previous.outputTokens + step.outputTokens,
        cacheReadTokens: previous.cacheReadTokens + step.cacheReadTokens,
        cacheWriteTokens: previous.cacheWriteTokens + step.cacheWriteTokens,
        costUsd: previous.costUsd + cost,
      },
    },
    costUsd: totals.costUsd + cost,
    unpricedModels: unpriced,
  };
}

interface RawEvent {
  seq: number;
  ts: string;
  agent: Actor;
  type: string;
  data: Record<string, unknown>;
}

class LineError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(data: Record<string, unknown>, key: string): string {
  const value = data[key];
  if (typeof value !== "string") throw new LineError(`campo ${key} ausente ou não é texto`);
  return value;
}

function count(data: Record<string, unknown>, key: string): number {
  const value = data[key] ?? 0;
  if (typeof value !== "number") throw new LineError(`campo ${key} não é número`);
  return value;
}

function optionalNumber(data: Record<string, unknown>, key: string): number | null {
  const value = data[key] ?? null;
  if (value !== null && typeof value !== "number") throw new LineError(`campo ${key} não é número`);
  return value;
}

function optionalText(data: Record<string, unknown>, key: string): string | null {
  const value = data[key] ?? null;
  if (value !== null && typeof value !== "string") throw new LineError(`campo ${key} não é texto`);
  return value;
}

type Envelope = RawEvent | Extract<Step, { kind: "unsupported_version" }>;

function parseEnvelope(line: string): Envelope {
  const parsed: unknown = JSON.parse(line);
  if (!isRecord(parsed)) throw new LineError("a linha não é um objeto JSON");
  const { v, seq, ts, agent, type, data } = parsed;
  if (v !== SCHEMA_VERSION) {
    return { kind: "unsupported_version", seq: typeof seq === "number" ? seq : null, version: v, raw: line };
  }
  if (typeof seq !== "number" || typeof ts !== "string" || typeof agent !== "string") {
    throw new LineError("seq, ts ou agent ausente");
  }
  if (typeof type !== "string" || !isRecord(data)) throw new LineError("type ou data ausente");
  return { seq, ts, agent: agent as Actor, type, data };
}

function applyEvent(view: TraceView, event: RawEvent): TraceView {
  const { seq, ts, agent, data } = event;
  const base = { seq, ts };
  const add = (step: Step): TraceView => ({ ...view, steps: [...view.steps, step] });

  switch (event.type) {
    case "session_started":
      return add({ ...base, kind: "session_started", customerId: text(data, "customer_id") });
    case "customer_message":
    case "agent_message":
      return add({ ...base, kind: "message", from: agent, text: text(data, "text") });
    case "delegation":
      return add({
        ...base,
        kind: "delegation",
        specialist: text(data, "specialist"),
        task: text(data, "task"),
      });
    case "specialist_returned":
      return add({ ...base, kind: "specialist_returned", specialist: agent, result: text(data, "result") });
    case "tool_call":
      return add({
        ...base,
        kind: "tool",
        agent,
        callId: text(data, "call_id"),
        tool: text(data, "tool"),
        args: data.args,
        status: "running",
      });
    case "tool_result": {
      const callId = text(data, "call_id");
      const status = data.is_error === true ? "error" : "ok";
      return {
        ...view,
        steps: view.steps.map((step) =>
          step.kind === "tool" && step.callId === callId ? { ...step, status, result: data.result } : step,
        ),
      };
    }
    case "guardrail_blocked":
      return add({
        ...base,
        kind: "guardrail_blocked",
        agent,
        guardrail: text(data, "guardrail"),
        tool: text(data, "tool"),
        args: data.args,
        reason: text(data, "reason"),
      });
    case "escalation":
      return add({
        ...base,
        kind: "escalation",
        agent,
        ticketId: text(data, "ticket_id"),
        priority: text(data, "priority"),
        orderId: optionalText(data, "order_id"),
      });
    case "usage": {
      const step: Extract<Step, { kind: "usage" }> = {
        ...base,
        kind: "usage",
        agent,
        model: text(data, "model"),
        inputTokens: count(data, "input_tokens"),
        outputTokens: count(data, "output_tokens"),
        cacheReadTokens: count(data, "cache_read_tokens"),
        cacheWriteTokens: count(data, "cache_write_tokens"),
        costUsd: optionalNumber(data, "cost_usd"),
      };
      return { ...add(step), usage: addUsage(view.usage, step) };
    }
    case "quota":
      return add({
        ...base,
        kind: "quota",
        window: text(data, "window"),
        status: text(data, "status"),
        utilization: optionalNumber(data, "utilization"),
        resetsAt: optionalNumber(data, "resets_at"),
      });
    case "session_ended":
      return { ...add({ ...base, kind: "session_ended" }), ended: true };
    default:
      return add({ ...base, kind: "other", type: event.type, agent, data });
  }
}

/** Folds one trace line (a JSONL line or an SSE `data`) into the view. */
export function applyLine(view: TraceView, line: string): TraceView {
  try {
    const envelope = parseEnvelope(line);
    // Events arrive in trace order, so a position already read means a repeat.
    if (envelope.seq !== null && envelope.seq <= view.lastSeq) return view;
    const lastSeq = envelope.seq ?? view.lastSeq;
    if ("kind" in envelope) return { ...view, steps: [...view.steps, envelope], lastSeq };
    return { ...applyEvent(view, envelope), lastSeq };
  } catch (error) {
    if (!(error instanceof LineError || error instanceof SyntaxError)) throw error;
    return { ...view, steps: [...view.steps, { kind: "invalid", reason: error.message, raw: line }] };
  }
}

/** Reads a whole JSONL trace, as written by the backend or exported for the replay. */
export function readTrace(jsonl: string): TraceView {
  return jsonl
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "")
    .reduce(applyLine, emptyTraceView);
}
