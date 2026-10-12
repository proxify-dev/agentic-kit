/**
 * `ak xray` end to end: the CLI as a subprocess against an in-process
 * gateway (createServer, scratch data dir, a fake upstream that counts what
 * reaches it), and a "claude" on PATH that is a script — it sends one side call
 * and one main call through the base URL it was given, with the headers xray
 * put in ANTHROPIC_CUSTOM_HEADERS, and writes down what came back. Then the
 * pieces on their own: the section cut on a body shaped like a real one, the
 * plan for a command line, the line diff.
 *
 * What these keep honest: the gateway dry-runs every model call and nothing
 * reaches upstream; the catch is the main row and never the side one; the
 * launch carries the routed env (and none of a parent session's); `-o` then
 * `--vs` shows only what changed, reading the body back from the gateway's
 * blobs; a person's session is killed at the catch and what it left removed;
 * a gateway that is off, or too old to dry-run, launches nothing; a model call
 * that went upstream anyway is reported; a launch that sends nothing is
 * reported with what it printed.
 */
import assert from "node:assert/strict"
import { spawn } from "node:child_process"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import http from "node:http"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import test, { after } from "node:test"
import { fileURLToPath } from "node:url"

import { envName } from "../src/brand.ts"
import { command as gw } from "../src/command.ts"
import { loadConfig } from "../src/config.ts"
import { createServer } from "../src/server.ts"
import { anatomy, lineDiff, pinsFor, plan, splitSections } from "../src/xray.ts"
import type { Body } from "../src/xray.ts"

const XRAY = fileURLToPath(new URL("../src/xray.ts", import.meta.url))

/**
 * A claude stand-in. FAKE_TAG is its [tracer] line; FAKE_HANG makes it
 * print a folder-trust question and send nothing; FAKE_STAY keeps it running
 * after the main call, as a TUI would; FAKE_LEAK has it send one more call of
 * the same session without the dry-run header.
 */
