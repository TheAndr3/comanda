import Link from "next/link";

import type { ReplayEntry } from "@/lib/replay";

import styles from "./replay.module.css";

const REPO_URL = "https://github.com/TheAndr3/comanda";

/** The screen that lists the recorded Atendimentos and opens one to play. */
export function ReplayList({ replays, publicSite }: { replays: ReplayEntry[]; publicSite: boolean }) {
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.brand}>
          Comanda <span>Replay</span>
        </h1>
        <nav className={styles.nav} aria-label="Painel">
          {!publicSite && (
            <>
              <Link href="/">Atendimento</Link>
              <Link href="/queue">Fila</Link>
            </>
          )}
          <a href={REPO_URL}>Código no GitHub</a>
        </nav>
      </header>

      <main className={styles.listMain}>
        <section className={styles.intro} aria-labelledby="intro-title">
          <h2 id="intro-title">Atendimentos gravados ao lado do Rastro</h2>
          <p>
            O Comanda atende clientes de um app de delivery com agentes: a Recepção conversa com o cliente e delega aos
            especialistas de Pedidos e de Reembolsos. O valor do reembolso sai sempre de uma regra em código, e guardrails
            fora do modelo barram o que o processo não permite.
          </p>
          <p>
            Cada replay reproduz um atendimento gravado, passo a passo, sem chamar nenhum modelo. Nome, telefone e e-mail
            do cliente foram mascarados na gravação.
          </p>
        </section>

        {replays.length === 0 ? (
          <p className={styles.emptyList}>
            Nenhum replay exportado ainda. Use <code>uv run comanda export-replay &lt;session_id&gt;</code> no backend.
          </p>
        ) : (
          <ul className={styles.cards}>
            {replays.map((replay) => (
              <li key={replay.sessionId}>
                <Link href={`/replay/${replay.sessionId}`} className={styles.card}>
                  <h3>{replay.title}</h3>
                  <ul className={styles.shows} aria-label="O que este replay mostra">
                    {replay.shows.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                  <p className={styles.meta}>
                    {replay.customerId && <>Cliente {replay.customerId} · </>}
                    <code>{replay.sessionId}</code>
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}
