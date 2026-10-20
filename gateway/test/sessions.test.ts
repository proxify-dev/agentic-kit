/**
 * The sessions views: which sessions go through the gateway, and the agents in each.
 *
 * Born from a question the wire could not answer: "which agents are running in
 * which session, on which model?" Claude Code was sending the subagent's id and
 * its parent's on every call (x-claude-code-agent-id, x-claude-code-parent-agent-id)
 * and the gateway dropped both, so two Explore agents of one session were one blur.
 *
 * What these keep honest: the tree is built from those two ids; state follows
 * the newest turn's stop reason, never a side call; rows from before the ids fold
 * into one node per type, said so; a restart rebuilds from the day files without
 * counting a row twice; titles and task descriptions come from Claude Code's own
 * files, and an id that is not a file name never becomes a path; and through a
 * live gateway, the headers reach `/_gateway/sessions`.
 */
import assert from "node:assert/strict"
import http from "node:http"
import { appendFileSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import test from "node:test"

import { loadConfig } from "../src/config.ts"
import { readHints } from "../src/identity.ts"
import type { Hints } from "../src/identity.ts"
import { createServer } from "../src/server.ts"
import { Sessions } from "../src/sessions.ts"
import type { SessionDetail, SessionsList } from "../src/sessions.ts"
import type { TraceRow } from "../src/trace.ts"
import { Transcripts } from "../src/transcripts.ts"
import { sessionTreeBlock, sessionsBlock, treeRows } from "../src/tui.ts"

const plain = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, "")
const NOW = Date.parse("2026-10-20T12:00:00Z")
const MIN = 60_000
const S1 = "0a1b2c3d-1111-4222-8333-444455556666"
const S2 = "9f8e7d6c-1111-4222-8333-444455556666"

let n = 0
/** One finished call's trace row: `at` ms before NOW, as the agent the hints name. */
function row(session: string, at: number, h: Partial<Hints> = {}, o: Partial<TraceRow> = {}): TraceRow {
  n += 1
  const hints: Hints = {
    requestClass: "main", agentType: null, compaction: null, contextCompacted: null, tools: null, agentId: null, parentAgentId: null, ...h,
  }
  return {
    v: 2, id: `t-${n}`, ts: new Date(NOW - at).toISOString(), durMs: 1000, ttfbMs: 300, method: "POST", path: "/v1/messages",
    status: 200, account: "a@x", accountSource: "rotated", model: "claude-opus-5-5", stream: true,
    identity: { run: null, agent: null, item: null, project: "kit", origin: "cli", session, anonymous: false },
    hints, usage: { input: 10, output: 100, cacheRead: 1000, cacheCreation: 50 }, stopReason: "tool_use", messageId: null,
    blobs: { system: null, tools: null, body: null }, shape: { messages: 1, tools: 0, systemBlocks: 1, bytesIn: 1, bytesOut: 1 },
    policy: [], error: null, ...o,
  }
}
const sub = (agentId: string, agentType: string, parentAgentId: string | null = null): Partial<Hints> => ({ requestClass: "subagent", agentType, agentId, parentAgentId })
const scratch = (p: string) => mkdtempSync(join(tmpdir(), p))
const fresh = () => new Sessions(scratch("gw-sess-trace-"), new Transcripts(scratch("gw-sess-claude-")))

test("sessions: the two agent ids make a tree — two Explores stay two, a nested one hangs under its parent", () => {
  const s = fresh()
  s.record(row(S1, 10 * MIN))
  s.record(row(S1, 9 * MIN, sub("a1", "Explore"), { model: "claude-haiku-5-5" }))
  s.record(row(S1, 8 * MIN, sub("a2", "Explore"), { model: "claude-haiku-5-5", stopReason: "end_turn" }))
  s.record(row(S1, 7 * MIN, sub("f1", "fork")))
  s.record(row(S1, 6 * MIN, sub("e3", "Explore", "f1"), { model: "claude-sonnet-5-5" }))
  const d = s.detail(S1, NOW)!
  const by = Object.fromEntries(d.agents.map((a) => [a.id, a]))
  assert.deepEqual(Object.keys(by).sort(), ["a1", "a2", "e3", "f1", "main"])
  assert.equal(by.a1.parent, "main")
  assert.equal(by.e3.parent, "f1")
  assert.equal(by.e3.depth, 2)
  assert.equal(by.e3.model, "claude-sonnet-5-5")
  assert.deepEqual(d.session.agents.byType, { Explore: 3, fork: 1 })
  assert.equal(d.session.calls, 5)
  assert.equal(d.session.tokens.output, 500)
  assert.deepEqual(treeRows(d.agents).map((r) => `${r.prefix}${r.agent.id}`), ["main", "├─ a1", "├─ a2", "└─ f1", "   └─ e3"])
})

