import type { Metadata } from "next";

import { ReplayList } from "@/components/replay-list";
import { readReplayIndex } from "@/lib/replay/files";
import { publicReplay } from "@/lib/site";

export const metadata: Metadata = {
  title: "Comanda · Replay",
  description: "Atendimentos gravados ao lado do Rastro, sem chamar nenhum modelo.",
};

export default async function ReplaysPage() {
  return <ReplayList replays={await readReplayIndex()} publicSite={publicReplay} />;
}
