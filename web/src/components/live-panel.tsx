"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { ApiError, apiUrl, closeSession, listCustomers, sendMessage, startSession, type CustomerOption } from "@/lib/api";

import { Nav } from "./nav";
import styles from "./panel.module.css";
import { TracePanel } from "./trace-panel";

interface ChatEntry {
  from: "customer" | "reception" | "notice";
  text: string;
}

interface LiveSession {
  id: string;
  customer: CustomerOption;
  closed: boolean;
}

type Busy = "starting" | "sending" | "closing" | null;

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Algo deu errado ao falar com a API.";
}

/** The live screen: chat with a chosen customer next to the Atendimento's trace. */
export function LivePanel() {
  const [customers, setCustomers] = useState<CustomerOption[] | null>(null);
  const [customersError, setCustomersError] = useState<string | null>(null);
  const [customerId, setCustomerId] = useState("");
  const [session, setSession] = useState<LiveSession | null>(null);
  const [chat, setChat] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<Busy>(null);

  useEffect(() => {
    let cancelled = false;
    listCustomers()
      .then((found) => {
        if (cancelled) return;
        setCustomers(found);
        setCustomerId((current) => current || (found[0]?.id ?? ""));
      })
      .catch((error: unknown) => {
        if (!cancelled) setCustomersError(errorText(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const log = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [chat, busy]);

  const say = (...entries: ChatEntry[]) => setChat((current) => [...current, ...entries]);
  const open = session !== null && !session.closed;

  async function start() {
    const customer = customers?.find((c) => c.id === customerId);
    if (!customer) return;
    setBusy("starting");
    try {
      if (open) await closeSession(session.id).catch(() => undefined);
      const id = await startSession(customer.id);
      setSession({ id, customer, closed: false });
      setChat([{ from: "notice", text: `Atendimento ${id} iniciado para ${customer.name} (${customer.id}).` }]);
      setDraft("");
    } catch (error) {
      say({ from: "notice", text: errorText(error) });
    } finally {
      setBusy(null);
    }
  }

  async function close() {
    if (!open) return;
    setBusy("closing");
    try {
      await closeSession(session.id);
      setSession({ ...session, closed: true });
      say({ from: "notice", text: "Atendimento encerrado." });
    } catch (error) {
      say({ from: "notice", text: errorText(error) });
    } finally {
      setBusy(null);
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!open || busy !== null || text === "") return;
    setDraft("");
    say({ from: "customer", text });
    setBusy("sending");
    try {
      const replies = await sendMessage(session.id, text);
      say(...replies.map((reply): ChatEntry => ({ from: "reception", text: reply })));
    } catch (error) {
      say({ from: "notice", text: errorText(error) });
    } finally {
      setBusy(null);
    }
  }

  function submitOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    // Enter also confirms an IME composition; that keystroke must not send the message.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.currentTarget.form?.requestSubmit();
  }

  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <Nav current="live" />
        <div className={styles.controls}>
          <label htmlFor="customer">Cliente</label>
          <select
            id="customer"
            value={customerId}
            onChange={(event) => setCustomerId(event.target.value)}
            disabled={customers === null || busy !== null}
          >
            {customers === null && <option value="">{customersError ? "indisponível" : "carregando…"}</option>}
            {customers?.map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.id} · {customer.name}
              </option>
            ))}
          </select>
          <button type="button" onClick={start} disabled={!customerId || busy !== null} className={styles.primary}>
            {open ? "Novo atendimento" : "Iniciar atendimento"}
          </button>
          <button type="button" onClick={close} disabled={!open || busy !== null}>
            Encerrar
          </button>
        </div>
      </header>

      {customersError && (
        <p className={styles.banner} role="alert">
          {customersError} Suba a API com <code>uv run comanda serve</code> e confira{" "}
          <code>NEXT_PUBLIC_COMANDA_API_URL</code> (agora {apiUrl}).
        </p>
      )}

      <main className={styles.columns}>
        <section className={styles.chat} aria-labelledby="chat-title">
          <header className={styles.chatHeader}>
            <h2 id="chat-title">Atendimento</h2>
            {session && (
              <p>
                {session.customer.name} · <code>{session.id}</code>
                {session.closed && " · encerrado"}
              </p>
            )}
          </header>
          <ol ref={log} className={styles.messages} role="log" aria-label="Conversa com a Recepção" tabIndex={0}>
            {!session && (
              <li className={styles.empty}>Escolha um cliente e inicie um atendimento para conversar com a Recepção.</li>
            )}
            {chat.map((entry, index) => (
              <li key={index} className={styles.message} data-from={entry.from}>
                {entry.from !== "notice" && (
                  <span className={styles.author}>{entry.from === "customer" ? "Cliente" : "Recepção"}</span>
                )}
                <p>{entry.text}</p>
              </li>
            ))}
            {busy === "sending" && (
              <li className={styles.message} data-from="reception" aria-busy="true">
                <span className={styles.author}>Recepção</span>
                <p className={styles.typing}>respondendo…</p>
              </li>
            )}
          </ol>
          <form className={styles.composer} onSubmit={send}>
            <label htmlFor="message" className={styles.visuallyHidden}>
              Mensagem do cliente
            </label>
            <textarea
              id="message"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={submitOnEnter}
              placeholder={open ? "Escreva como o cliente… (Enter envia, Shift+Enter quebra linha)" : "Inicie um atendimento"}
              disabled={!open}
              rows={2}
            />
            <button type="submit" className={styles.primary} disabled={!open || busy !== null || draft.trim() === ""}>
              Enviar
            </button>
          </form>
        </section>

        {session ? (
          <TracePanel key={session.id} sessionId={session.id} />
        ) : (
          <section className={styles.trace} aria-label="Rastro">
            <p className={styles.empty}>
              O Rastro aparece aqui ao vivo: delegações, ferramentas, bloqueios de guardrail, tokens e custo.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}