test("sessions: state follows the newest turn's stop — calling, tools, your turn, done, idle — and a side call changes none of it", () => {
  const s = fresh()
  s.record(row(S1, 2 * MIN, {}, { stopReason: "end_turn" }))
  s.record(row(S1, 1 * MIN, { requestClass: "auxiliary" }, { stopReason: "tool_use", model: "claude-haiku-5-5" }))
  s.record(row(S1, 3 * MIN, sub("a1", "Explore"), { stopReason: "end_turn" }))
  s.record(row(S1, 1 * MIN, sub("a2", "Explore"), { stopReason: "tool_use" }))
  s.record(row(S1, 40 * MIN, sub("a3", "Explore"), { stopReason: "tool_use" }))
  // A row that arrives late but began earlier does not overwrite the newer turn.
  s.record(row(S1, 30 * MIN, {}, { stopReason: "tool_use", model: "claude-sonnet-5-5" }))
  const state = () => Object.fromEntries(s.detail(S1, NOW)!.agents.map((a) => [a.id, a.state]))
  assert.deepEqual(state(), { main: "waiting", a1: "done", a2: "tools", a3: "idle" })
  const main = s.detail(S1, NOW)!.agents[0]
  assert.equal(main.model, "claude-opus-5-5", "the newest turn's model, not a side call's or an older row's")
  assert.equal(main.sideCalls, 1)
  assert.equal(s.list({ now: NOW }).sessions[0].state, "working", "a subagent running tools keeps the session working")

  s.begin("live-1", "/v1/messages", row(S1, 0).identity, sub("a1", "Explore") as Hints, NOW)
  assert.equal(state().a1, "calling")
  s.begin("side-1", "/v1/messages", row(S1, 0).identity, { ...(sub("a3", "Explore") as Hints), requestClass: "auxiliary" }, NOW)
  assert.equal(state().a3, "idle", "a title or classifier call in flight does not make its agent busy")
  s.end("live-1")
  assert.equal(state().a1, "done")
  assert.equal(s.list({ now: NOW + 2 * 3_600_000 }).sessions.length, 1, "an old session is idle, still listed in the window")
})

test("sessions: rows from before the ids fold into one node per type, said so", () => {
  const s = fresh()
  s.record(row(S1, 5 * MIN))
  s.record(row(S1, 4 * MIN, { requestClass: "subagent", agentType: "Explore" }))
  s.record(row(S1, 3 * MIN, { requestClass: "subagent", agentType: "Explore" }))
  s.record(row(S1, 2 * MIN, { requestClass: "workflow", agentType: "workflow-subagent" }))
  const agents = s.detail(S1, NOW)!.agents
  assert.deepEqual(agents.map((a) => [a.id, a.merged, a.calls]), [["main", false, 1], ["~subagent/Explore", true, 2], ["~workflow/workflow-subagent", true, 1]])
  const lines = sessionTreeBlock(s.detail(S1, NOW)!, { width: 140, rows: 20, selected: 1, now: NOW }).map(plain)
  assert.ok(lines.some((l) => l.includes("Explore  earlier calls, not told apart")))
  assert.ok(!lines.some((l) => l.includes("~subagent")), "the fold's own key is never shown")
})

test("sessions: a start rebuilds from the day files, a row seen live is not counted twice, and the window holds", () => {
  const dir = scratch("gw-sess-trace-")
  const live = row(S1, 2 * MIN)
  const day = join(dir, `${new Date(NOW).toISOString().slice(0, 10)}.ndjson`)
  writeFileSync(day, "")
  for (const r of [
    row(S1, 5 * MIN),
    live,
    row(S2, 30 * 3_600_000), // outside the window
    row(S1, 4 * MIN, {}, { path: "/v1/messages/count_tokens" }),
    row(S1, 3 * MIN, {}, { dryRun: true }),
  ]) appendFileSync(day, `${JSON.stringify(r)}\n`)
  appendFileSync(day, '{"half a row')
  const s = new Sessions(dir, new Transcripts(scratch("gw-sess-claude-")))
  s.record(live)
  const l = s.list({ now: NOW })
  assert.equal(l.counts.sessions, 1)
  assert.equal(l.sessions[0].calls, 2, "two model calls: count_tokens and a dry run are not turns, the live row counted once")
})

