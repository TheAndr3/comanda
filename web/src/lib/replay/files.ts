import { readFile } from "node:fs/promises";
import path from "node:path";

import { parseReplayIndex, type ReplayEntry } from "./index";

/**
 * Where `comanda export-replay` writes (ADR-0012). Read on the server only: while `next build`
 * renders the static site, or by `next dev`. The browser never fetches the index.
 */
const REPLAYS_DIR = path.join(process.cwd(), "public", "replays");

export async function readReplayIndex(): Promise<ReplayEntry[]> {
  return parseReplayIndex(await readFile(path.join(REPLAYS_DIR, "index.json"), "utf8"));
}

/** The recorded trace of one replay, as exported: one JSON event per line. */
export async function readReplayTrace(sessionId: string): Promise<string> {
  return readFile(path.join(REPLAYS_DIR, `${sessionId}.jsonl`), "utf8");
}
