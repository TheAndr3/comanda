"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { fetchRecordedTrace } from "@/lib/queue";
import { emptyTraceView, readTrace, type TraceView } from "@/lib/trace";

import styles from "./panel.module.css";
import { TraceSection } from "./trace-panel";

type Loaded = { view: TraceView } | { error: string } | null;

/** The trace of an Atendimento as the backend recorded it, live or closed (ADR-0028). */
export function RecordedTrace({ sessionId }: { sessionId: string }) {
  const [loaded, setLoaded] = useState<Loaded>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRecordedTrace(sessionId)
      .then((jsonl) => {
        if (!cancelled) setLoaded({ view: readTrace(jsonl) });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoaded({ error: error instanceof ApiError ? error.message : "Não consegui ler o Rastro." });
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  if (loaded === null) {
    return <TraceSection view={emptyTraceView} status="connecting" label={`Lendo o Rastro de ${sessionId}…`} />;
  }
  if ("error" in loaded) {
    return (
      <section className={styles.trace} aria-label="Rastro">
        <p className={styles.empty} role="alert">
          {loaded.error}
        </p>
      </section>
    );
  }
  const { view } = loaded;
  // A trace without `session_ended` belongs to an Atendimento still going, or one the API lost.
  const label = view.ended ? `Atendimento ${sessionId} encerrado` : `Atendimento ${sessionId} sem encerramento gravado`;
  return <TraceSection view={view} status={view.ended ? "ended" : "reconnecting"} label={label} />;
}
