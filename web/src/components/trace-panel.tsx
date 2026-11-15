"use client";

import { useEffect, useReducer, useRef, useState } from "react";

import { eventsUrl } from "@/lib/api";
import { applyLine, emptyTraceView, type Step, type TraceView } from "@/lib/trace";

import { agentName, formatInt, formatJson, formatPercent, formatTime, formatUsd } from "./format";
import styles from "./panel.module.css";

type Connection = "connecting" | "live" | "reconnecting" | "lost";

const CONNECTION_LABEL: Record<Connection | "ended", string> = {
  connecting: "Conectando ao Rastro…",
  live: "Ao vivo",
  reconnecting: "Reconectando…",
  lost: "Sem conexão com o Rastro",
  ended: "Atendimento encerrado",
};

/** Follows one Atendimento's trace over SSE and shows each step as it happens. */
export function TracePanel({ sessionId }: { sessionId: string }) {
  const [view, dispatch] = useReducer(applyLine, emptyTraceView);
  const [connection, setConnection] = useState<Connection>("connecting");
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    // EventSource reconnects by itself and sends `Last-Event-ID`, so the API resumes from the
    // next event (ADR-0021); a repeat that still slips through is dropped by `applyLine`.
    const events = new EventSource(eventsUrl(sessionId));
    source.current = events;
    events.onopen = () => setConnection("live");
    events.onmessage = (message: MessageEvent<string>) => dispatch(message.data);
    // CLOSED means the browser gave up, e.g. a 404 after the API restarted and lost the session.
    events.onerror = () => setConnection(events.readyState === EventSource.CLOSED ? "lost" : "reconnecting");
    return () => events.close();
  }, [sessionId]);

  useEffect(() => {
    // The stream ends with the Atendimento; without this the browser would keep reconnecting.
    if (view.ended) source.current?.close();
  }, [view.ended]);

  const status = view.ended ? "ended" : connection;
  return <TraceSection view={view} status={status} label={CONNECTION_LABEL[status]} />;
}

/**
 * One Atendimento's trace, however it was read: live over SSE or recorded (the Fila). `status`
 * styles the indicator next to the title and `label` says it in words.
 */
export function TraceSection({ view, status, label }: { view: TraceView; status: string; label: string }) {
  const list = useRef<HTMLOListElement>(null);
  const followBottom = useRef(true);
  useEffect(() => {
    const element = list.current;
    if (element && followBottom.current) element.scrollTop = element.scrollHeight;
  }, [view.steps]);

  return (
    <section className={styles.trace} aria-labelledby="trace-title">
      <header className={styles.traceHeader}>
        <div className={styles.traceTitleRow}>
          <h2 id="trace-title">Rastro</h2>
          <p className={styles.connection} data-status={status} role="status">
            {label}
          </p>
        </div>
        <TraceSummary view={view} />
      </header>
      <ol
        ref={list}
        className={styles.steps}
        aria-label="Passos do atendimento"
        tabIndex={0}
        onScroll={(event) => {
          const element = event.currentTarget;
          followBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
        }}
      >
        {view.steps.map((step, index) => (
          <StepItem key={index} step={step} />
        ))}
      </ol>
    </section>
  );
}

