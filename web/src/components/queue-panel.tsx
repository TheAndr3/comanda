"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError, apiUrl, listCustomers } from "@/lib/api";
import {
  approvalProblem,
  approveRefund,
  fetchQueue,
  reasonProblem,
  rejectRefund,
  resolveTicket,
  type OpenTicket,
  type PendingRefund,
  type Queue,
} from "@/lib/queue";

import { formatBrl, formatDateTime, issueName } from "./format";
import { Nav } from "./nav";
import styles from "./panel.module.css";
import queueStyles from "./queue.module.css";
import { RecordedTrace } from "./recorded-trace";

/** The query parameter that holds the Atendimento whose trace is open beside the Fila. */
const TRACE_PARAM = "rastro";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Algo deu errado ao falar com a API.";
}

/** A 404 or 409 means someone else already took the item off the Fila (the CLI, another tab). */
function goneFromQueue(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 409);
}

/** The Atendente humano's screen: pending refunds and open tickets, next to the origin trace. */
export function QueuePanel() {
  const selected = useSearchParams().get(TRACE_PARAM);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [customerNames, setCustomerNames] = useState<ReadonlyMap<string, string>>(new Map());
  const [decidedBy, setDecidedBy] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [itemErrors, setItemErrors] = useState<Record<string, string>>({});
  const [announcement, setAnnouncement] = useState("");
  const nameInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setQueue(await fetchQueue());
      setLoadError(null);
    } catch (error) {
      setLoadError(errorText(error));
    }
  }, []);

  useEffect(() => {
    // Both requests settle after the effect, so their state updates do not cascade renders.
    void load();
    listCustomers()
      .then((customers) => setCustomerNames(new Map(customers.map((c) => [c.id, c.name]))))
      .catch(() => undefined); // without names the Fila still shows customer ids
  }, [load]);

  const customer = (id: string) => {
    const name = customerNames.get(id);
    return name ? `${name} (${id})` : id;
  };

  function checkName(): boolean {
    const problem = approvalProblem(decidedBy);
    setNameError(problem);
    if (problem) nameInput.current?.focus();
    return problem === null;
  }

  /** Runs one decision on an item; on success the item leaves the Fila. */
  async function act(id: string, action: () => Promise<void>, done: string, remove: (queue: Queue) => Queue) {
    setBusy(id);
    setItemErrors((current) => {
      const rest = { ...current };
      delete rest[id];
      return rest;
    });
    try {
      await action();
      setQueue((current) => current && remove(current));
      setAnnouncement(done);
    } catch (error) {
      if (goneFromQueue(error)) {
        setAnnouncement(`${errorText(error)} A Fila foi atualizada.`);
        await load();
      } else {
        setItemErrors((current) => ({ ...current, [id]: errorText(error) }));
      }
    } finally {
      setBusy(null);
    }
  }

  const withoutRefund = (id: string) => (q: Queue) => ({
    ...q,
    pendingRefunds: q.pendingRefunds.filter((r) => r.id !== id),
  });

  function approve(refund: PendingRefund) {
    if (!checkName()) return;
    void act(
      refund.id,
      () => approveRefund(refund.id, decidedBy),
      `Reembolso ${refund.id} aprovado: ${formatBrl(refund.amountCents)}.`,
      withoutRefund(refund.id),
    );
  }

  function reject(refund: PendingRefund, reason: string) {
    void act(
      refund.id,
      () => rejectRefund(refund.id, decidedBy, reason),
      `Reembolso ${refund.id} recusado.`,
      withoutRefund(refund.id),
    );
  }

  function resolve(ticket: OpenTicket) {
    void act(ticket.id, () => resolveTicket(ticket.id), `Ticket ${ticket.id} resolvido.`, (q) => ({
      ...q,
      openTickets: q.openTickets.filter((t) => t.id !== ticket.id),
    }));
  }

  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <Nav current="queue" />
        <div className={styles.controls}>
          <label htmlFor="decided-by">Atendente</label>
          <input
            ref={nameInput}
            id="decided-by"
            className={queueStyles.name}
            value={decidedBy}
            onChange={(event) => {
              setDecidedBy(event.target.value);
              if (nameError) setNameError(approvalProblem(event.target.value));
            }}
            required
            autoComplete="name"
            placeholder="Seu nome"
            title="Registrado em cada decisão"
            aria-invalid={nameError !== null}
            aria-describedby={nameError ? "decided-by-error" : undefined}
          />
          <button type="button" onClick={() => void load()} disabled={busy !== null}>
            Atualizar
          </button>
        </div>
        {nameError && (
          <p id="decided-by-error" className={queueStyles.fieldError}>
            {nameError}
          </p>
        )}
      </header>

      {loadError && (
        <p className={styles.banner} role="alert">
          {loadError} Suba a API com <code>uv run comanda serve</code> e confira{" "}
          <code>NEXT_PUBLIC_COMANDA_API_URL</code> (agora {apiUrl}).
        </p>
      )}
      {/* Always in the page, so screen readers announce each new decision as it changes. */}
      <p className={queueStyles.notice} role="status">
        {announcement}
      </p>

      <main className={styles.columns}>
        <div className={queueStyles.queue}>
          <section aria-labelledby="refunds-title">
            <h2 id="refunds-title" className={queueStyles.heading}>
              Reembolsos pendentes {queue && <span>{queue.pendingRefunds.length}</span>}
            </h2>
            {queue?.pendingRefunds.length === 0 && <p className={queueStyles.none}>Nenhum reembolso pendente.</p>}
            <ul className={queueStyles.items}>
              {queue?.pendingRefunds.map((refund) => (
                <RefundItem
                  key={refund.id}
                  refund={refund}
                  customer={customer(refund.customerId)}
                  selected={refund.sessionId !== null && refund.sessionId === selected}
                  busy={busy === refund.id}
                  disabled={busy !== null}
                  error={itemErrors[refund.id]}
                  onApprove={() => approve(refund)}
                  onReject={(reason) => reject(refund, reason)}
                  checkName={checkName}
                />
              ))}
            </ul>
          </section>

          <section aria-labelledby="tickets-title">
            <h2 id="tickets-title" className={queueStyles.heading}>
              Tickets abertos {queue && <span>{queue.openTickets.length}</span>}
            </h2>
            {queue?.openTickets.length === 0 && <p className={queueStyles.none}>Nenhum ticket aberto.</p>}
            <ul className={queueStyles.items}>
              {queue?.openTickets.map((ticket) => (
                <TicketItem
                  key={ticket.id}
                  ticket={ticket}
                  customer={customer(ticket.customerId)}
                  selected={ticket.sessionId !== null && ticket.sessionId === selected}
                  busy={busy === ticket.id}
                  disabled={busy !== null}
                  error={itemErrors[ticket.id]}
                  onResolve={() => resolve(ticket)}
                />
              ))}
            </ul>
          </section>
        </div>

        {selected ? (
          <div className={queueStyles.traceColumn}>
            <Link href="/queue" scroll={false} className={queueStyles.closeTrace}>
              Fechar Rastro
            </Link>
            <RecordedTrace key={selected} sessionId={selected} />
          </div>
        ) : (
          <section className={`${styles.trace} ${queueStyles.tracePlaceholder}`} aria-label="Rastro">
            <p className={styles.empty}>
              Abra o Rastro de um item para ver o atendimento de origem: delegações, ferramentas, guardrails e o
              que a Recepção disse ao cliente.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}

function TraceLink({ sessionId }: { sessionId: string | null }) {
  if (sessionId === null) {
    return <p className={queueStyles.noTrace}>Criado fora de um atendimento: sem Rastro.</p>;
  }
  return (
    <Link href={`/queue?${TRACE_PARAM}=${encodeURIComponent(sessionId)}`} scroll={false} className={queueStyles.traceLink}>
      Ver o Rastro do atendimento <code>{sessionId}</code>
    </Link>
  );
}

/** What every item of the Fila gets from the screen, besides the item itself. */
interface ItemProps {
  customer: string;
  /** Its Atendimento's trace is the one open beside the Fila. */
  selected: boolean;
  busy: boolean;
  disabled: boolean;
  error: string | undefined;
}

function ItemError({ error }: { error: string | undefined }) {
  return error ? (
    <p className={queueStyles.fieldError} role="alert">
      {error}
    </p>
  ) : null;
}

function TicketItem({
  ticket,
  customer,
  selected,
  busy,
  disabled,
  error,
  onResolve,
}: ItemProps & { ticket: OpenTicket; onResolve: () => void }) {
  return (
    <li className={queueStyles.item} data-selected={selected || undefined}>
      <header className={queueStyles.itemHeader}>
        <h3>
          Ticket <code>{ticket.id}</code>
        </h3>
        <span className={queueStyles.priority} data-priority={ticket.priority}>
          prioridade {ticket.priority}
        </span>
      </header>
      <p className={queueStyles.summary}>{ticket.summary}</p>
      <dl className={queueStyles.facts}>
        <div>
          <dt>Cliente</dt>
          <dd>{customer}</dd>
        </div>
        <div>
          <dt>Pedido</dt>
          <dd>{ticket.orderId ?? "nenhum"}</dd>
        </div>
        <div>
          <dt>Aberto em</dt>
          <dd>
            <time dateTime={ticket.createdAt}>{formatDateTime(ticket.createdAt)}</time>
          </dd>
        </div>
      </dl>
      <TraceLink sessionId={ticket.sessionId} />
      <div className={queueStyles.actions}>
        <button type="button" className={queueStyles.approve} onClick={onResolve} disabled={disabled}>
          {busy ? "Resolvendo…" : "Marcar como resolvido"}
        </button>
      </div>
      <ItemError error={error} />
    </li>
  );
}

interface RefundItemProps extends ItemProps {
  refund: PendingRefund;
  onApprove: () => void;
  onReject: (reason: string) => void;
  checkName: () => boolean;
}

function RefundItem({ refund, customer, selected, busy, disabled, error, onApprove, onReject, checkName }: RefundItemProps) {
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);
  const reasonId = `reason-${refund.id}`;

  function submitRejection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!checkName()) return;
    // The reason is mandatory: blank or only spaces stops here, before the API is called.
    const problem = reasonProblem(reason);
    setReasonError(problem);
    if (problem) {
      event.currentTarget.querySelector("textarea")?.focus();
      return;
    }
    onReject(reason);
  }

  return (
    <li className={queueStyles.item} data-selected={selected || undefined}>
      <header className={queueStyles.itemHeader}>
        <h3>
          Reembolso <code>{refund.id}</code>
        </h3>
        {/* Shown, never edited: the amount comes from the rule (ADR-0001, ADR-0007). */}
        <p className={queueStyles.amount}>
          <span className={styles.visuallyHidden}>Valor: </span>
          {formatBrl(refund.amountCents)}
        </p>
      </header>
      <dl className={queueStyles.facts}>
        <div>
          <dt>Cliente</dt>
          <dd>{customer}</dd>
        </div>
        <div>
          <dt>Pedido</dt>
          <dd>{refund.orderId}</dd>
        </div>
        <div>
          <dt>Problema</dt>
          <dd>{issueName(refund.issue)}</dd>
        </div>
        <div>
          <dt>Regra</dt>
          <dd>
            <code>{refund.ruleId}</code>
          </dd>
        </div>
        <div>
          <dt>Registrado em</dt>
          <dd>
            <time dateTime={refund.createdAt}>{formatDateTime(refund.createdAt)}</time>
          </dd>
        </div>
      </dl>
      <TraceLink sessionId={refund.sessionId} />
      <form className={queueStyles.decision} onSubmit={submitRejection} noValidate>
        <label htmlFor={reasonId}>Motivo da recusa</label>
        <textarea
          id={reasonId}
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
            if (reasonError) setReasonError(reasonProblem(event.target.value));
          }}
          required
          rows={2}
          placeholder="Obrigatório para recusar"
          aria-invalid={reasonError !== null}
          aria-describedby={reasonError ? `${reasonId}-error` : undefined}
        />
        {reasonError && (
          <p id={`${reasonId}-error`} className={queueStyles.fieldError}>
            {reasonError}
          </p>
        )}
        <div className={queueStyles.actions}>
          <button type="button" className={queueStyles.approve} onClick={onApprove} disabled={disabled}>
            {busy ? "Enviando…" : "Aprovar"}
          </button>
          <button type="submit" className={queueStyles.reject} disabled={disabled}>
            Recusar
          </button>
        </div>
      </form>
      <ItemError error={error} />
    </li>
  );
}
