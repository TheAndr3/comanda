/**
 * The Replay: an Atendimento recorded earlier, played in the panel without calling any model
 * (ADR-0002). `comanda export-replay` copies the chosen traces to `public/replays/`, next to an
 * index with a short title and what each replay shows (ADR-0012).
 */

import { applyLine, emptyTraceView, type Step, type TraceView } from "../trace";

/** A recorded trace ready to play: one JSONL line per event, in trace order. */
export interface Replay {
  lines: string[];
  length: number;
}

export interface ChatLine {
  from: "customer" | "reception";
  text: string;
}

/** What the screen shows at one position of the playback. */
export interface ReplayFrame {
  view: TraceView;
  /** The conversation so far, as the customer saw it. */
  chat: ChatLine[];
  done: boolean;
}

export function loadReplay(jsonl: string): Replay {
  const lines = jsonl.split(/\r?\n/).filter((line) => line.trim() !== "");
  return { lines, length: lines.length };
}

/** The screen after the first `position` events, read by the same module as the live trace. */
export function replayAt(replay: Replay, position: number): ReplayFrame {
  const shown = Math.max(0, Math.min(position, replay.length));
  const view = replay.lines.slice(0, shown).reduce(applyLine, emptyTraceView);
  const chat = view.steps.flatMap((step): ChatLine[] =>
    step.kind === "message" && (step.from === "customer" || step.from === "reception")
      ? [{ from: step.from, text: step.text }]
      : [],
  );
  return { view, chat, done: shown === replay.length };
}

// Time on screen before each kind of step appears, in milliseconds. The recorded timestamps are
// not used: model latency would leave long gaps, and a scripted trace has none at all.
const PACE: Partial<Record<Step["kind"], number>> = {
  message: 1500,
  guardrail_blocked: 1300,
  delegation: 900,
  specialist_returned: 900,
  escalation: 900,
  tool: 700,
};
const BOOKKEEPING_PACE = 300;

/** How long the playback waits before showing the event at `position`. */
export function delayBefore(replay: Replay, position: number): number {
  const line = replay.lines[position];
  if (line === undefined) return BOOKKEEPING_PACE;
  const step = applyLine(emptyTraceView, line).steps[0];
  return (step && PACE[step.kind]) ?? BOOKKEEPING_PACE;
}

export interface ReplayEntry {
  sessionId: string;
  title: string;
  customerId: string | null;
  /** What the replay shows, in short Portuguese phrases, for the screen that lists replays. */
  shows: string[];
}

/** The index version this panel understands, written by `comanda export-replay`. */
export const INDEX_VERSION = 1;

function fail(reason: string): never {
  throw new Error(`Índice de replays inválido: ${reason}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function entry(value: unknown, position: number): ReplayEntry {
  if (!isRecord(value)) fail(`a entrada ${position} não é um objeto`);
  const { session_id, title, customer_id, shows } = value;
  if (typeof session_id !== "string" || !/^[A-Za-z0-9-]+$/.test(session_id)) {
    fail(`a entrada ${position} não tem session_id válido`);
  }
  if (typeof title !== "string") fail(`a entrada ${session_id} não tem título`);
  if (customer_id !== undefined && customer_id !== null && typeof customer_id !== "string") {
    fail(`o customer_id de ${session_id} não é texto`);
  }
  if (!Array.isArray(shows) || !shows.every((item) => typeof item === "string")) {
    fail(`shows de ${session_id} não é uma lista de textos`);
  }
  return { sessionId: session_id, title, customerId: customer_id ?? null, shows };
}

/**
 * Reads `public/replays/index.json`. Unlike the trace reader, this one throws: the index is
 * read while the static site is built, and a broken index must fail the build, not ship.
 */
export function parseReplayIndex(json: string): ReplayEntry[] {
  const parsed: unknown = JSON.parse(json);
  if (!isRecord(parsed)) fail("não é um objeto JSON");
  if (parsed.v !== INDEX_VERSION) fail(`versão ${String(parsed.v)}, este painel lê a ${INDEX_VERSION}`);
  if (!Array.isArray(parsed.replays)) fail("falta a lista replays");
  return parsed.replays.map(entry);
}
