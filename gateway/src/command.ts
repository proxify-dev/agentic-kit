/**
 * The command a person typed to reach this CLI, as every hint spells it back.
 * Standalone that is `cc-gateway`; inside the kit, `ak gateway` execs the CLI
 * with AK_GATEWAY_COMMAND="ak gateway" set, and the hints say that instead.
 * keeper.ts reads the same variable itself: it may import only brand.ts.
 */
import { env } from "./brand.ts"

/** The command, or the command and a verb: `command("enable")` → "cc-gateway enable". */
export function command(verb = ""): string {
  return `${env("GATEWAY_COMMAND", "cc-gateway")} ${verb}`.trim()
}

/** True inside the kit — where the kit's own tools (`ak tracer …`) are there to point at. */
export const inKit = (): boolean => env("GATEWAY_COMMAND") !== undefined
