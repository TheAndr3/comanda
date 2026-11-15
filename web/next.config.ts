import type { NextConfig } from "next";

// `pnpm build:replay` sets COMANDA_PUBLIC_REPLAY=1: a static export in `out/` with only the
// replays, for the public site (ADR-0002, ADR-0029). Without it, `next dev` and `next build`
// stay a normal Next.js app, so the live screen and the Fila keep any feature they need.
const publicReplay = process.env.COMANDA_PUBLIC_REPLAY === "1";

const nextConfig: NextConfig = publicReplay
  ? {
      output: "export",
      // `/replay/S-…/index.html` instead of `/replay/S-….html`: any static host serves it.
      trailingSlash: true,
    }
  : {};

export default nextConfig;
