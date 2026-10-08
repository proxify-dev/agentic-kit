process.removeAllListeners('warning');{const i=process.argv.indexOf('--root');if(i>0&&process.argv[i+1])process.env.AK_PATH=process.argv[i+1];}

// src/brand.ts
import { spawn } from "node:child_process";
import { homedir, tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";
var DISPLAY_NAME = "agentic kit";
var CLI = "ak";
var ENV_PREFIX = "AK_";
var HOME_DIR = ".ak";
function env(name, fallback) {
  return process.env[ENV_PREFIX + name] ?? fallback;
}
function envName(name) {
  return ENV_PREFIX + name;
}
function cmd(verb = "") {
  return `${CLI} ${verb}`.trim();
}
function envPath(name) {
  const v = process.env[name];
  if (!v) return void 0;
  return v === "~" || v.startsWith("~/") || v.startsWith("~\\") ? join(homedir(), v.slice(1)) : v;
}
function claudeHome() {
  return envPath("CLAUDE_CONFIG_DIR") ?? join(homedir(), ".claude");
}
function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";
  }
}

// src/preflight.ts
var NODE_FLOOR = [22, 13];
var parts = process.versions.node.split(".").map((n) => Number.parseInt(n, 10));
var major = parts[0] ?? 0;
var minor = parts[1] ?? 0;
if (major < NODE_FLOOR[0] || major === NODE_FLOOR[0] && minor < NODE_FLOOR[1]) {
  process.stderr.write(
    `[${DISPLAY_NAME}] node ${process.versions.node} is below the required ${NODE_FLOOR.join(".")} (node:sqlite) \u2014 the ${DISPLAY_NAME} tools cannot start
`
  );
  process.exit(1);
}
var at = process.argv.indexOf("--root");
if (at > 0 && process.argv[at + 1]) process.env[envName("PATH")] = process.argv[at + 1];

// bin/traces.ts
import { existsSync as existsSync6 } from "node:fs";
import { parseArgs } from "node:util";

// src/traces.ts
import { existsSync as existsSync4, mkdirSync as mkdirSync2, rmSync as rmSync2 } from "node:fs";
import { homedir as homedir4, setPriority } from "node:os";
import { DatabaseSync } from "node:sqlite";
import { join as join6 } from "node:path";

// src/paths.ts
import { dirname, join as join2, resolve } from "node:path";
import { existsSync, realpathSync } from "node:fs";
import { homedir as homedir2 } from "node:os";
var VAULT_MARKER = "vault-manifest.json";
var isVault = (d) => existsSync(join2(d, VAULT_MARKER));
var kitHome = () => env("HOME")?.trim() || env("PATH")?.trim() || join2(homedir2(), HOME_DIR);
var globalVault = () => env("PATH")?.trim() || join2(kitHome(), "vault");
var realOrRaw = (p) => {
  try {
    return realpathSync(p);
  } catch {
    return resolve(p);
  }
};
function scopes(cwd) {
  const pinned = env("PATH");
  if (pinned) return [pinned];
  const home = realOrRaw(globalVault());
  const kit = /* @__PURE__ */ new Set([home, realOrRaw(join2(homedir2(), HOME_DIR)), realOrRaw(join2(homedir2(), HOME_DIR, "vault"))]);
  const found = [];
  let d = realOrRaw(cwd ?? process.cwd());
  for (; ; ) {
    const nested = join2(d, HOME_DIR);
    if (isVault(nested)) {
      if (!kit.has(nested)) found.push(nested);
    } else if (isVault(d) && !kit.has(d)) found.push(d);
    const parent = dirname(d);
    if (parent === d) break;
    d = parent;
  }
  if (!found.includes(home)) found.push(home);
  return [...new Set(found)];
}
var brainRoot = (cwd) => scopes(cwd)[0];
var BRAIN = brainRoot();
var HOME_CLAUDE = claudeHome();
var KIT_HOME = kitHome();
var STORE_DIR = env("STORE")?.trim() || join2(KIT_HOME, "db");
var STORE_DB = join2(STORE_DIR, "brain.db");
var CACHE_DIR = join2(KIT_HOME, "cache");
var CLAUDE_MEM_DB = join2(homedir2(), ".claude-mem", "claude-mem.db");

// src/traces-blobs.ts
import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync as existsSync2,
  mkdirSync,
  openSync,
  readSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync
} from "node:fs";
import { homedir as homedir3 } from "node:os";
import { join as join3 } from "node:path";

// src/rename.ts
import { renameSync } from "node:fs";
function renameRetrySync(from, to, plat = process.platform, rename = renameSync) {
  for (let i = 0; ; i++) {
    try {
      return rename(from, to);
    } catch (err) {
      const code = err.code;
      if (plat !== "win32" || i >= 5 || code !== "EPERM" && code !== "EBUSY" && code !== "EACCES") throw err;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50);
    }
  }
}

