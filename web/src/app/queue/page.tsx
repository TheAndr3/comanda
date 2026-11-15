import type { Metadata } from "next";
import { Suspense } from "react";

import { QueuePanel } from "@/components/queue-panel";

export const metadata: Metadata = {
  title: "Comanda · Fila",
  description: "Reembolsos pendentes e tickets abertos para o atendente humano, com o Rastro de origem.",
};

export default function QueuePage() {
  // The open trace lives in the query string (`?rastro=`), read in the browser: the page is
  // prerendered and the Fila itself comes from the API at runtime (ADR-0025).
  return (
    <Suspense>
      <QueuePanel />
    </Suspense>
  );
}
