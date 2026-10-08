// traces: the tool-call index (`index`, and the reads `ak tracer calls` makes). Run it as
// `node plugins/tracer/traces/bin/traces.mjs <verb>` on any OS; it runs dist/traces.mjs from the
// home directory, so every caller reads and writes the one home trace store.
import { launch } from "./_launch.mjs"

await launch("traces", { debug: "TRACES_DEBUG", home: true })
