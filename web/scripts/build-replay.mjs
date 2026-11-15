// Builds the public replay site: `next build` with COMANDA_PUBLIC_REPLAY=1, which turns on the
// static export into `out/` (ADR-0029). A script, not `VAR=1 next build`, so it runs on Windows too.
import { spawnSync } from "node:child_process";

const result = spawnSync("next", ["build"], {
  stdio: "inherit",
  shell: true,
  env: { ...process.env, COMANDA_PUBLIC_REPLAY: "1" },
});
process.exit(result.status ?? 1);