function fakeClaude(dir: string): string {
  const js = join(dir, "fake-claude.mjs")
  writeFileSync(
    js,
    `import { mkdirSync, writeFileSync } from "node:fs"
import { join } from "node:path"
const report = { argv: process.argv.slice(2), pid: process.pid, env: {} }
for (const k of ["ANTHROPIC_AUTH_TOKEN", "CC_SHIM_DISABLE", "ENABLE_TOOL_SEARCH", "CLAUDE_CODE_GATEWAY_HINT_HEADERS", "CLAUDE_CODE_OAUTH_TOKEN",
  "ANTHROPIC_API_KEY", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "HTTPS_PROXY", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS"])
  report.env[k] = process.env[k] ?? null
const save = () => writeFileSync(process.env.FAKE_REPORT, JSON.stringify(report))
save()
if (process.env.FAKE_HANG) {
  console.log("Quick safety check: Do you trust the files in this folder?")
  setInterval(() => {}, 1000)
} else {
  const sid = "0a1b2c3d-1111-4222-8333-444455556666"
  if (process.env.FAKE_STAY) {
    const c = process.env.CLAUDE_CONFIG_DIR
    const d = join(c, "projects", "-scratch")
    mkdirSync(join(d, sid), { recursive: true })
    writeFileSync(join(d, sid + ".jsonl"), "{}\\n")
    mkdirSync(join(c, "session-env", sid), { recursive: true })
    mkdirSync(join(c, "teams", "session-" + sid.slice(0, 8)), { recursive: true })
    mkdirSync(join(c, "sessions"), { recursive: true })
    writeFileSync(join(c, "sessions", process.pid + ".json"), JSON.stringify({ pid: process.pid, sessionId: sid }))
    writeFileSync(join(c, "sessions", process.pid + ".abc.key"), "k")
    writeFileSync(join(c, "sessions", "1.json"), JSON.stringify({ pid: 1, sessionId: "someone-else" }))
  }
  const hdrs = Object.fromEntries((process.env.ANTHROPIC_CUSTOM_HEADERS ?? "").split("\\n").filter(Boolean)
    .map((l) => [l.slice(0, l.indexOf(":")).trim(), l.slice(l.indexOf(":") + 1).trim()]))
  const post = (cls, body) => fetch(process.env.ANTHROPIC_BASE_URL + "/v1/messages?beta=true", {
    method: "POST",
    headers: { "content-type": "application/json", "x-claude-code-request-class": cls, "x-claude-code-session-id": sid, ...hdrs },
    body: JSON.stringify(body),
  })
  // Claude Code's own background call, as 2.1.281 sends it: to the base URL, without the custom headers.
  report.hello = (await fetch(process.env.ANTHROPIC_BASE_URL + "/api/hello")).status
  const side = await post("auxiliary", { model: "claude-haiku-4-5", max_tokens: 1, tools: [{ name: "Side", input_schema: {} }], messages: [{ role: "user", content: "quota" }] })
  report.side = await side.json()
  const tag = process.env.FAKE_TAG ?? "[tracer] which session did what: /tracer:trace"
  const main = await post("main", {
    model: "claude-opus-5-5",
    stream: true,
    system: [{ type: "text", text: "x-anthropic-billing-header: cc_version=9;" }, { type: "text", text: "You are Claude Code, Anthropic's official CLI for Claude." },
      { type: "text", text: "You are an interactive agent.\\n\\n# Harness\\nrun tools\\n\\n# Environment\\ncwd: /x" }],
    tools: [{ name: "Bash", description: "run a command", input_schema: { type: "object" } }, { name: "Read", description: "read a file", input_schema: { type: "object" } }],
    messages: [
      { role: "user", content: [{ type: "text", text: "<system-reminder>\\nContents of /p/CLAUDE.md (project instructions):\\n\\n# Title\\nbody\\n</system-reminder>\\n" }, { type: "text", text: "hi" }] },
      { role: "system", content: [{ type: "text", text: "SessionStart hook additional context: " + tag + "\\n[wikilinks] links resolve inside Read\\n\\n# Environment\\nplatform: darwin" }] },
    ],
  })
  const sse = await main.text()
  const events = sse.split("\\n\\n").filter(Boolean).map((e) => JSON.parse(e.slice(e.indexOf("data: ") + 6)))
  report.main = { status: main.status, type: main.headers.get("content-type"), events: events.map((e) => e.type),
    id: events[0]?.message?.id, text: events.filter((e) => e.delta?.type === "text_delta").map((e) => e.delta.text).join(""),
    stop: events.find((e) => e.type === "message_delta")?.delta?.stop_reason }
  if (process.env.FAKE_LEAK) {
    const { "x-brain-dry-run": _drop, ...plain } = hdrs
    const r = await fetch(process.env.ANTHROPIC_BASE_URL + "/v1/messages", { method: "POST",
      headers: { "content-type": "application/json", "x-claude-code-request-class": "auxiliary", "x-claude-code-session-id": sid, "x-brain-origin": "cli" },
      body: JSON.stringify({ model: "claude-haiku-4-5", max_tokens: 1, messages: [{ role: "user", content: "title" }] }) })
    report.leak = r.status
    await r.text()
  }
  save()
  if (process.env.FAKE_STAY) setInterval(() => {}, 1000)
}
`,
  )
  const sh = join(dir, "claude")
  writeFileSync(sh, `#!/bin/sh\nexec "${process.execPath}" "${js}" "$@"\n`)
  chmodSync(sh, 0o755)
  return sh
}

type World = {
  dir: string
  env: NodeJS.ProcessEnv
  base: string
  /** Model calls that reached the fake upstream. */
  hits: () => number
  upstream: string[]
  report: () => Record<string, any>
  close: () => Promise<void>
}
const worlds: World[] = []
after(() => Promise.all(worlds.map((w) => w.close())))

/**
 * A scratch world: an in-process gateway on its own data dir, over a fake
 * upstream that counts what reaches it (one account, so a call that is not dry
 * would get there); its own HOME and claude config dir; a parent session's env
 * to be dropped; and the fake claude first on PATH. xray finds the gateway the
 * way it always does — AK_GATEWAY_PORT and _DATA_DIR.
 */
