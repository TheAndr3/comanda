import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ReplayPlayer } from "@/components/replay-player";
import { readReplayIndex, readReplayTrace } from "@/lib/replay/files";

// Only the exported replays exist; any other id is a 404, also in the static export.
export const dynamicParams = false;

export async function generateStaticParams() {
  return (await readReplayIndex()).map((replay) => ({ sessionId: replay.sessionId }));
}

async function findReplay(sessionId: string) {
  return (await readReplayIndex()).find((replay) => replay.sessionId === sessionId);
}

export async function generateMetadata({ params }: PageProps<"/replay/[sessionId]">): Promise<Metadata> {
  const entry = await findReplay((await params).sessionId);
  return { title: entry ? `${entry.title} · Comanda Replay` : "Comanda · Replay" };
}

export default async function ReplayPage({ params }: PageProps<"/replay/[sessionId]">) {
  const entry = await findReplay((await params).sessionId);
  if (!entry) notFound();
  return <ReplayPlayer entry={entry} trace={await readReplayTrace(entry.sessionId)} />;
}
