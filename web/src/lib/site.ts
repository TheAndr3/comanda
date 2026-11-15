/**
 * True in the public build (`pnpm build:replay`): a static export that shows only the replays,
 * with no API and no model behind it (ADR-0002, ADR-0029). Read by server code at build time.
 */
export const publicReplay = process.env.COMANDA_PUBLIC_REPLAY === "1";