async function world(): Promise<World> {
  const dir = mkdtempSync(join(tmpdir(), "gw-xray-"))
  fakeClaude(dir)
  const seen: string[] = []
  const upstream = http.createServer((req, res) => {
    seen.push(req.url ?? "/")
    req.resume()
    res.writeHead(200, { "content-type": "application/json" })
    res.end(JSON.stringify({ id: "msg_real", type: "message", content: [{ type: "text", text: "real" }], usage: { input_tokens: 1, output_tokens: 1 } }))
  })
  await new Promise<void>((r) => upstream.listen(0, "127.0.0.1", r))
  const accounts = join(dir, "accounts.json")
  writeFileSync(
    accounts,
    JSON.stringify({ accounts: [{ name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "o-a", accessToken: "t", refreshToken: "r", expiresAt: Date.now() + 86_400_000 }] }),
  )
  const gw = await createServer(
    loadConfig({ port: 0, dataDir: join(dir, "data"), upstreamUrl: `http://127.0.0.1:${(upstream.address() as { port: number }).port}`, accountsFile: accounts, capture: "meta", policy: [] }),
  )
  await new Promise<void>((r) => gw.server.listen(0, "127.0.0.1", r))
  const port = (gw.server.address() as { port: number }).port
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    HOME: dir,
    PATH: `${dir}:${dirname(process.execPath)}:/usr/bin:/bin`,
    [envName("GATEWAY_CONFIG")]: join(dir, "gateway.json"),
    [envName("GATEWAY_DATA_DIR")]: join(dir, "data"),
    [envName("GATEWAY_PORT")]: String(port),
    CLAUDE_CONFIG_DIR: join(dir, ".claude"),
    FAKE_REPORT: join(dir, "report.json"),
    CLAUDECODE: "1",
    CLAUDE_CODE_ENTRYPOINT: "cli",
    CLAUDE_CODE_OAUTH_TOKEN: "real-token-must-not-pass",
  }
  for (const k of ["CC_SYSPROMPT_FILE", "ENABLE_TOOL_SEARCH", "CLAUDE_CODE_GATEWAY_HINT_HEADERS", "ANTHROPIC_CUSTOM_HEADERS", envName("XRAY_TIMEOUT"), "HTTPS_PROXY", envName("GATEWAY_ACCOUNTS_FILE")])
    delete env[k]
  let closed = false
  const w: World = {
    dir,
    env,
    base: `http://127.0.0.1:${port}`,
    hits: () => seen.filter((u) => u.split("?")[0].endsWith("/v1/messages")).length,
    upstream: seen,
    report: () => JSON.parse(readFileSync(join(dir, "report.json"), "utf8")) as Record<string, any>,
    close: async () => {
      if (closed) return
      closed = true
      await gw.close()
      await new Promise<void>((r) => upstream.close(() => r()))
    },
  }
  worlds.push(w)
  return w
}

function xray(argv: string[], env: NodeJS.ProcessEnv, cwd: string): Promise<{ code: number | null; out: string; err: string }> {
  return new Promise((resolve) => {
    const c = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", XRAY, ...argv], { env, cwd, stdio: ["ignore", "pipe", "pipe"] })
    let out = ""
    let err = ""
    c.stdout.on("data", (b: Buffer) => (out += b.toString()))
    c.stderr.on("data", (b: Buffer) => (err += b.toString()))
    c.on("exit", (code) => resolve({ code, out, err }))
  })
}