function TraceSummary({ view }: { view: TraceView }) {
  const { usage } = view;
  const models = Object.entries(usage.byModel);
  const count = (kind: Step["kind"]) => view.steps.filter((step) => step.kind === kind).length;

  return (
    <div className={styles.summary}>
      <dl className={styles.metrics}>
        <div>
          <dt>Custo equivalente em API</dt>
          <dd>{formatUsd(usage.costUsd)}</dd>
        </div>
        <div>
          <dt>Delegações</dt>
          <dd>{count("delegation")}</dd>
        </div>
        <div>
          <dt>Ferramentas</dt>
          <dd>{count("tool")}</dd>
        </div>
        <div data-alert={count("guardrail_blocked") > 0 || undefined}>
          <dt>Bloqueios</dt>
          <dd>{count("guardrail_blocked")}</dd>
        </div>
      </dl>
      {models.length > 0 && (
        <table className={styles.models}>
          <caption>Tokens por modelo (custo é estimativa)</caption>
          <thead>
            <tr>
              <th scope="col">Modelo</th>
              <th scope="col">Entrada</th>
              <th scope="col">Cache</th>
              <th scope="col">Saída</th>
              <th scope="col">Custo</th>
            </tr>
          </thead>
          <tbody>
            {models.map(([model, totals]) => (
              <tr key={model}>
                <th scope="row">{model}</th>
                <td>{formatInt(totals.inputTokens)}</td>
                <td>{formatInt(totals.cacheReadTokens + totals.cacheWriteTokens)}</td>
                <td>{formatInt(totals.outputTokens)}</td>
                <td>
                  {formatUsd(totals.costUsd)}
                  {usage.unpricedModels.includes(model) && (
                    <span title="Há chamadas sem preço conhecido, fora do total."> *</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {usage.unpricedModels.length > 0 && (
        <p className={styles.note}>* Chamadas sem preço conhecido ficam fora do total.</p>
      )}
    </div>
  );
}

function Payload({ label, value }: { label: string; value: unknown }) {
  return (
    <details className={styles.payload}>
      <summary>{label}</summary>
      <pre>{formatJson(value)}</pre>
    </details>
  );
}

function StepItem({ step }: { step: Step }) {
  const time = "ts" in step ? <time dateTime={step.ts}>{formatTime(step.ts)}</time> : null;
  return (
    <li className={styles.step} data-kind={step.kind}>
      {time}
      <div className={styles.stepBody}>
        <StepContent step={step} />
      </div>
    </li>
  );
}

function StepContent({ step }: { step: Step }) {
  switch (step.kind) {
    case "session_started":
      return <p className={styles.stepTitle}>Atendimento iniciado para o cliente {step.customerId}</p>;
    case "message":
      return (
        <p className={styles.stepQuote}>
          <strong>{agentName(step.from)}:</strong> {step.text}
        </p>
      );
    case "delegation":
      return (
        <>
          <p className={styles.stepTitle}>
            Recepção <span aria-label="delega a">→</span> {agentName(step.specialist)}
          </p>
          <p className={styles.stepText}>{step.task}</p>
        </>
      );
    case "specialist_returned":
      return (
        <>
          <p className={styles.stepTitle}>
            {agentName(step.specialist)} <span aria-label="devolve à">→</span> Recepção
          </p>
          <p className={styles.stepText}>{step.result}</p>
        </>
      );
    case "tool":
      return (
        <>
          <p className={styles.stepTitle}>
            {agentName(step.agent)} chama <code>{step.tool}</code>{" "}
            <span className={styles.badge} data-status={step.status}>
              {step.status === "running" ? "em andamento" : step.status === "ok" ? "ok" : "erro"}
            </span>
          </p>
          <Payload label="Argumentos" value={step.args} />
          {step.status !== "running" && <Payload label="Resultado" value={step.result} />}
        </>
      );
    case "guardrail_blocked":
      return (
        <>
          <p className={styles.stepTitle}>
            Guardrail <code>{step.guardrail}</code> bloqueou <code>{step.tool}</code> ({agentName(step.agent)})
          </p>
          <p className={styles.stepText}>{step.reason}</p>
          <Payload label="Argumentos" value={step.args} />
        </>
      );
    case "escalation":
      return (
        <p className={styles.stepTitle}>
          Ticket {step.ticketId} aberto para um atendente humano, prioridade {step.priority}
          {step.orderId ? `, pedido ${step.orderId}` : ""}
        </p>
      );
    case "usage":
      return (
        <p className={styles.stepMeta}>
          {agentName(step.agent)} em {step.model}: {formatInt(step.inputTokens)} entrada
          {step.cacheReadTokens + step.cacheWriteTokens > 0 &&
            ` (+${formatInt(step.cacheReadTokens + step.cacheWriteTokens)} cache)`}
          , {formatInt(step.outputTokens)} saída,{" "}
          {step.costUsd === null ? "custo desconhecido" : `${formatUsd(step.costUsd)} (estimativa)`}
        </p>
      );
    case "quota":
      return (
        <p className={styles.stepMeta}>
          Cota da assinatura, janela {step.window}:{" "}
          {step.utilization === null ? step.status : `${formatPercent(step.utilization)} usada (${step.status})`}
        </p>
      );
    case "session_ended":
      return <p className={styles.stepTitle}>Atendimento encerrado</p>;
    case "other":
      return (
        <>
          <p className={styles.stepTitle}>
            Evento <code>{step.type}</code> de {agentName(step.agent)}
          </p>
          <Payload label="Dados" value={step.data} />
        </>
      );
    case "unsupported_version":
      return (
        <>
          <p className={styles.stepTitle}>
            Evento na versão de esquema {String(step.version)}, que este painel não sabe ler
          </p>
          <Payload label="Linha original" value={step.raw} />
        </>
      );
    case "invalid":
      return (
        <>
          <p className={styles.stepTitle}>Linha do Rastro ilegível: {step.reason}</p>
          <Payload label="Linha original" value={step.raw} />
        </>
      );
  }
}