test("sessions: one-shot jobs are counted, listed only when asked; n caps the rows; a prefix opens a session", () => {
  const s = fresh()
  for (let i = 0; i < 3; i++) s.record(row(S1, (i + 1) * MIN))
  s.record(row(S2, 10 * MIN, {}, { stopReason: "end_turn" }))
  s.record(row("0a1b2c3d-2222-4222-8333-444455556666", 20 * MIN, {}, { stopReason: "end_turn" }))
  const l = s.list({ now: NOW })
  assert.equal(l.counts.oneShots, 2)
  assert.deepEqual(l.sessions.map((x) => x.id), [S1])
  assert.equal(s.list({ now: NOW, all: true }).sessions.length, 3)
  assert.equal(s.list({ now: NOW, all: true, n: 2 }).sessions.length, 2)
  assert.equal(s.detail("9f8e7d6c", NOW)?.session.id, S2)
  assert.equal(s.detail("0a1b2c3d", NOW), null, "a prefix two sessions share opens neither")
  const lines = sessionsBlock(l, { width: 140, rows: 10, selected: 0, all: false, now: NOW }).map(plain)
  assert.ok(lines[0].includes("2 one-shot jobs hidden — o shows them"))
  assert.ok(lines[2].startsWith(">") && lines[2].includes("kit  0a1b2c3d"), "an untitled session reads as its project and id")
})

test("transcripts: the newest title — a /rename over the ai-title — read again only as the file grows; a subagent's task from its meta file", () => {
  const claude = scratch("gw-sess-claude-")
  const proj = join(claude, "projects", "-Users-x-kit")
  mkdirSync(join(proj, S1, "subagents"), { recursive: true })
  const file = join(proj, `${S1}.jsonl`)
  writeFileSync(file, `${JSON.stringify({ type: "user", message: "hi" })}\n${JSON.stringify({ type: "ai-title", aiTitle: "First title" })}\n`)
  const t = new Transcripts(claude)
  assert.equal(t.title(S1, "kit"), "First title")
  appendFileSync(file, `${JSON.stringify({ type: "ai-title", aiTitle: "Second title" })}\n`)
  assert.equal(t.title(S1, "kit"), "Second title")
  appendFileSync(file, `${JSON.stringify({ type: "custom-title", customTitle: "Named by hand" })}\n${JSON.stringify({ type: "ai-title", aiTitle: "Third" })}\n`)
  assert.equal(t.title(S1, "kit"), "Named by hand", "a /rename outranks every ai-title after it")

  writeFileSync(join(proj, S1, "subagents", "agent-a1.meta.json"), JSON.stringify({ agentType: "Explore", description: "Find the readers", finished: false }))
  assert.deepEqual(t.subagent(S1, "a1"), { agentType: "Explore", description: "Find the readers", model: null, finished: false })
  writeFileSync(join(proj, S1, "subagents", "agent-a1.meta.json"), JSON.stringify({ agentType: "Explore", description: "Find the readers", finished: true }))
  assert.equal(t.subagent(S1, "a1")?.finished, true, "an unfinished one is read again")
  assert.equal(t.subagent(S1, "../../etc"), null)
  assert.equal(t.subagent(S1, "a.b"), null, "an id that is not a file name is never a path")
  assert.equal(t.title("not-a-session"), null)

  const s = new Sessions(scratch("gw-sess-trace-"), t)
  s.record(row(S1, 2 * MIN, sub("a1", "Explore"), { stopReason: "tool_use" }))
  const a1 = s.detail(S1, NOW)!.agents.find((a) => a.id === "a1")!
  assert.equal(a1.description, "Find the readers")
  assert.equal(a1.state, "done", "its meta says finished: the wire's tool_use is not the last word")

  // A user-defined agent reaches the wire as `custom` (seen live: a user's own Explore); its meta file names it.
  writeFileSync(join(proj, S1, "subagents", "agent-c1.meta.json"), JSON.stringify({ agentType: "code-reviewer", description: "Review the diff", finished: false }))
  s.record(row(S1, 1 * MIN, sub("c1", "custom")))
  assert.equal(s.detail(S1, NOW)!.agents.find((a) => a.id === "c1")!.type, "code-reviewer")
  assert.deepEqual(s.list({ now: NOW }).sessions[0].agents.byType, { Explore: 1, "code-reviewer": 1 })
})