const alive = (pid: number) => {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

test("xray: the gateway dry-runs every model call, the catch is the main one, and the launch carries the routed env", async () => {
  const w = await world()
  const r = await xray(["claude", "-p", "hello"], w.env, w.dir)
  assert.equal(r.code, 0, `${r.out}\n${r.err}`)
  assert.match(r.out, /^ {2}program session · claude-opus-5-5 · 2 model calls dry-run \(1 side\) · none left the gateway$/m)
  assert.equal(w.hits(), 0, "no model call reached upstream")
  assert.match(r.out, /^ {2}tools {8}2 .* Bash .* · Read /m, "the main call's tools, not the side call's")
  assert.doesNotMatch(r.out, /Side/)

  const got = w.report()
  assert.deepEqual(got.argv, ["-p", "hello", "--no-session-persistence"], "the command as typed, plus no persistence")
  assert.equal(got.env.CC_SHIM_DISABLE, "1")
  assert.equal(got.env.ENABLE_TOOL_SEARCH, "true")
  assert.equal(got.env.CLAUDE_CODE_GATEWAY_HINT_HEADERS, "1")
  assert.equal(got.env.CLAUDE_CODE_OAUTH_TOKEN, null, "an inherited credential is dropped")
  assert.equal(got.env.CLAUDECODE, null, "a parent session's markers are dropped")
  assert.equal(got.env.CLAUDE_CODE_ENTRYPOINT, null)
  assert.equal(got.env.ANTHROPIC_BASE_URL, w.base, "the normal route to the gateway")
  assert.equal(got.env.ANTHROPIC_AUTH_TOKEN, "gateway-doorman")
  assert.equal(got.env.HTTPS_PROXY, null, "no proxy of xray's own")
  assert.match(got.env.ANTHROPIC_CUSTOM_HEADERS, /^x-brain-origin: xray\nx-brain-run: xray-[0-9a-z]+-[0-9a-f]{6}\nx-brain-dry-run: 1$/)
  assert.deepEqual(w.upstream, ["/api/hello"], "a background call without the headers takes the normal path, as in every routed session")
  assert.equal(got.hello, 200)
  assert.doesNotMatch(r.out, /WARNING/, "and is not a model call, so no leak")
  assert.equal(got.side.content[0].text, "ok", "the non-streamed reply is a message too")

  const raw = await xray(["--json", "claude", "-p", "hello"], w.env, w.dir)
  const body = JSON.parse(raw.out) as Body
  assert.equal(body.model, "claude-opus-5-5")
  assert.equal(body.messages?.length, 2)
  assert.equal(w.hits(), 0)
})

test("xray: the gateway's canned reply reaches claude as one whole message", async () => {
  const w = await world()
  const r = await xray(["claude", "-p", "hello"], w.env, w.dir)
  assert.equal(r.code, 0, r.err)
  const main = w.report().main
  assert.equal(main.status, 200)
  assert.equal(main.type, "text/event-stream")
  assert.deepEqual(main.events, ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"])
  assert.match(main.id, /^msg_dryrun_/)
  assert.equal(main.text, "ok")
  assert.equal(main.stop, "end_turn")
})

/** A body cut the way Claude Code 2.1.28x sends one. */
function realisticBody(tag = "[tracer] which session did what: /tracer:trace <session> (a recap)"): Body {
  return {
    model: "claude-opus-5-5",
    system: [
      { type: "text", text: "x-anthropic-billing-header: cc_version=2.1.281; cc_entrypoint=sdk-cli;" },
      { type: "text", text: "You are Claude Code, Anthropic's official CLI for Claude, running within the Claude Agent SDK." },
      {
        type: "text",
        text:
          "\nYou are an interactive agent that helps users.\n\n# Harness\nTools run in a sandbox.\n```sh\n# not a heading\n```\n\n# Environment\n" +
          "Primary working directory: /x\n\n<reply-style>\nplain english\n</reply-style>",
      },
    ],
    tools: [
      { name: "Bash", description: "Executes a bash command.", input_schema: { type: "object", properties: { command: { type: "string" } } } },
      { name: "Read", description: "Reads a file.", input_schema: { type: "object" } },
      { name: "DeferredToolPlaceholder", description: "", input_schema: { type: "object" }, defer_loading: true },
    ],
    messages: [
      {
        role: "user",
        content: [
          {
            type: "text",
            text:
              "<system-reminder>\nCodebase and user instructions are shown below. Be sure to adhere to these instructions.\n\n" +
              "Contents of /Users/me/.claude/CLAUDE.md (user's private global instructions for all projects):\n\nUse uv.\n\n" +
              "Contents of /Users/me/repo/CLAUDE.md (project instructions, checked into the codebase):\n\n# ~/repo — the harness\n\n## Map\n| a | b |\n" +
              "The following rows are not a header here\n[x] nor is this\n</system-reminder>\n",
          },
          {
            type: "text",
            text:
              "<system-reminder>\nAs you answer the user's questions, you can use the following context:\n# gitStatus\nCurrent branch: main\n\n" +
              "IMPORTANT: this context may or may not be relevant to your tasks.\n</system-reminder>\n",
          },
          { type: "text", text: "hi" },
        ],
      },
      {
        role: "system",
        content: [
          {
            type: "text",
            text:
              `SessionStart hook additional context: ${tag}\n` +
              "[wikilinks] `[[name]]` anywhere is a markdown file.\n" +
              "[memory] a note is markdown + frontmatter;\n  scaffold it from _templates/<type>.md\n\n" +
              "# Environment\nYou have been invoked in the following environment:\n - Platform: darwin\n\n" +
              "The following deferred tools are now available via ToolSearch. Their schemas are NOT loaded:\nWebFetch\nmcp__GitHub__get_me\n\n" +
              "Available agent types for the Agent tool:\n- Explore: read-only search\n\n" +
              "The following skills are available for use with the Skill tool:\n\n- vault: how to read and write notes\n",
          },
        ],
      },
    ],
  }
}

test("xray: sections are cut at header lines and named by tag, heading or path — and put back together they are the block", () => {
  const body = realisticBody()
  const a = anatomy(body, [{ label: "appended prompt", text: "<reply-style>\nplain english\n</reply-style>\n" }])
  const labels = (part: string, where?: string) => a.sections.filter((s) => s.part === part && (!where || s.where === where)).map((s) => s.label)

  assert.deepEqual(labels("system"), ["billing header", "identity", '"You are an interactive agen…"', "# Harness", "# Environment", "appended prompt"])
  assert.deepEqual(labels("messages", "message 1"), ["/Users/me/.claude/CLAUDE.md", "/Users/me/repo/CLAUDE.md", "git status", '"hi"'], "a CLAUDE.md's own headings stay inside it")
  assert.deepEqual(labels("messages", "message 2 (system)"), ["[tracer]", "[wikilinks]", "[memory]", "# Environment", "deferred tools", "agent types", "skills"])
  assert.equal(a.sections.filter((s) => s.part === "tools" && !s.deferred).length, 2, "a defer_loading tool is not in context")
  assert.deepEqual(a.deferred, ["WebFetch", "mcp__GitHub__get_me"], "the placeholder is not a tool; the listed names are")
  assert.deepEqual(new Set(a.sections.map((s) => s.key)).size, a.sections.length, "every key is unique")

  // Nothing is lost or invented by the cut.
  for (const b of [...(body.system as { text: string }[]), ...body.messages!.flatMap((m) => m.content as { text: string }[])])
    assert.equal(splitSections(b.text, [{ label: "appended prompt", text: "<reply-style>\nplain english\n</reply-style>" }]).map((s) => s.text).join(""), b.text)
})

test("xray: the appended system prompt is read from the config dir the shim reads (configDir()/cc-shim), CC_SYSPROMPT_FILE wins", () => {
  const cfg = mkdtempSync(join(tmpdir(), "xray-cfg-"))
  mkdirSync(join(cfg, "cc-shim"))
  writeFileSync(join(cfg, "cc-shim", "system-prompt.md"), "from the config dir\n")
  const other = join(cfg, "other.md")
  writeFileSync(other, "from the override\n")
  const saved = { xdg: process.env.XDG_CONFIG_HOME, appdata: process.env.APPDATA, file: process.env.CC_SYSPROMPT_FILE }
  try {
    process.env.XDG_CONFIG_HOME = cfg // POSIX
    process.env.APPDATA = cfg // Windows
    delete process.env.CC_SYSPROMPT_FILE
    assert.deepEqual(pinsFor(["claude", "-p", "hi"]), [{ label: "appended prompt", text: "from the config dir\n" }])
    process.env.CC_SYSPROMPT_FILE = other
    assert.deepEqual(pinsFor(["claude", "-p", "hi"]), [{ label: "appended prompt", text: "from the override\n" }])
  } finally {
    for (const [k, v] of [["XDG_CONFIG_HOME", saved.xdg], ["APPDATA", saved.appdata], ["CC_SYSPROMPT_FILE", saved.file]] as const) {
      if (v === undefined) delete process.env[k]
      else process.env[k] = v
    }
  }
})

test("xray: -o then --vs shows only the changed [tag] section, as a line diff", async () => {
  const w = await world()
  const saved = await xray(["-o", "before", "claude", "-p", "hello"], w.env, w.dir)
  assert.equal(saved.code, 0, saved.err)
  assert.match(saved.out, /saved {5}.*data\/xray\/before\.json — run xray-/)
  const file = JSON.parse(readFileSync(join(w.dir, "data", "xray", "before.json"), "utf8")) as { v: number; body: string; run: string }
  assert.equal(file.v, 2)
  assert.match(file.body, /^[0-9a-f]{64}$/, "a hash, not the body")
  assert.ok(existsSync(join(w.dir, "data", "blobs", file.body.slice(0, 2), `${file.body}.json`)), "the body is in the gateway's blobs, at capture meta")

  const same = await xray(["--vs", "before", "claude", "-p", "hello"], w.env, w.dir)
  assert.match(same.out, /0 changed · 0 added · 0 removed/)
  assert.match(same.out, /nothing added, removed or changed/)

  const r = await xray(["--vs", "before", "claude", "-p", "hello"], { ...w.env, FAKE_TAG: "[tracer] which session did what: /tracer:trace · convo_drill" }, w.dir)
  assert.equal(r.code, 0, r.err)
  assert.match(r.out, /1 changed · 0 added · 0 removed/)
  assert.match(r.out, /^ {2}~ message 2 \(system\) › \[tracer\] /m)
  assert.match(r.out, /^ {4}− SessionStart hook additional context: \[tracer\] which session did what: \/tracer:trace$/m)
  assert.match(r.out, /^ {4}\+ SessionStart hook additional context: \[tracer\] which session did what: \/tracer:trace · convo_drill$/m)
  assert.doesNotMatch(r.out, /\[wikilinks\]|# Harness|Bash/, "unchanged sections are not shown")

  const missing = await xray(["--vs", "nope", "claude", "-p", "hello"], w.env, w.dir)
  assert.equal(missing.code, 66)
  assert.match(missing.err, /nothing saved as nope .* saved: before/)
})

test("xray: a person's session runs in a terminal, is killed at the catch, and the session it left is removed", async () => {
  const w = await world()
  const r = await xray(["claude", "--model", "opus"], { ...w.env, FAKE_STAY: "1" }, w.dir)
  assert.equal(r.code, 0, `${r.out}\n${r.err}`)
  assert.match(r.out, /^ {2}person session · claude-opus-5-5 · 2 model calls dry-run \(1 side\) · no prompt given — sent "hi" · none left the gateway$/m)
  const got = w.report()
  assert.deepEqual(got.argv, ["hi", "--model", "opus"], "a placeholder first prompt, ahead of the flags; no --no-session-persistence")
  assert.ok(!alive(got.pid), "the session was killed at the catch")
  const c = join(w.dir, ".claude")
  const sid = "0a1b2c3d-1111-4222-8333-444455556666"
  for (const p of [join("projects", "-scratch"), join("session-env", sid), join("teams", "session-0a1b2c3d"), join("sessions", `${got.pid}.json`), join("sessions", `${got.pid}.abc.key`)])
    assert.ok(!existsSync(join(c, p)), `${p} is gone`)
  assert.ok(existsSync(join(c, "sessions", "1.json")), "another session's registry entry stays")
  assert.match(r.out, /removed {3}what the session left in .*\.claude: projects\/-scratch\/0a1b2c3d-1111-4222-8333-444455556666\.jsonl · /)
})

test("xray: a launch that sends nothing is reported with the likely cause and its last lines", async () => {
  const w = await world()
  const r = await xray(["claude"], { ...w.env, FAKE_HANG: "1", [envName("XRAY_TIMEOUT")]: "2" }, w.dir)
  assert.equal(r.code, 1)
  assert.match(r.err, /xray: no prompt caught in 2 s — it is waiting on the folder-trust dialog/)
  assert.match(r.err, /last lines from claude:\n {4}Quick safety check: Do you trust the files in this folder\?/)
  assert.ok(!alive(w.report().pid), "the hung launch was killed")
})

test("xray: the plan for a command line — prompt or not, where the placeholder goes, what is refused", () => {
  const ok = (cmd: string[]) => {
    const p = plan(cmd)
    assert.ok(!("error" in p), JSON.stringify(p))
    return p as Exclude<ReturnType<typeof plan>, { error: string }>
  }
  assert.deepEqual(ok(["claude", "--plugin-dir", "./x"]).argv, ["hi", "--plugin-dir", "./x"])
  assert.equal(ok(["claude", "--plugin-dir", "./x"]).program, false)
  assert.deepEqual(ok(["claude", "-p", "--model", "opus"]).argv, ["hi", "-p", "--model", "opus", "--no-session-persistence"])
  assert.deepEqual(ok(["claude", "-p", "hi", "--no-session-persistence"]).argv, ["-p", "hi", "--no-session-persistence"])
  assert.deepEqual(ok(["claude", "-p", "--", "-x"]).argv, ["-p", "--no-session-persistence", "--", "-x"], "never past a `--`, where it would be prompt text")
  assert.equal(ok(["claude", "--add-dir", "a", "b"]).placeholder, "hi", "a variadic flag takes the words after it")
  assert.equal(ok(["claude", "--settings={}", "do it"]).placeholder, null)
  assert.deepEqual(ok(["claude", "--settings", "s.json", "-p", "x"]).settings, ["s.json"])
  assert.match((plan(["claude", "mcp", "list"]) as { error: string }).error, /subcommand/)
  assert.match((plan(["claude", "--version"]) as { error: string }).error, /sends no prompt/)
  assert.match((plan(["claude", "--resume", "abc"]) as { error: string }).error, /--fork-session/)
  assert.ok(!("error" in plan(["claude", "-p", "--resume", "abc", "x"])), "-p does not persist, so a resume is safe")
  // The cc-shim's account flags, in every spelling it reads, go; a literal after `--` stays.
  const acct = ok(["claude", "--account", "foo", "-p", "--acct=bar", "--prefer", "baz", "--account", "--model", "opus", "--", "--account", "x"])
  assert.deepEqual(acct.dropped, ["--account", "foo", "--acct=bar", "--prefer", "baz", "--account"])
  assert.deepEqual(acct.argv, ["-p", "--model", "opus", "--no-session-persistence", "--", "--account", "x"])
  assert.equal(ok(["claude", "--prefer", "work"]).placeholder, "hi", "an account name is not a prompt")
})

test("xray: a settings file that routes elsewhere is refused before anything launches", async () => {
  const w = await world()
  mkdirSync(join(w.dir, ".claude"), { recursive: true })
  writeFileSync(join(w.dir, ".claude", "settings.json"), JSON.stringify({ env: { ANTHROPIC_BASE_URL: "https://api.anthropic.com" } }))
  const r = await xray(["claude", "-p", "hello"], w.env, w.dir)
  assert.equal(r.code, 1)
  assert.match(r.err, /refusing: this launch would not reach the gateway — .*settings\.json sets env\.ANTHROPIC_BASE_URL/)
  assert.ok(!existsSync(join(w.dir, "report.json")), "claude was never started")
})

test("xray: a gateway that is off launches nothing, and says how to turn it on", async () => {
  const w = await world()
  const free = http.createServer()
  await new Promise<void>((r) => free.listen(0, "127.0.0.1", r))
  const port = (free.address() as { port: number }).port
  await new Promise<void>((r) => free.close(() => r()))
  const r = await xray(["claude", "-p", "hello"], { ...w.env, [envName("GATEWAY_PORT")]: String(port) }, w.dir)
  assert.equal(r.code, 1)
  assert.match(r.err, new RegExp(`xray: the gateway is off — nothing answers on 127\\.0\\.0\\.1:${port}.*\`${gw("enable")}\` turns it on`))
  assert.ok(!existsSync(join(w.dir, "report.json")), "claude was never started")
})

test("xray as a verb of the CLI: the rest of argv is xray's, and it runs once", async () => {
  const w = await world()
  const viaCli = (argv: string[], env: NodeJS.ProcessEnv) =>
    new Promise<{ code: number | null; out: string; err: string }>((resolve) => {
      const c = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", fileURLToPath(new URL("../src/cli.ts", import.meta.url)), "xray", ...argv], { env, cwd: w.dir })
      let out = ""
      let err = ""
      c.stdout.on("data", (b: Buffer) => (out += b.toString()))
      c.stderr.on("data", (b: Buffer) => (err += b.toString()))
      c.on("exit", (code) => resolve({ code, out, err }))
    })
  const help = await viaCli(["--help"], w.env)
  assert.equal(help.code, 0, help.err)
  assert.equal(help.out, (await xray(["--help"], w.env, w.dir)).out)
  assert.equal(help.out.split(`usage: ${gw("xray")}`).length, 2, "the usage printed once")
  const free = http.createServer()
  await new Promise<void>((r) => free.listen(0, "127.0.0.1", r))
  const port = (free.address() as { port: number }).port
  await new Promise<void>((r) => free.close(() => r()))
  const off = await viaCli(["claude", "-p", "hello"], { ...w.env, [envName("GATEWAY_PORT")]: String(port) })
  assert.equal(off.code, 1)
  assert.match(off.err, /^xray: the gateway is off/)
})

test("xray: a gateway too old to dry-run launches nothing — it would have sent the prompt", async () => {
  const w = await world()
  const older = http.createServer((_req, res) => {
    res.writeHead(200, { "content-type": "application/json" })
    res.end(JSON.stringify({ ok: true, port: 0, pid: 4242, accounts: 1 }))
  })
  await new Promise<void>((r) => older.listen(0, "127.0.0.1", r))
  try {
    const port = (older.address() as { port: number }).port
    const r = await xray(["claude", "-p", "hello"], { ...w.env, [envName("GATEWAY_PORT")]: String(port) }, w.dir)
    assert.equal(r.code, 1)
    assert.match(r.err, /the gateway on :\d+ \(pid 4242\) is a build without dry-run — it would send this prompt to Anthropic, so nothing was launched/)
    assert.ok(!existsSync(join(w.dir, "report.json")), "claude was never started")
  } finally {
    await new Promise<void>((r) => older.close(() => r()))
  }
})

test("xray: a model call of the session that went upstream anyway is reported, and fails the run", async () => {
  const w = await world()
  const r = await xray(["claude", "-p", "hello"], { ...w.env, FAKE_LEAK: "1" }, w.dir)
  assert.equal(w.report().leak, 200)
  assert.equal(w.hits(), 1, "the one call without the header took the normal path")
  assert.equal(r.code, 1)
  assert.match(r.out, /^ {2}WARNING {3}1 model call\(s\) of this session went upstream, not dry: rows \S+$/m)
})

test("xray: an account flag is taken out of the launch, and the header says why it does not matter", async () => {
  const w = await world()
  const r = await xray(["claude", "-p", "hello", "--account", "work"], w.env, w.dir)
  assert.equal(r.code, 0, `${r.out}\n${r.err}`)
  assert.deepEqual(w.report().argv, ["-p", "hello", "--no-session-persistence"])
  assert.match(r.out, /^ {2}program session · .* · --account work dropped: a dry run is answered before any account is chosen · none left the gateway$/m)
})

test("xray: the line diff keeps order and marks only what moved", () => {
  const d = lineDiff("a\nb\nc\nd", "a\nB\nc\nd\ne")
  assert.deepEqual(
    d.filter((e) => e.op === "-" || e.op === "+").map((e) => `${e.op}${e.line}`),
    ["-b", "+B", "+e"],
  )
})
