import type { Metadata } from "next";

import { LivePanel } from "@/components/live-panel";
import { ReplayList } from "@/components/replay-list";
import { readReplayIndex } from "@/lib/replay/files";
import { publicReplay } from "@/lib/site";

export function generateMetadata(): Metadata {
  return publicReplay
    ? {
        title: "Comanda · Replay",
        description: "Atendimentos multi-agente de delivery gravados ao lado do Rastro, sem chamar nenhum modelo.",
      }
    : {};
}

export default async function Home() {
  // The public site has no API behind it, so it opens on the replays (ADR-0029).
  if (publicReplay) return <ReplayList replays={await readReplayIndex()} publicSite />;
  return <LivePanel />;
}