// src/traces-blobs.ts
var SUMMARY_CAP = 160;
var LINE_CAP = 8 * 1024 * 1024;
var BLOB_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1e3;
var BLOB_MAX_BYTES = 200 * 1024 * 1024;
var SECRET_KEY = /key|token|secret|password|authorization|cookie/i;
var SECRET_SHAPE = /^(sk-|ghp_|gho_|ghs_|github_pat_|xox[abprs]-|AKIA|ASIA|eyJ[A-Za-z0-9_-]{10,}\.)/;
function entropy(s) {
  const freq = /* @__PURE__ */ new Map();
  for (const c of s) freq.set(c, (freq.get(c) ?? 0) + 1);
  let h = 0;
  for (const n of freq.values()) {
    const p = n / s.length;
    h -= p * Math.log2(p);
  }
  return h;
}
function looksSecret(s) {
  if (s.length <= 64) return false;
  if (SECRET_SHAPE.test(s)) return true;
  if (/\s/.test(s)) return false;
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) return false;
  if (s.includes("/") || s.includes("\\")) return false;
  return entropy(s) >= 3.6;
}
var NESTED_JSON_CAP = 256 * 1024;
function redact(v, depth = 0) {
  if (typeof v === "string") {
    const t = v.trim();
    if ((t.startsWith("{") || t.startsWith("[")) && t.length <= NESTED_JSON_CAP && depth < 8) {
      try {
        return JSON.stringify(redact(JSON.parse(t), depth + 1));
      } catch {
      }
    }
    return looksSecret(v) ? `[redacted:${v.length} chars]` : v;
  }
  if (v === null || typeof v !== "object") return v;
  if (depth >= 8) return "\u2026";
  if (Array.isArray(v)) return v.slice(0, 200).map((x) => redact(x, depth + 1));
  const out = {};
  for (const [k, val] of Object.entries(v)) {
    const numeric = typeof val === "number" || typeof val === "boolean";
    out[k] = SECRET_KEY.test(k) && !numeric ? "[redacted]" : redact(val, depth + 1);
  }
  return out;
}
var oneLine = (s) => s.replace(/\s+/g, " ").trim();
var clip = (s, n) => s.length > n ? `${s.slice(0, n - 1)}\u2026` : s;
function scalar(v) {
  if (typeof v === "string") return oneLine(v);
  if (v === null || v === void 0) return String(v);
  if (typeof v === "object") return clip(JSON.stringify(v), 40);
  return String(v);
}
function summarize(v) {
  let s;
  if (v === null || v === void 0) s = "";
  else if (typeof v === "string") s = oneLine(v);
  else if (Array.isArray(v)) s = v.map(scalar).join(", ");
  else if (typeof v === "object")
    s = `{${Object.entries(v).slice(0, 12).map(([k, val]) => `${k}:${scalar(val)}`).join(", ")}}`;
  else s = String(v);
  return s.length > SUMMARY_CAP ? `${s.slice(0, SUMMARY_CAP - 1)}\u2026` : s;
}
var stringField = (o, key) => typeof o[key] === "string" ? oneLine(o[key]) : "";
var tilde = (p) => {
  const home = homedir3();
  return p === home || p.startsWith(home + "/") ? "~" + p.slice(home.length) : p;
};
function stepLabel(tool, v) {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return "";
  const o = v;
  const line = (s) => clip(s, SUMMARY_CAP);
  const desc = stringField(o, "description");
  if (desc) return line(desc);
  const cmd2 = stringField(o, "command");
  if (cmd2) return line(`$ ${cmd2}`);
  for (const key of ["file_path", "notebook_path"]) {
    const p = stringField(o, key);
    if (p) return line(tilde(p));
  }
  const pattern = stringField(o, "pattern");
  if (pattern) {
    const where = stringField(o, "path");
    const shown = tool === "Grep" ? `'${pattern}'` : pattern;
    return line(shown + (where ? ` in ${tilde(where)}` : ""));
  }
  const skill = stringField(o, "skill");
  if (skill) {
    const args = stringField(o, "args");
    return line(skill + (args ? ` ${args}` : ""));
  }
  for (const key of ["url", "query", "subject", "prompt", "name", "title"]) {
    const t = stringField(o, key);
    if (t) return line(t);
  }
  return "";
}
var textOf = (v) => {
  if (!Array.isArray(v)) return null;
  for (const raw of v) {
    const b = raw ?? {};
    if (b.type === "text" && typeof b.text === "string") return b.text;
    if (b.type === "image" || b.type === "audio") return "media";
  }
  return null;
};
function builtinLine(o) {
  if (typeof o.stdout !== "string" && typeof o.stderr !== "string") return null;
  const out = String(o.stdout ?? "").trim();
  const err = String(o.stderr ?? "").trim();
  if (!out && !err) return "no output";
  return out ? out : `stderr \xB7 ${err}`;
}
function brainLine(o) {
  switch (o.kind) {
    case "refused": {
      const reason = (o.refusal ?? {}).reason;
      return `refused \xB7 ${oneLine(reason ?? "no reason given")}`;
    }
    case "recall":
      return `${o.rows?.length ?? 0} recalled \xB7 ${Number(o.tokens ?? 0)} tokens`;
    case "list":
      return `${o.rows?.length ?? 0} of ${Number(o.total ?? 0)}`;
    case "note":
      return String(o.stem ?? o.path ?? "note");
    case "object": {
      const obj = o.object ?? {};
      const what = obj.path ?? obj.stem;
      if (!what) return "written";
      return `${obj.kind === "proposal" ? "proposed" : "wrote"} ${what}`;
    }
    // The trace family answers on this server too, so its shapes belong here.
    case "traces":
      return `${o.rows?.length ?? 0} rows`;
    case "trace":
      return String((o.row ?? {}).tool ?? "trace");
    case "purged":
      return `${Number(o.removed ?? 0)} removed`;
    default:
      return null;
  }
}
function classifyOutput(v, depth = 0) {
  if (v === null || v === void 0) return { line: "", refused: false };
  if (depth > 3) return { line: summarize(redact(v)), refused: false };
  const text = textOf(v);
  if (text !== null) return classifyOutput(text, depth + 1);
  if (typeof v === "string") {
    const t = v.trim();
    if (t.startsWith("{") || t.startsWith("[")) {
      try {
        return classifyOutput(JSON.parse(t), depth + 1);
      } catch {
      }
    }
    return {
      line: clip(oneLine(String(redact(v))), SUMMARY_CAP),
      refused: false
    };
  }
  if (Array.isArray(v)) {
    if (v.length && v.every((r) => r && typeof r === "object"))
      return {
        line: `${v.length} ${v.length === 1 ? "row" : "rows"}`,
        refused: false
      };
    return { line: summarize(redact(v)), refused: false };
  }
  if (typeof v === "object") {
    const envelope = builtinLine(v);
    if (envelope !== null)
      return { line: clip(oneLine(envelope), SUMMARY_CAP), refused: false };
    const o = redact(v);
    const refusal = brainLine(o);
    if (refusal)
      return { line: clip(refusal, SUMMARY_CAP), refused: o.kind === "refused" };
    if (Array.isArray(o.content)) {
      const inner = classifyOutput(o.content, depth + 1);
      return {
        line: o.isError ? clip(`error \xB7 ${inner.line}`, SUMMARY_CAP) : inner.line,
        refused: inner.refused
      };
    }
    return { line: summarize(o), refused: false };
  }
  return { line: summarize(v), refused: false };
}
function splitTool(name) {
  if (!name.startsWith("mcp__")) return { server: "claude", tool: name };
  const rest = name.slice(5);
  const cut = rest.indexOf("__");
  if (cut < 0) return { server: "claude", tool: name };
  return { server: rest.slice(0, cut), tool: rest.slice(cut + 2) };
}
var blobDir = (dir2) => join3(dir2, "blobs");
function writeBlob(dir2, value) {
  try {
    const body = JSON.stringify(value ?? null);
    const hash = createHash("sha256").update(body).digest("hex");
    const folder = join3(blobDir(dir2), hash.slice(0, 2));
    const file = join3(folder, `${hash}.json`);
    if (!existsSync2(file)) {
      mkdirSync(folder, { recursive: true });
      const tmp = `${file}.${process.pid}.tmp`;
      writeFileSync(tmp, body, "utf8");
      renameRetrySync(tmp, file);
    }
    return `sha256:${hash}`;
  } catch (err) {
    console.error(`[traces] blob write failed: ${err.message}`);
    return null;
  }
}
function readLineAt(path, offset) {
  let fd = -1;
  try {
    fd = openSync(path, "r");
    const chunks = [];
    const buf = Buffer.alloc(64 * 1024);
    let pos = offset;
    let total = 0;
    for (; ; ) {
      const n = readSync(fd, buf, 0, buf.length, pos);
      if (n <= 0) break;
      const nl = buf.subarray(0, n).indexOf(10);
      if (nl >= 0) {
        chunks.push(Buffer.from(buf.subarray(0, nl)));
        break;
      }
      chunks.push(Buffer.from(buf.subarray(0, n)));
      pos += n;
      total += n;
      if (total > LINE_CAP) return null;
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return null;
  } finally {
    if (fd >= 0) closeSync(fd);
  }
}
function pickBlock(line, id, side) {
  const content = line?.message?.content;
  if (!Array.isArray(content)) return null;
  for (const b of content) {
    if (side === "input" && b.type === "tool_use" && b.id === id)
      return b.input ?? null;
    if (side === "output" && b.type === "tool_result" && b.tool_use_id === id)
      return b.content ?? null;
  }
  return null;
}
function readBlob(dir2, ref, id, side) {
  if (!ref) return null;
  try {
    if (ref.startsWith("sha256:")) {
      const hash = ref.slice(7);
      if (!/^[0-9a-f]{64}$/.test(hash)) return null;
      const file = join3(blobDir(dir2), hash.slice(0, 2), `${hash}.json`);
      if (!existsSync2(file)) return null;
      const size = statSync(file).size;
      const fd = openSync(file, "r");
      try {
        const buf = Buffer.alloc(Math.min(size, LINE_CAP));
        readSync(fd, buf, 0, buf.length, 0);
        return JSON.parse(buf.toString("utf8"));
      } finally {
        closeSync(fd);
      }
    }
    const cut = ref.lastIndexOf(":");
    if (cut < 0) return null;
    const offset = Number(ref.slice(cut + 1));
    if (!Number.isFinite(offset)) return null;
    return redact(pickBlock(readLineAt(ref.slice(0, cut), offset), id, side));
  } catch (err) {
    console.error(`[traces] blob read failed: ${err.message}`);
    return null;
  }
}

// src/traces-witness.ts
import { existsSync as existsSync3 } from "node:fs";
import { join as join5 } from "node:path";

// src/feed.ts
import { createServer } from "node:http";
var MAX_BODY = 5 * 1024 * 1024;
var PORT = Number(env("FEED_PORT")) || 4577;

// src/traces-walk.ts
import {
  closeSync as closeSync2,
  openSync as openSync2,
  readSync as readSync2,
  readFileSync,
  readdirSync as readdirSync2,
  statSync as statSync2
} from "node:fs";
import { basename, dirname as dirname2, join as join4, sep } from "node:path";
var ROOT = env("TRACES_PROJECTS") || join4(HOME_CLAUDE, "projects");
var BACKFILL_BYTES = Number(env("TRACES_BACKFILL_MB") ?? 256) * 1024 * 1024;
var TICK_BYTES = Number(env("TRACES_TICK_MB") ?? 16) * 1024 * 1024;
var RECENT_MS = 60 * 60 * 1e3;
var PAYLOAD_TRIM = 4096;
var CHUNK = 256 * 1024;
function jsonlUnder(dir2, depth) {
  if (depth > 4) return [];
  let names;
  try {
    names = readdirSync2(dir2);
  } catch {
    return [];
  }
  const out = [];
  for (const name of names) {
    const path = join4(dir2, name);
    if (name.endsWith(".jsonl")) out.push(path);
    else if (!name.includes(".")) out.push(...jsonlUnder(path, depth + 1));
  }
  return out;
}
function candidates() {
  const out = [];
  let projects;
  try {
    projects = readdirSync2(ROOT);
  } catch {
    return out;
  }
  for (const project of projects) {
    const dir2 = join4(ROOT, project);
    let names;
    try {
      names = readdirSync2(dir2);
    } catch {
      continue;
    }
    for (const name of names) {
      const path = join4(dir2, name);
      try {
        if (name.endsWith(".jsonl")) {
          const st = statSync2(path);
          out.push({ path, mtime: st.mtimeMs, size: st.size, sub: false });
          continue;
        }
        for (const f of jsonlUnder(join4(path, "subagents"), 0)) {
          const st = statSync2(f);
          out.push({ path: f, mtime: st.mtimeMs, size: st.size, sub: true });
        }
      } catch {
      }
    }
  }
  return out.sort((a, b) => Number(a.sub) - Number(b.sub) || b.mtime - a.mtime);
}
function parentSession(path) {
  const parts2 = path.split(sep);
  const at2 = parts2.lastIndexOf("subagents");
  return at2 > 0 ? parts2[at2 - 1] : basename(dirname2(dirname2(path)));
}
function subContext(path, mark) {
  let agent = mark?.agentName ?? null;
  let parent = mark?.parentUse ?? null;
  let description = "";
  try {
    const meta = JSON.parse(
      readFileSync(path.replace(/\.jsonl$/, ".meta.json"), "utf8")
    );
    description = String(meta.description ?? "");
    agent ??= String(meta.name ?? meta.agentType ?? "") || null;
  } catch {
  }
  if (!parent && description) {
    const first = firstTimestamp(path);
    parent = findAgentCall(
      parentSession(path),
      description,
      first ?? "9999"
    );
  }
  return { agent, parent, description };
}
function firstTimestamp(path) {
  let fd = -1;
  try {
    fd = openSync2(path, "r");
    const buf = Buffer.alloc(64 * 1024);
    const n = readSync2(fd, buf, 0, buf.length, 0);
    for (const raw of buf.subarray(0, n).toString("utf8").split("\n")) {
      if (!raw.trim()) continue;
      const ts = JSON.parse(raw).timestamp;
      if (typeof ts === "string") return ts;
    }
    return null;
  } catch {
    return null;
  } finally {
    if (fd >= 0) closeSync2(fd);
  }
}
var trim = (v) => typeof v === "string" && v.length > PAYLOAD_TRIM ? v.slice(0, PAYLOAD_TRIM) : v;
var bytesOf = (v) => {
  try {
    return Buffer.byteLength(
      typeof v === "string" ? v : JSON.stringify(v ?? null)
    );
  } catch {
    return 0;
  }
};
var DENIED = /The user doesn't want to proceed|haven't granted it yet|requested permissions to use/;
function outcomeOf(b) {
  if (!b.is_error) return { outcome: "ok" };
  const text = typeof b.content === "string" ? b.content : JSON.stringify(b.content ?? "");
  const message = text.replace(/\s+/g, " ").slice(0, 400);
  return DENIED.test(text) ? { outcome: "denied", error: message } : { outcome: "error", error: message };
}
function handle(line, offset, path, ctx) {
  const ts = typeof line.timestamp === "string" ? line.timestamp : (/* @__PURE__ */ new Date()).toISOString();
  const session = typeof line.sessionId === "string" ? line.sessionId : null;
  const prompt = typeof line.promptId === "string" ? line.promptId : null;
  const content = line.message?.content;
  if (!Array.isArray(content)) return;
  if (line.type === "assistant") {
    for (const b of content) {
      if (b.type !== "tool_use" || !b.id || !b.name) continue;
      const { server, tool } = splitTool(b.name);
      record({
        id: b.id,
        witness: "transcript",
        ts,
        server,
        tool,
        origin: "session",
        parent: ctx?.parent ?? null,
        identity: {
          session,
          prompt,
          cwd: typeof line.cwd === "string" ? line.cwd : null,
          agent: ctx?.agent ?? null
        },
        input: trim(b.input),
        bytes: { in: bytesOf(b.input) },
        blobs: { input: `${path}:${offset}` },
        file: path
      });
      if (server === "claude" && (tool === "Agent" || tool === "Task") && session)
        noteAgentCall({
          id: b.id,
          session,
          ts,
          description: String(b.input?.description ?? "")
        });
    }
    return;
  }
  if (line.type !== "user") return;
  for (const b of content) {
    if (b.type !== "tool_result" || !b.tool_use_id) continue;
    const { outcome, error } = outcomeOf(b);
    record({
      id: b.tool_use_id,
      witness: "transcript",
      endTs: ts,
      outcome,
      ...error ? { error } : {},
      identity: { session, prompt },
      output: trim(b.content),
      bytes: { out: bytesOf(b.content) },
      blobs: { output: `${path}:${offset}` },
      file: path
    });
  }
}
function walkFile(path, known, limit = {}) {
  let fd = -1;
  try {
    const st = statSync2(path);
    const mark = known === void 0 ? fileMark(path) : known;
    const pos = mark && mark.size <= st.size ? mark.pos : 0;
    if (pos >= st.size) {
      if (!mark || mark.mtime !== st.mtimeMs || mark.size !== st.size)
        saveFileMark({
          path,
          pos,
          mtime: st.mtimeMs,
          size: st.size,
          parentUse: mark?.parentUse ?? null,
          agentName: mark?.agentName ?? null
        });
      return 0;
    }
    const sub = path.split(sep).includes("subagents");
    const ctx = sub ? subContext(path, mark) : null;
    const markAt = (at2, size) => ({
      path,
      pos: at2,
      mtime: st.mtimeMs,
      size,
      parentUse: ctx?.parent ?? null,
      agentName: ctx?.agent ?? null
    });
    fd = openSync2(path, "r");
    const buf = Buffer.alloc(CHUNK);
    let carry = Buffer.alloc(0);
    let lineStart = pos;
    let saved = pos;
    let consumed = 0;
    let stopped = false;
    for (; ; ) {
      const n = readSync2(fd, buf, 0, buf.length, pos + consumed);
      if (n <= 0) break;
      let chunk = Buffer.concat([carry, buf.subarray(0, n)]);
      consumed += n;
      const found = [];
      for (; ; ) {
        const nl = chunk.indexOf(10);
        if (nl < 0) break;
        const raw = chunk.subarray(0, nl);
        if (raw.includes('"tool_use"') || raw.includes('"tool_result"'))
          found.push([raw.toString("utf8"), lineStart]);
        lineStart += raw.length + 1;
        chunk = chunk.subarray(nl + 1);
      }
      carry = chunk;
      if (found.length || lineStart > saved) {
        const at2 = lineStart;
        batch(() => {
          for (const [text, off] of found) {
            try {
              handle(JSON.parse(text), off, path, ctx);
            } catch {
            }
          }
          if (at2 > saved) saveFileMark(markAt(at2, at2));
        });
        saved = at2;
      }
      const spent = lineStart - pos;
      if (spent > 0 && (limit.bytes !== void 0 && spent >= limit.bytes || limit.deadline !== void 0 && Date.now() >= limit.deadline)) {
        stopped = true;
        break;
      }
    }
    if (!stopped) saveFileMark(markAt(lineStart, st.size));
    if (ctx?.parent) adoptParent(path, ctx.parent);
    return lineStart - pos;
  } catch (err) {
    console.error(`[traces] walk failed for ${path}: ${err.message}`);
    return 0;
  } finally {
    if (fd >= 0) closeSync2(fd);
  }
}
async function pass(budget, opts = {}) {
  let files = 0;
  let bytes = 0;
  const marks = fileMarks();
  const since = opts.recent ? Date.now() - RECENT_MS : 0;
  for (const c of candidates()) {
    if (bytes >= budget) break;
    if (opts.deadline && Date.now() >= opts.deadline) break;
    if (c.mtime < since) continue;
    const mark = marks.get(c.path) ?? null;
    if (mark && mark.size === c.size && mark.mtime === c.mtime) continue;
    const got = walkFile(c.path, mark, {
      bytes: budget - bytes,
      deadline: opts.deadline
    });
    if (got > 0) {
      files++;
      bytes += got;
      await new Promise((r) => setImmediate(r));
    }
  }
  return { files, bytes };
}

// src/traces.ts
var KIT_TRACES = join6(STORE_DIR, "traces");
var DIR = env("TRACES_DIR") || (existsSync4(KIT_TRACES) ? KIT_TRACES : join6(HOME_CLAUDE, "brain-traces"));
var DEFAULT_LIMIT = 50;
var SCHEMA_VERSION = 5;
var MIGRATE_CHUNK = 400;
var SCHEMA = `
  CREATE TABLE IF NOT EXISTS spans (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    parent     TEXT,
    ts         TEXT NOT NULL,
    durMs      INTEGER,
    server     TEXT NOT NULL,
    tool       TEXT NOT NULL,
    origin     TEXT NOT NULL,
    identity   TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    summary    TEXT NOT NULL,
    blobs      TEXT NOT NULL,
    shape      TEXT NOT NULL,
    witnesses  TEXT NOT NULL,
    policy     TEXT,
    ledger     TEXT,
    error      TEXT,
    session    TEXT,
    file       TEXT,
    sumv       INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX IF NOT EXISTS spans_ts      ON spans(ts DESC);
  CREATE INDEX IF NOT EXISTS spans_server  ON spans(server);
  CREATE INDEX IF NOT EXISTS spans_tool    ON spans(tool);
  CREATE INDEX IF NOT EXISTS spans_session ON spans(session);
  CREATE INDEX IF NOT EXISTS spans_outcome ON spans(outcome);
  CREATE INDEX IF NOT EXISTS spans_file    ON spans(file);
  CREATE INDEX IF NOT EXISTS spans_sumv    ON spans(sumv);

  CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    pos        INTEGER NOT NULL,
    mtime      INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    parentUse  TEXT,
    agentName  TEXT
  );

  CREATE TABLE IF NOT EXISTS agent_calls (
    id          TEXT PRIMARY KEY,
    session     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    description TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS agent_calls_session ON agent_calls(session, ts DESC);
`;
var db = null;
var degraded = false;
var notify = null;
var inTxn = false;
var strict = false;
function open() {
  if (db) return db;
  if (degraded) return null;
  let conn = null;
  try {
    mkdirSync2(DIR, { recursive: true });
    conn = new DatabaseSync(join6(DIR, "traces.db"));
    conn.exec("PRAGMA busy_timeout = 3000");
    conn.exec("PRAGMA journal_mode = WAL");
    const older = conn.prepare(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'spans'"
    ).get();
    if (older) {
      const columns = conn.prepare("PRAGMA table_info(spans)").all();
      if (!columns.some((c) => c.name === "sumv"))
        conn.exec(
          "ALTER TABLE spans ADD COLUMN sumv INTEGER NOT NULL DEFAULT 0"
        );
    }
    conn.exec(SCHEMA);
    const stamped = conn.prepare("PRAGMA user_version").get();
    if (Number(stamped?.user_version) !== SCHEMA_VERSION)
      conn.exec(`PRAGMA user_version = ${SCHEMA_VERSION}`);
    db = conn;
    return db;
  } catch (err) {
    try {
      conn?.close();
    } catch {
    }
    if (isLocked(err)) {
      console.error(`[traces] store busy, not opened: ${err.message}`);
      return null;
    }
    degraded = true;
    console.error(
      `[traces] store unavailable, running degraded: ${err.message}`
    );
    return null;
  }
}
function openReadOnly() {
  if (db) return db;
  if (!existsSync4(dbFile())) return null;
  try {
    const conn = new DatabaseSync(dbFile(), { readOnly: true });
    conn.exec("PRAGMA busy_timeout = 3000");
    const spans = conn.prepare(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'spans'"
    ).get();
    if (!spans) {
      conn.close();
      return null;
    }
    db = conn;
    return db;
  } catch (err) {
    console.error(`[traces] store unreadable: ${err.message}`);
    return null;
  }
}
var dir = () => DIR;
var dbFile = () => join6(DIR, "traces.db");
function close() {
  try {
    db?.close();
  } catch {
  }
  db = null;
  degraded = false;
}
var parse = (s, fallback) => {
  try {
    return JSON.parse(String(s));
  } catch {
    return fallback;
  }
};
var EMPTY_IDENTITY = {
  session: null,
  prompt: null,
  cwd: null,
  agent: null,
  run: null,
  item: null
};
function hydrate(r) {
  const row = {
    id: String(r.id),
    kind: "tool",
    parent: r.parent ?? null,
    ts: String(r.ts),
    durMs: r.durMs === null || r.durMs === void 0 ? null : Number(r.durMs),
    server: String(r.server),
    tool: String(r.tool),
    origin: String(r.origin),
    identity: parse(r.identity, { ...EMPTY_IDENTITY }),
    outcome: String(r.outcome),
    summary: parse(r.summary, { input: "", output: "" }),
    blobs: parse(r.blobs, { input: null, output: null }),
    shape: parse(r.shape, { bytesIn: 0, bytesOut: 0 }),
    witnesses: parse(r.witnesses, [])
  };
  if (r.policy) row.policy = String(r.policy);
  if (r.ledger) row.ledger = String(r.ledger);
  if (r.error) row.error = String(r.error);
  return row;
}
var fill = (current, next) => current === null || current === void 0 || current === "" ? next ?? null : current;
function planChunk(rows, read = readBlob) {
  const out = [];
  for (const r of rows) {
    const id = String(r.id);
    const refs = parse(r.blobs, {
      input: null,
      output: null
    });
    const was = parse(r.summary, { input: "", output: "" });
    const next = { ...was };
    let outcome = String(r.outcome);
    const inPayload = read(DIR, refs.input, id, "input");
    if (inPayload !== null) {
      const safe = redact(inPayload);
      next.input = summarize(safe);
      next.label = stepLabel(String(r.tool), safe);
    }
    const outPayload = read(DIR, refs.output, id, "output");
    if (outPayload !== null) {
      const line = classifyOutput(outPayload);
      next.output = line.line;
      if (line.refused && (outcome === "error" || outcome === "ok"))
        outcome = "refused";
    }
    out.push({ id, summary: JSON.stringify(next), outcome });
  }
  return out;
}
function migrateChunk(conn, read = readBlob) {
  const all = conn.prepare(
    `SELECT id, tool, blobs, summary, outcome FROM spans WHERE sumv < ? LIMIT ${MIGRATE_CHUNK}`
  ).all(SCHEMA_VERSION);
  if (!all.length) return 0;
  const planned = planChunk(all, read);
  const update = conn.prepare(
    "UPDATE spans SET summary = ?, outcome = ?, sumv = ? WHERE id = ?"
  );
  conn.exec("BEGIN IMMEDIATE");
  try {
    for (const p of planned)
      update.run(p.summary, p.outcome, SCHEMA_VERSION, p.id);
    conn.exec("COMMIT");
  } catch (err) {
    conn.exec("ROLLBACK");
    throw err;
  }
  return all.length;
}
var migrating = false;
async function resummarize() {
  const t0 = Date.now();
  let rows = 0;
  if (migrating) return { rows, ms: 0 };
  migrating = true;
  try {
    for (; ; ) {
      const conn = open();
      if (!conn || degraded) break;
      const n = migrateChunk(conn);
      if (!n) break;
      rows += n;
      await new Promise((r) => setImmediate(r));
    }
    if (rows)
      console.error(
        `[traces] re-read ${rows} rows to v${SCHEMA_VERSION} in ${Date.now() - t0}ms`
      );
  } catch (err) {
    console.error(
      `[traces] re-read stopped after ${rows} rows: ${err.message}`
    );
  } finally {
    migrating = false;
  }
  return { rows, ms: Date.now() - t0 };
}
var UPSERT = `INSERT INTO spans (id, kind, parent, ts, durMs, server, tool, origin, identity, outcome,
                     summary, blobs, shape, witnesses, policy, ledger, error, session, file, sumv)
  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
  ON CONFLICT(id) DO UPDATE SET
    parent=excluded.parent, durMs=excluded.durMs, origin=excluded.origin,
    identity=excluded.identity, outcome=excluded.outcome, summary=excluded.summary,
    blobs=excluded.blobs, shape=excluded.shape, witnesses=excluded.witnesses,
    policy=excluded.policy, ledger=excluded.ledger, error=excluded.error,
    session=excluded.session, file=COALESCE(spans.file, excluded.file), sumv=excluded.sumv`;
var isLocked = (err) => /locked|busy/i.test(err?.message ?? "");
var LOCK_RETRIES = 3;
var LOCK_WAIT_MS = 200;
function pause(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}
function withRetry(what, fn, fallback) {
  for (let attempt = 0; ; attempt++) {
    try {
      return fn();
    } catch (err) {
      if (attempt < LOCK_RETRIES && isLocked(err)) {
        pause(LOCK_WAIT_MS);
        continue;
      }
      if (strict) throw err;
      console.error(`[traces] ${what}: ${err.message}`);
      return fallback;
    }
  }
}
function raising(fn) {
  const was = strict;
  strict = true;
  try {
    return fn();
  } finally {
    strict = was;
  }
}
function batch(fn) {
  const conn = open();
  if (!conn || inTxn) return fn();
  conn.exec("BEGIN IMMEDIATE");
  inTxn = true;
  try {
    const out = fn();
    conn.exec("COMMIT");
    return out;
  } catch (err) {
    try {
      conn.exec("ROLLBACK");
    } catch {
    }
    throw err;
  } finally {
    inTxn = false;
  }
}
function record(input) {
  const stored = { input: null, output: null };
  if (input.store) {
    for (const side of ["input", "output"])
      if (input[side] !== void 0)
        stored[side] = writeBlob(DIR, redact(input[side]));
  }
  const row = withRetry(
    `record failed for ${input.id}`,
    () => batch(() => writeSpan(input, stored)),
    null
  );
  if (row) notify?.(row);
  return row;
}
function writeSpan(input, stored) {
  {
    const conn = open();
    if (!conn || degraded) return null;
    const existing = conn.prepare("SELECT * FROM spans WHERE id = ?").get(input.id);
    const prev = existing ? hydrate(existing) : null;
    if (!prev && !(input.server && input.tool)) return null;
    const ts = prev?.ts ?? input.ts ?? (/* @__PURE__ */ new Date()).toISOString();
    let durMs = prev?.durMs ?? null;
    if (durMs === null && input.durMs !== void 0 && input.durMs !== null)
      durMs = input.durMs;
    if (durMs === null && input.endTs)
      durMs = Math.max(0, Date.parse(input.endTs) - Date.parse(ts));
    const identity = { ...EMPTY_IDENTITY, ...prev?.identity ?? {} };
    for (const [k, v] of Object.entries(input.identity ?? {})) {
      const key = k;
      identity[key] = fill(identity[key], v);
    }
    const summary = { ...prev?.summary ?? { input: "", output: "" } };
    const blobs = { ...prev?.blobs ?? { input: null, output: null } };
    const shape = { ...prev?.shape ?? { bytesIn: 0, bytesOut: 0 } };
    let refused = false;
    for (const side of ["input", "output"]) {
      const payload = input[side];
      if (payload !== void 0) {
        const key = side === "input" ? "bytesIn" : "bytesOut";
        const given = side === "input" ? input.bytes?.in : input.bytes?.out;
        if (!shape[key])
          shape[key] = given ?? Buffer.byteLength(JSON.stringify(payload ?? null));
        if (side === "output") {
          const read = classifyOutput(payload);
          refused = read.refused;
          if (!summary.output) summary.output = read.line;
        } else if (!summary.input) {
          const safe = redact(payload);
          summary.input = summarize(safe);
          summary.label = stepLabel(prev?.tool ?? input.tool ?? "", safe);
        }
        if (!blobs[side] && stored[side]) blobs[side] = stored[side];
      }
      const ref = input.blobs?.[side];
      if (ref && !blobs[side]) blobs[side] = ref;
    }
    const witnesses = [...prev?.witnesses ?? []];
    if (!witnesses.includes(input.witness)) witnesses.push(input.witness);
    let outcome = prev?.outcome ?? input.outcome ?? "running";
    const overturned = prev?.outcome === "error" && prev.error === UNREPORTED && input.outcome !== void 0 && input.outcome !== "running";
    if (input.outcome && (!prev || prev.outcome === "running" || overturned))
      outcome = input.outcome;
    if (refused && (outcome === "error" || outcome === "ok"))
      outcome = "refused";
    let origin = prev?.origin ?? input.origin ?? "session";
    if (input.origin && input.origin !== "session" && origin === "session")
      origin = input.origin;
    const row = {
      id: input.id,
      kind: "tool",
      parent: fill(prev?.parent, input.parent),
      ts,
      durMs,
      server: prev?.server ?? input.server,
      tool: prev?.tool ?? input.tool,
      origin,
      identity,
      outcome,
      summary,
      blobs,
      shape,
      witnesses
    };
    const policy = fill(prev?.policy, input.policy);
    const ledger = fill(prev?.ledger, input.ledger);
    const error = overturned ? input.error ?? null : fill(prev?.error, input.error);
    if (policy) row.policy = policy;
    if (ledger) row.ledger = ledger;
    if (error) row.error = error;
    conn.prepare(UPSERT).run(
      row.id,
      row.kind,
      row.parent,
      row.ts,
      row.durMs,
      row.server,
      row.tool,
      row.origin,
      JSON.stringify(row.identity),
      row.outcome,
      JSON.stringify(row.summary),
      JSON.stringify(row.blobs),
      JSON.stringify(row.shape),
      JSON.stringify(row.witnesses),
      row.policy ?? null,
      row.ledger ?? null,
      row.error ?? null,
      row.identity.session,
      input.file ?? null,
      SCHEMA_VERSION
    );
    return row;
  }
}
function list(filter = {}) {
  return withRetry("list failed", () => readPage(filter), { rows: [], next: null });
}
function readPage(filter) {
  {
    const conn = open();
    if (!conn) return { rows: [], next: null };
    const where = [];
    const args = [];
    const eq = (col, v) => {
      if (v) {
        where.push(`${col} = ?`);
        args.push(v);
      }
    };
    eq("server", filter.server);
    eq("tool", filter.tool);
    eq("session", filter.session);
    eq("origin", filter.origin);
    eq("outcome", filter.outcome);
    if (filter.since) {
      where.push("ts >= ?");
      args.push(filter.since);
    }
    if (filter.until) {
      where.push("ts < ?");
      args.push(filter.until);
    }
    if (filter.q) {
      where.push("(tool LIKE ? OR summary LIKE ?)");
      args.push(`%${filter.q}%`, `%${filter.q}%`);
    }
    if (filter.cursor) {
      const cut = filter.cursor.lastIndexOf("|");
      const cts = filter.cursor.slice(0, cut);
      where.push("(ts < ? OR (ts = ? AND id < ?))");
      args.push(cts, cts, filter.cursor.slice(cut + 1));
    }
    const limit = Math.max(1, Math.min(500, filter.limit ?? DEFAULT_LIMIT));
    const found = conn.prepare(
      `SELECT * FROM spans${where.length ? ` WHERE ${where.join(" AND ")}` : ""} ORDER BY ts DESC, id DESC LIMIT ${limit + 1}`
    ).all(...args);
    const rows = found.slice(0, limit).map(hydrate);
    const last = rows.at(-1);
    return {
      rows,
      next: found.length > limit && last ? `${last.ts}|${last.id}` : null
    };
  }
}
function get(id) {
  const row = withRetry(
    `get failed for ${id}`,
    () => {
      const conn = open();
      if (!conn) return null;
      const r = conn.prepare("SELECT * FROM spans WHERE id = ?").get(id);
      return r ? hydrate(r) : null;
    },
    null
  );
  if (!row) return null;
  return {
    row,
    input: readBlob(DIR, row.blobs.input, id, "input"),
    output: readBlob(DIR, row.blobs.output, id, "output")
  };
}
function sessionsLike(prefix, limit = 20) {
  return withRetry(
    "session lookup failed",
    () => {
      const conn = open();
      if (!conn) return [];
      return conn.prepare(
        `SELECT session, MAX(ts) at FROM spans WHERE session >= ? AND session < ?
             GROUP BY session ORDER BY at DESC LIMIT ?`
      ).all(prefix, `${prefix}\uFFFF`, limit).map((r) => String(r.session));
    },
    []
  );
}
function stats(server) {
  const empty = {
    total: 0,
    today: 0,
    errors: 0,
    running: 0,
    lastAt: null,
    tools: {}
  };
  return withRetry("stats failed", () => readStats(server), empty);
}
function readStats(server) {
  {
    const conn = open();
    const empty = {
      total: 0,
      today: 0,
      errors: 0,
      running: 0,
      lastAt: null,
      tools: {}
    };
    if (!conn) return empty;
    const w = server ? " WHERE server = ?" : "";
    const and = server ? `${w} AND` : " WHERE";
    const a = server ? [server] : [];
    const midnight = /* @__PURE__ */ new Date();
    midnight.setHours(0, 0, 0, 0);
    const one = (sql, extra = []) => Number(conn.prepare(sql).get(...a, ...extra)?.n ?? 0);
    const tools = {};
    for (const r of conn.prepare(
      `SELECT tool, COUNT(*) n FROM spans${w} GROUP BY tool ORDER BY n DESC LIMIT 200`
    ).all(...a))
      tools[String(r.tool)] = Number(r.n);
    const last = conn.prepare(`SELECT MAX(ts) ts FROM spans${w}`).get(...a);
    return {
      total: one(`SELECT COUNT(*) n FROM spans${w}`),
      today: one(`SELECT COUNT(*) n FROM spans${and} ts >= ?`, [
        midnight.toISOString()
      ]),
      errors: one(
        `SELECT COUNT(*) n FROM spans${and} outcome IN ('error','denied','refused')`
      ),
      running: one(`SELECT COUNT(*) n FROM spans${and} outcome = 'running'`),
      lastAt: last?.ts ? String(last.ts) : null,
      tools
    };
  }
}
function purge(args) {
  try {
    const conn = open();
    if (!conn || degraded) return { removed: 0 };
    const where = args.server ? "ts < ? AND server = ?" : "ts < ?";
    const params = args.server ? [args.before, args.server] : [args.before];
    if (args.dryRun)
      return {
        removed: Number(
          conn.prepare(`SELECT COUNT(*) n FROM spans WHERE ${where}`).get(...params)?.n ?? 0
        )
      };
    const hashes = /* @__PURE__ */ new Set();
    for (const d of conn.prepare(`SELECT blobs FROM spans WHERE ${where}`).all(...params)) {
      const b = parse(d.blobs, {
        input: null,
        output: null
      });
      for (const ref of [b.input, b.output])
        if (ref?.startsWith("sha256:")) hashes.add(ref.slice(7));
    }
    const removed = Number(
      conn.prepare(`DELETE FROM spans WHERE ${where}`).run(...params).changes
    );
    for (const hash of hashes) {
      const still = conn.prepare("SELECT COUNT(*) n FROM spans WHERE blobs LIKE ?").get(`%${hash}%`);
      if (Number(still?.n ?? 0) === 0)
        rmSync2(join6(DIR, "blobs", hash.slice(0, 2), `${hash}.json`), {
          force: true
        });
    }
    return { removed };
  } catch (err) {
    if (strict) throw err;
    console.error(`[traces] purge failed: ${err.message}`);
    return { removed: 0 };
  }
}
var toMark = (r) => ({
  path: String(r.path),
  pos: Number(r.pos),
  mtime: Number(r.mtime),
  size: Number(r.size),
  parentUse: r.parentUse ?? null,
  agentName: r.agentName ?? null
});
function fileMark(path) {
  try {
    const conn = open();
    if (!conn) return null;
    const r = conn.prepare("SELECT * FROM files WHERE path = ?").get(path);
    return r ? toMark(r) : null;
  } catch {
    return null;
  }
}
function fileMarks() {
  const out = /* @__PURE__ */ new Map();
  try {
    const conn = open();
    if (!conn) return out;
    for (const r of conn.prepare("SELECT * FROM files").all())
      out.set(String(r.path), toMark(r));
  } catch {
  }
  return out;
}
function saveFileMark(m) {
  try {
    const conn = open();
    if (!conn || degraded) return;
    conn.prepare(
      `INSERT INTO files (path, pos, mtime, size, parentUse, agentName) VALUES (?,?,?,?,?,?)
         ON CONFLICT(path) DO UPDATE SET pos=excluded.pos, mtime=excluded.mtime, size=excluded.size,
           parentUse=COALESCE(excluded.parentUse, files.parentUse),
           agentName=COALESCE(excluded.agentName, files.agentName)
         WHERE excluded.pos >= files.pos
            OR (excluded.size < files.size AND excluded.mtime > files.mtime)`
    ).run(m.path, m.pos, m.mtime, m.size, m.parentUse, m.agentName);
  } catch (err) {
    console.error(
      `[traces] file mark failed for ${m.path}: ${err.message}`
    );
  }
}
function noteAgentCall(a) {
  try {
    const conn = open();
    if (!conn || degraded) return;
    conn.prepare(
      "INSERT OR REPLACE INTO agent_calls (id, session, ts, description) VALUES (?,?,?,?)"
    ).run(a.id, a.session, a.ts, a.description);
  } catch {
  }
}
function findAgentCall(session, description, before) {
  try {
    const conn = open();
    if (!conn || !description) return null;
    const stem = description.replace(/[…\s]+$/, "");
    if (!stem) return null;
    const r = conn.prepare(
      `SELECT id FROM agent_calls WHERE session = ? AND ts <= ? AND description LIKE ? ORDER BY ts DESC LIMIT 1`
    ).get(session, before, `${stem}%`);
    return r ? String(r.id) : null;
  } catch {
    return null;
  }
}
function adoptParent(file, parent) {
  try {
    const conn = open();
    if (!conn || degraded) return;
    conn.prepare("UPDATE spans SET parent = ? WHERE file = ? AND parent IS NULL").run(parent, file);
  } catch {
  }
}
var UNREPORTED = "unreported";
function closeStale(maxAgeMs = 10 * 60 * 1e3) {
  try {
    const conn = open();
    if (!conn || degraded) return 0;
    const cutoff = new Date(Date.now() - maxAgeMs).toISOString();
    return Number(
      conn.prepare(
        "UPDATE spans SET outcome = 'error', error = COALESCE(error, ?) WHERE outcome = 'running' AND ts < ?"
      ).run(UNREPORTED, cutoff).changes
    );
  } catch {
    return 0;
  }
}

// src/traces-index.ts
import {
  closeSync as closeSync3,
  existsSync as existsSync5,
  openSync as openSync3,
  readFileSync as readFileSync2,
  rmSync as rmSync3,
  statSync as statSync3,
  writeFileSync as writeFileSync2,
  writeSync
} from "node:fs";
import { join as join7 } from "node:path";
var STALE_MS = 60 * 60 * 1e3;
var MAX_PASSES = 3;
var MAX_MS = 3e4;
var alive = (pid) => pidAlive(pid);
var EMPTY_STALE_MS = 1e4;
function lockState(lock) {
  try {
    const age = Date.now() - statSync3(lock).mtimeMs;
    if (age > STALE_MS) return "stale";
    const pid = Number.parseInt(readFileSync2(lock, "utf8"), 10);
    if (!(pid > 0)) return age > EMPTY_STALE_MS ? "stale" : "held";
    return alive(pid) ? "held" : "stale";
  } catch (err) {
    return err.code === "ENOENT" ? "gone" : "held";
  }
}
function acquire(lock, state = lockState) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const fd = openSync3(lock, "wx");
      try {
        writeSync(fd, String(process.pid));
      } finally {
        closeSync3(fd);
      }
      return true;
    } catch (err) {
      if (err.code !== "EEXIST") throw err;
      const now = state(lock);
      if (now === "held") return false;
      if (now === "stale") rmSync3(lock, { force: true });
    }
  }
  return false;
}
function release(lock) {
  try {
    if (readFileSync2(lock, "utf8") === String(process.pid)) rmSync3(lock);
  } catch {
  }
}
function consume(flag) {
  rmSync3(flag, { force: true });
}
async function runIndex(opts = {}) {
  const t0 = Date.now();
  const out = { files: 0, bytes: 0, ms: 0, passes: 0, locked: false };
  if (!open()) throw new Error(`the store at ${dir()} would not open`);
  const lock = join7(dir(), "walk.lock");
  const again = join7(dir(), "walk.again");
  const deadline = t0 + (opts.maxMs ?? MAX_MS);
  const budget = opts.budget ?? BACKFILL_BYTES;
  writeFileSync2(again, "");
  if (!acquire(lock)) {
    out.locked = true;
    out.ms = Date.now() - t0;
    return out;
  }
  for (let round = 0; ; round++) {
    try {
      let more = false;
      do {
        consume(again);
        const r = await pass(budget, { recent: opts.recent, deadline });
        out.files += r.files;
        out.bytes += r.bytes;
        out.passes++;
        more = r.bytes >= budget;
      } while (out.passes < MAX_PASSES && Date.now() < deadline && (more || existsSync5(again)));
      if (round === 0) {
        closeStale();
        if (opts.upkeep !== false) await resummarize();
      }
    } finally {
      release(lock);
    }
    if (!existsSync5(again)) break;
    if (out.passes >= MAX_PASSES || Date.now() >= deadline) break;
    if (!acquire(lock)) break;
  }
  out.ms = Date.now() - t0;
  return out;
}

