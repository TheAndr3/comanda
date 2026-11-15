"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { delayBefore, loadReplay, replayAt, type ReplayEntry } from "@/lib/replay";

import panel from "./panel.module.css";
import styles from "./replay.module.css";
import { TraceSection } from "./trace-panel";

/**
 * Plays one recorded Atendimento: the conversation on the left, the trace on the right, both
 * advancing one event at a time. Everything comes from the exported JSONL; nothing is called.
 */
export function ReplayPlayer({ entry, trace }: { entry: ReplayEntry; trace: string }) {
  const replay = useMemo(() => loadReplay(trace), [trace]);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(true);
  const frame = useMemo(() => replayAt(replay, position), [replay, position]);

  useEffect(() => {
    if (!playing || frame.done) return;
    const timer = setTimeout(() => setPosition((current) => current + 1), delayBefore(replay, position));
    return () => clearTimeout(timer);
  }, [playing, frame.done, replay, position]);

  const log = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [frame.chat.length]);

  const status = frame.done ? "ended" : playing ? "live" : "paused";
  const statusLabel = frame.done ? "Fim do replay" : playing ? "Reproduzindo" : "Pausado";

  return (
    <div className={panel.shell}>
      <header className={panel.topbar}>
        <h1 className={panel.brand}>
          Comanda <span>Replay</span>
        </h1>
        <div className={`${panel.controls} ${styles.playerControls}`}>
          <Link href="/replay" className={styles.back}>
            Todos os replays
          </Link>
          <button
            type="button"
            className={panel.primary}
            onClick={() => {
              if (frame.done) setPosition(0);
              setPlaying(frame.done || !playing);
            }}
          >
            {frame.done ? "Assistir de novo" : playing ? "Pausar" : "Continuar"}
          </button>
          <button
            type="button"
            onClick={() => {
              setPlaying(false);
              setPosition((current) => Math.min(current + 1, replay.length));
            }}
            disabled={frame.done}
          >
            Próximo passo
          </button>
          <button
            type="button"
            onClick={() => {
              setPlaying(false);
              setPosition(replay.length);
            }}
            disabled={frame.done}
          >
            Ver tudo
          </button>
          <p className={styles.progress} aria-live="off">
            passo {position} de {replay.length}
          </p>
        </div>
      </header>

      <section className={styles.about} aria-label="Sobre este replay">
        <h2>{entry.title}</h2>
        <p>
          Atendimento gravado {entry.customerId && <>do cliente {entry.customerId} </>}e reproduzido sem chamar nenhum
          modelo. <a href={`/replays/${entry.sessionId}.jsonl`}>Baixar o Rastro (JSONL)</a>
        </p>
      </section>

      <main className={panel.columns}>
        <section className={panel.chat} aria-labelledby="chat-title">
          <header className={panel.chatHeader}>
            <h2 id="chat-title">Atendimento</h2>
            <p>
              <code>{entry.sessionId}</code>
            </p>
          </header>
          <ol ref={log} className={panel.messages} role="log" aria-label="Conversa com a Recepção" tabIndex={0}>
            {frame.chat.length === 0 && <li className={panel.empty}>O cliente vai escrever em instantes…</li>}
            {frame.chat.map((line, index) => (
              <li key={index} className={panel.message} data-from={line.from}>
                <span className={panel.author}>{line.from === "customer" ? "Cliente" : "Recepção"}</span>
                <p>{line.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <TraceSection view={frame.view} status={status} label={statusLabel} />
      </main>
    </div>
  );
}