test("hints: the agent ids are kept when they look like ids, dropped when not", () => {
  const h = readHints({ "x-claude-code-agent-id": "a0d710fde6090ef56", "x-claude-code-parent-agent-id": "lead@team-1" })!
  assert.equal(h.agentId, "a0d710fde6090ef56")
  assert.equal(h.parentAgentId, "lead@team-1")
  assert.equal(readHints({ "x-claude-code-agent-id": "../../x y" }), null)
})

test("through a live gateway, the agent headers reach /_gateway/sessions as a tree", async () => {
  const dir = scratch("gw-sess-live-")
  const upstream = http.createServer((req, res) => {
    req.resume()
    res.writeHead(200, { "content-type": "application/json" })
    res.end(JSON.stringify({ id: "msg_x", type: "message", content: [], usage: { input_tokens: 5, output_tokens: 7 }, stop_reason: "tool_use" }))
  })
  await new Promise<void>((r) => upstream.listen(0, "127.0.0.1", r))
  const accounts = join(dir, "accounts.json")
  writeFileSync(
    accounts,
    JSON.stringify({ accounts: [{ name: "a", type: "oauth", accountUuid: "u-a", orgUuid: "o-a", accessToken: "t", refreshToken: "r", expiresAt: Date.now() + 86_400_000 }] }),
  )
  const gw = await createServer(
    loadConfig({ port: 0, dataDir: dir, accountsFile: accounts, policy: [], upstreamUrl: `http://127.0.0.1:${(upstream.address() as { port: number }).port}` }),
    { claudeConfigDir: scratch("gw-sess-claude-") },
  )
  try {
    await new Promise<void>((r) => gw.server.listen(0, "127.0.0.1", r))
    const base = `http://127.0.0.1:${(gw.server.address() as { port: number }).port}`
    const call = (headers: Record<string, string>) =>
      fetch(`${base}/v1/messages?beta=true`, {
        method: "POST",
        headers: { "content-type": "application/json", authorization: "Bearer gateway-doorman", "x-claude-code-session-id": S1, ...headers },
        body: JSON.stringify({ model: "claude-opus-5-5", max_tokens: 1, stream: false, messages: [{ role: "user", content: JSON.stringify(headers) }] }),
      }).then((r) => assert.equal(r.status, 200))
    await call({ "x-claude-code-request-class": "main" })
    await call({ "x-claude-code-request-class": "subagent", "x-claude-code-agent-type": "fork", "x-claude-code-agent-id": "f1" })
    await call({ "x-claude-code-request-class": "subagent", "x-claude-code-agent-type": "Explore", "x-claude-code-agent-id": "e1", "x-claude-code-parent-agent-id": "f1" })

    const list = (await (await fetch(`${base}/_gateway/sessions`)).json()) as SessionsList
    assert.equal(list.sessions[0].id, S1)
    assert.equal(list.sessions[0].state, "working")
    const d = (await (await fetch(`${base}/_gateway/sessions/${S1.slice(0, 8)}`)).json()) as SessionDetail
    assert.deepEqual(d.agents.map((a) => [a.id, a.parent, a.kind, a.type]), [["main", null, "main", null], ["f1", "main", "subagent", "fork"], ["e1", "f1", "subagent", "Explore"]])
    assert.equal(d.agents[2].tokens.output, 7)
    assert.equal((await fetch(`${base}/_gateway/sessions/ffffffff`)).status, 404)
  } finally {
    await gw.close()
    await new Promise<void>((r) => upstream.close(() => r()))
  }
})

test("tui: a sessions view never takes more lines than it is given, so the key line stays on screen", () => {
  const s = fresh()
  for (let i = 0; i < 60; i++) {
    const id = `0a1b2c3d-1111-4222-8333-${String(i).padStart(12, "0")}`
    for (let k = 0; k < 3; k++) s.record(row(id, (i + 1) * MIN + k))
  }
  for (let i = 0; i < 40; i++) s.record(row(S2, 2 * MIN, sub(`a${i}`, "Explore"), { stopReason: "end_turn" }))
  for (const rows of [10, 20, 36]) {
    assert.ok(sessionsBlock(s.list({ now: NOW }), { width: 120, rows, selected: 30, all: false, now: NOW }).length <= rows, `list in ${rows}`)
    for (const selected of [0, 39])
      assert.ok(sessionTreeBlock(s.detail(S2, NOW)!, { width: 120, rows, selected, now: NOW }).length <= rows, `tree in ${rows}, agent ${selected}`)
  }
})
