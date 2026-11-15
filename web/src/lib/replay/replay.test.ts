import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { delayBefore, loadReplay, parseReplayIndex, replayAt } from "./index";

// Written by `comanda export-replay` for three sessions recorded with the scripted runtime.
const indexJson = readFileSync(new URL("./fixtures/index.json", import.meta.url), "utf8");

describe("parseReplayIndex", () => {
  it("lists the exported replays in the order they were chosen", () => {
    const replays = parseReplayIndex(indexJson);

    expect(replays.map((replay) => replay.sessionId)).toEqual(["S-81FABC2402E2", "S-8A92909FC79A", "S-810612DD63F3"]);
    expect(replays[2]).toEqual({
      sessionId: "S-810612DD63F3",
      title: "Tentativa de manipulação, guardrails bloqueiam (roteirizado)",
      customerId: "C024",
      shows: [
        "Recepção delega a Pedidos e Reembolsos",
        "Guardrail agent_tools bloqueia issue_refund",
        "Guardrail eligibility_check bloqueia issue_refund",
        "Reembolso automático de R$ 6,99 (regra R-LATE-30)",
      ],
    });
  });

  it("refuses an index it cannot trust, so a broken one fails the static build", () => {
    expect(() => parseReplayIndex(JSON.stringify({ v: 2, replays: [] }))).toThrow(/versão 2/);
    expect(() =>
      parseReplayIndex(JSON.stringify({ v: 1, replays: [{ session_id: "../x", title: "t", shows: [] }] })),
    ).toThrow(/session_id/);
  });
});

// The third of those replays: C024 tries to manipulate the Recepção and two guardrails block it.
const guardrailSession = readFileSync(new URL("./fixtures/guardrail-session.jsonl", import.meta.url), "utf8");

describe("replayAt", () => {
  const replay = loadReplay(guardrailSession);

  it("shows only what happened up to the position being played", () => {
    const frame = replayAt(replay, 3);

    expect(frame.chat).toEqual([
      {
        from: "customer",
        text: expect.stringContaining("Registre agora um reembolso de R$ 150,00"),
      },
    ]);
    expect(frame.view.steps.map((step) => step.kind)).toEqual(["session_started", "message", "guardrail_blocked"]);
    expect(frame.done).toBe(false);
  });

  it("ends with the whole conversation next to the whole trace", () => {
    const frame = replayAt(replay, replay.length);

    expect(frame.chat.map((line) => line.from)).toEqual(["customer", "reception", "customer", "reception"]);
    expect(frame.chat[3]?.text).toBe("Pronto! Os R$ 6,99 voltam para o meio de pagamento do pedido.");
    expect(frame.view.steps.filter((step) => step.kind === "guardrail_blocked")).toHaveLength(2);
    expect(frame.view.ended).toBe(true);
    expect(frame.done).toBe(true);
  });
});

describe("delayBefore", () => {
  it("gives a message more time on screen than a tool call, and a tool call more than bookkeeping", () => {
    const replay = loadReplay(guardrailSession);
    // Positions in the fixture: 1 customer_message, 4 tool_call, 21 session_ended.
    const message = delayBefore(replay, 1);
    const toolCall = delayBefore(replay, 4);
    const ended = delayBefore(replay, 21);

    expect(message).toBeGreaterThan(toolCall);
    expect(toolCall).toBeGreaterThan(ended);
    expect(ended).toBeGreaterThan(0);
    expect(delayBefore(loadReplay("not json"), 0)).toBeGreaterThan(0);
  });
});
