import Link from "next/link";

import styles from "./nav.module.css";
import panel from "./panel.module.css";

const SCREENS = [
  { key: "live", href: "/", label: "Atendimento" },
  { key: "queue", href: "/queue", label: "Fila" },
] as const;

export type Screen = (typeof SCREENS)[number]["key"];

/** The panel's name and the links between its screens; `current` is the screen showing it. */
export function Nav({ current }: { current: Screen }) {
  return (
    <div className={styles.identity}>
      <h1 className={panel.brand}>
        Comanda <span>Painel</span>
      </h1>
      <nav aria-label="Telas do painel">
        <ul className={styles.links}>
          {SCREENS.map((screen) => (
            <li key={screen.key}>
              <Link href={screen.href} aria-current={screen.key === current ? "page" : undefined}>
                {screen.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