// bin/traces.ts
var USAGE = "usage: traces index [--recent] | list [--server S] [--tool T] [--session ID] [--origin O] [--outcome O] [--since ISO] [--until ISO] [--q TEXT] [--limit N] [--cursor C] [--refresh] | get <id> | stats [--server S] | purge --before ISO [--server S] [--dry-run]";
var NO_INDEX = `no trace index yet \u2014 run: ${cmd("trace index")}`;
var UUID_LENGTH = 36;
var CANDIDATES = 20;
var REFRESH_MS = 2e3;
var ORIGINS = /* @__PURE__ */ new Set(["session", "agent", "automation", "bench"]);
var OUTCOMES = /* @__PURE__ */ new Set(["ok", "error", "refused", "denied", "running"]);
var Exit = class extends Error {
  code;
  doc;
  constructor(code, message, doc) {
    super(message);
    this.code = code;
    this.doc = doc;
  }
};
var usage = (why) => new Exit(64, `${why} \u2014 ${USAGE}`);
function parsed(fn) {
  try {
    return fn();
  } catch (err) {
    throw usage(err.message);
  }
}
var ROOT2 = { root: { type: "string" } };
function iso(flag, v) {
  if (v === void 0) return void 0;
  const t = Date.parse(v);
  if (Number.isNaN(t)) throw usage(`--${flag} is not a date: ${v}`);
  return new Date(t).toISOString();
}
function readable() {
  if (!openReadOnly()) throw new Exit(1, NO_INDEX);
}
function resolveSession(given) {
  if (!given) throw usage("--session is empty");
  if (given.length >= UUID_LENGTH) return given;
  const found = raising(() => sessionsLike(given, CANDIDATES + 1));
  if (!found.length) throw new Exit(1, `no session starts with ${given}`);
  if (found.length > 1) {
    const count = found.length > CANDIDATES ? `more than ${CANDIDATES}` : String(found.length);
    throw new Exit(2, `${count} sessions start with ${given} \u2014 give more of the id`, {
      candidates: found.slice(0, CANDIDATES)
    });
  }
  return found[0];
}
async function list2(args) {
  const { values: v } = parsed(
    () => parseArgs({
      args,
      strict: true,
      options: {
        server: { type: "string" },
        tool: { type: "string" },
        session: { type: "string" },
        origin: { type: "string" },
        outcome: { type: "string" },
        since: { type: "string" },
        until: { type: "string" },
        q: { type: "string" },
        limit: { type: "string" },
        cursor: { type: "string" },
        refresh: { type: "boolean" },
        ...ROOT2
      }
    })
  );
  if (v.origin !== void 0 && !ORIGINS.has(v.origin)) throw usage(`--origin is one of ${[...ORIGINS].join(", ")}`);
  if (v.outcome !== void 0 && !OUTCOMES.has(v.outcome))
    throw usage(`--outcome is one of ${[...OUTCOMES].join(", ")}`);
  let limit;
  if (v.limit !== void 0) {
    if (!/^\d+$/.test(v.limit) || Number(v.limit) < 1) throw usage(`--limit is a positive integer: ${v.limit}`);
    limit = Math.min(500, Number(v.limit));
  }
  const since = iso("since", v.since);
  const until = iso("until", v.until);
  if (v.refresh) {
    if (!existsSync6(dbFile())) throw new Exit(1, NO_INDEX);
    await runIndex({ recent: true, budget: TICK_BYTES, upkeep: false, maxMs: REFRESH_MS });
  } else readable();
  const filter = {
    ...v.server ? { server: v.server } : {},
    ...v.tool ? { tool: v.tool } : {},
    ...v.session !== void 0 ? { session: resolveSession(v.session) } : {},
    ...v.origin ? { origin: v.origin } : {},
    ...v.outcome ? { outcome: v.outcome } : {},
    ...since ? { since } : {},
    ...until ? { until } : {},
    ...v.q ? { q: v.q } : {},
    ...limit ? { limit } : {},
    ...v.cursor ? { cursor: v.cursor } : {}
  };
  return raising(() => list(filter));
}
function get2(args) {
  const { positionals } = parsed(
    () => parseArgs({ args, strict: true, allowPositionals: true, options: { ...ROOT2 } })
  );
  if (positionals.length !== 1) throw usage(`get takes one id, got ${positionals.length}`);
  readable();
  const detail = raising(() => get(positionals[0]));
  if (!detail) throw new Exit(1, `no trace ${positionals[0]}`);
  return detail;
}
function stats2(args) {
  const { values } = parsed(
    () => parseArgs({ args, strict: true, options: { server: { type: "string" }, ...ROOT2 } })
  );
  readable();
  return raising(() => stats(values.server));
}
function purge2(args) {
  const { values } = parsed(
    () => parseArgs({
      args,
      strict: true,
      options: {
        before: { type: "string" },
        server: { type: "string" },
        "dry-run": { type: "boolean" },
        ...ROOT2
      }
    })
  );
  const before = iso("before", values.before);
  if (!before) throw usage("purge needs --before");
  const dryRun = values["dry-run"] === true;
  if (dryRun) readable();
  else if (!existsSync6(dbFile())) throw new Exit(1, NO_INDEX);
  const result = raising(
    () => purge({ before, dryRun, ...values.server ? { server: values.server } : {} })
  );
  return { ...result, dryRun };
}
async function index(args) {
  const { values } = parsed(
    () => parseArgs({ args, strict: true, options: { recent: { type: "boolean" }, ...ROOT2 } })
  );
  return runIndex({ recent: values.recent === true });
}
var VERBS = { index, list: list2, get: get2, stats: stats2, purge: purge2 };
async function main(argv) {
  const [verb, ...rest] = argv;
  try {
    const run = verb ? VERBS[verb] : void 0;
    if (!run) throw usage(verb ? `no verb ${verb}` : "no verb");
    const doc = await run(rest);
    process.stdout.write(`${JSON.stringify(doc)}
`);
    process.exitCode = 0;
  } catch (err) {
    const e = err instanceof Exit ? err : new Exit(1, String(err?.message ?? err).split("\n")[0]);
    if (e.doc !== void 0) process.stdout.write(`${JSON.stringify(e.doc)}
`);
    process.stderr.write(`traces: ${e.message}
`);
    process.exitCode = e.code;
  } finally {
    close();
  }
}
if (/(^|[\\/])traces\.(ts|mjs)$/.test(process.argv[1] ?? "")) await main(process.argv.slice(2));
