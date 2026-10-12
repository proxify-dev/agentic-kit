// cc-shim selftest — installs into a throwaway HOME, asserts the fail-open guarantee and the
// installer, then uninstalls. node:test, no dependencies:
//
//   node --test shim/tests/*.test.mjs        (or: cd shim && node --test)
//
// The fail-open matrix is the reason this file exists: every one of those fragments is a way a
// user could brick `claude` if the shim ran fragments in-process instead of contained.
//
// The end-to-end half runs the real POSIX path — `cc-shim install`, the generated stub, a fake
// claude that reports the argv and env it was handed. Windows cannot run it here; the Windows
// pieces (PATHEXT lookup, cmd.exe quoting, seeing through claude.cmd, the User Path edit) are
// pure functions, tested with simulated inputs below.
import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { after, before, describe, test } from "node:test"
import { fileURLToPath } from "node:url"

import {
  fromPsB64,
  launchPlan,
  onlyOurLine,
  parseAccount,
  parseEnvDump,
  prependPathEntry,
  PS_GET,
  PS_SET,
  readUserPath,
  toPsB64,
  unwireWindowsPath,
  wireWindowsPath,
  quoteCmdArg,
  quoteCmdCommand,
  removePathEntry,
  renderCmdStub,
  renderEnvironmentD,
  resolveReal,
  seeThroughCmd,
  whichAll,
} from "../cc-shim.mjs"

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const SHIM = path.join(SRC, "cc-shim.mjs")
const POSIX = process.platform !== "win32"
const BRAND_CLI = fs.readFileSync(path.join(SRC, "_brand.sh"), "utf8").match(/^BRAND_CLI="([^"]*)"/m)[1]

// ================================================================== pure pieces (every OS)
describe("the account flags", () => {
  const t = (args) => {
    const r = parseAccount(args)
    return `USE:${r.seen ? r.name : ""}${r.soft ? " soft" : ""} ARGV:${r.argv.map((a) => `[${a}]`).join("")}`
  }
  test("consumed at any position before --", () => {
    assert.equal(t(["--account", "x", "-p", "hi"]), "USE:x ARGV:[-p][hi]")
    assert.equal(t(["-p", "hi", "--account", "x"]), "USE:x ARGV:[-p][hi]")
    assert.equal(t(["--dsp", "--agent", "m", "--account", "x"]), "USE:x ARGV:[--dsp][--agent][m]")
    assert.equal(t(["--account=x", "-p", "hi"]), "USE:x ARGV:[-p][hi]")
    assert.equal(t(["--acct", "x", "-p", "hi"]), "USE:x ARGV:[-p][hi]")
    assert.equal(t(["--acct=x", "-p", "hi"]), "USE:x ARGV:[-p][hi]")
    assert.equal(t(["--account", "a", "-p", "hi", "--account", "b"]), "USE:b ARGV:[-p][hi]")
    assert.equal(t(["-p", "hi", "--", "--account", "x"]), "USE: ARGV:[-p][hi][--][--account][x]")
    assert.equal(t([]), "USE: ARGV:")
    assert.equal(t(["--account", "x"]), "USE:x ARGV:")
    assert.equal(t(["--prefer", "x", "-p"]), "USE:x soft ARGV:[-p]")
    assert.equal(t(["--prefer=x", "--account", "y"]), "USE:y ARGV:", "last one wins, and takes its failure mode")
  })
  test("a flag after a bare --account is not its name", () => {
    const r = parseAccount(["--account", "-p", "hi"])
    assert.deepEqual(r, { seen: true, name: "", soft: false, argv: ["-p", "hi"] })
  })
})

describe("finding the real claude (simulated file systems)", () => {
  // A fake disk: the set of executable files, and a realpath map for symlinks.
  const disk = (files, links = {}) => ({
    exec: (p) => files.includes(p),
    real: (p) => links[p] ?? p,
  })

  test("Windows: PATHEXT order, ; as the delimiter, the shim's own dir skipped", () => {
    const self = "C:\\Users\\me\\AppData\\Local\\cc-shim"
    const d = disk([`${self}\\claude.cmd`, "C:\\tools\\claude.cmd", "C:\\tools\\claude.exe", "C:\\Users\\me\\.local\\bin\\claude.exe"])
    const env = { Path: `${self};C:\\tools;C:\\Users\\me\\.local\\bin`, PATHEXT: ".COM;.EXE;.BAT;.CMD", USERPROFILE: "C:\\Users\\me" }
    assert.equal(resolveReal({ env, plat: "win32", selfDir: self, ...d }), "C:\\tools\\claude.exe", ".EXE comes before .CMD in PATHEXT")
    assert.equal(resolveReal({ env: { ...env, PATHEXT: ".CMD;.EXE" }, plat: "win32", selfDir: self, ...d }), "C:\\tools\\claude.cmd")
    assert.deepEqual(whichAll("claude", { env, plat: "win32", exec: d.exec }), [`${self}\\claude.cmd`, "C:\\tools\\claude.exe", "C:\\tools\\claude.cmd", "C:\\Users\\me\\.local\\bin\\claude.exe"])
  })

  test("Windows: an unset PATHEXT is the stock one; a stripped Path falls back to the standard installs", () => {
    const self = "C:\\s"
    const d = disk(["C:\\Users\\me\\.local\\bin\\claude.exe", "C:\\Users\\me\\AppData\\Roaming\\npm\\claude.cmd"])
    const env = { Path: "C:\\Windows", USERPROFILE: "C:\\Users\\me", APPDATA: "C:\\Users\\me\\AppData\\Roaming" }
    assert.equal(resolveReal({ env, plat: "win32", selfDir: self, ...d }), "C:\\Users\\me\\.local\\bin\\claude.exe")
    const npmOnly = disk(["C:\\Users\\me\\AppData\\Roaming\\npm\\claude.cmd"])
    assert.equal(resolveReal({ env, plat: "win32", selfDir: self, ...npmOnly }), "C:\\Users\\me\\AppData\\Roaming\\npm\\claude.cmd")
    assert.equal(resolveReal({ env, plat: "win32", selfDir: self, ...disk([]) }), null)
  })

  test("POSIX: skip-self by dir, by realpath of a symlink elsewhere, then ~/.local/bin", () => {
    const self = "/h/.local/share/cc-shim"
    const d = disk([`${self}/claude`, "/elsewhere/claude", "/usr/local/bin/claude", "/h/.local/bin/claude"], { "/elsewhere/claude": `${self}/claude` })
    const env = { PATH: `${self}:/elsewhere:/usr/local/bin`, HOME: "/h" }
    assert.equal(resolveReal({ env, plat: "linux", selfDir: self, ...d }), "/usr/local/bin/claude")
    assert.equal(resolveReal({ env: { PATH: `${self}:/elsewhere`, HOME: "/h" }, plat: "linux", selfDir: self, ...d }), "/h/.local/bin/claude")
    assert.equal(resolveReal({ env: { ...env, CC_SHIM_REAL: "/usr/local/bin/claude", PATH: "" }, plat: "linux", selfDir: self, ...d }), "/usr/local/bin/claude")
  })
})

describe("Windows: handing over to a batch file", () => {
  // Expected strings follow cross-spawn's escaping (the scheme node's own docs point to):
  // quote, escape inner quotes, caret-escape every cmd metacharacter — twice for a batch file.
  const table = [
    ["plain", "plain", '^^^"plain^^^"'],
    ["two words", "two words", '^^^"two^^^ words^^^"'],
    ['say "hi"', 'say "hi"', '^^^"say^^^ \\^^^"hi\\^^^"^^^"'],
    ["a&b", "a&b", '^^^"a^^^&b^^^"'],
    ["100%", "100%", '^^^"100^^^%^^^"'],
    ["%PATH%", "%PATH%", '^^^"^^^%PATH^^^%^^^"'],
    ["caret ^", "^", '^^^"^^^^^^^"'],
    ["pipe and redirect", "a|b>c<d", '^^^"a^^^|b^^^>c^^^<d^^^"'],
    ["trailing backslash", "C:\\dir\\", '^^^"C:\\dir\\\\^^^"'],
    ["backslash before quote", 'a\\"b', '^^^"a\\\\\\^^^"b^^^"'],
    ["empty", "", '^^^"^^^"'],
    ["bang", "wow!", '^^^"wow^^^!^^^"'],
  ]
  for (const [name, arg, want] of table) test(`quoting: ${name}`, () => assert.equal(quoteCmdArg(arg, true), want))

  test("quoting: single escape when the target does not re-parse", () => {
    assert.equal(quoteCmdArg("a&b", false), '^"a^&b^"')
    assert.equal(quoteCmdCommand("C:\\Program Files (x86)\\x.cmd"), "C:\\Program^ Files^ ^(x86^)\\x.cmd")
  })

  test("an npm claude.cmd is seen through to its node script or native exe — no cmd.exe", () => {
    const npm = [
      "@ECHO off", "GOTO start", ":find_dp0", "SET dp0=%~dp0", "EXIT /b", ":start", "SETLOCAL", "CALL :find_dp0",
      'IF EXIST "%dp0%\\node.exe" (', '  SET "_prog=%dp0%\\node.exe"', ") ELSE (", '  SET "_prog=node"', ")",
      'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*',
    ].join("\r\n")
    const seen = seeThroughCmd("C:\\Users\\me\\AppData\\Roaming\\npm\\claude.cmd", npm)
    assert.equal(seen.args[0], "C:\\Users\\me\\AppData\\Roaming\\npm\\node_modules\\@anthropic-ai\\claude-code\\cli.js")
    assert.equal(seen.command, process.execPath, "no node.exe beside it: the node running the shim")
    const exe = seeThroughCmd("C:\\n\\claude.cmd", '@"%~dp0\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe" %*')
    assert.deepEqual(exe, { command: "C:\\n\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe", args: [] })
    assert.equal(seeThroughCmd("C:\\x.cmd", "@echo off\r\nsomething %*"), null)
  })

  test("launch plan: an exe as is, an opaque batch file through cmd.exe, a line break refused", () => {
    assert.deepEqual(launchPlan("C:\\c\\claude.exe", ["-p", "a b"], "win32"), { command: "C:\\c\\claude.exe", args: ["-p", "a b"], options: {} })
    const plan = launchPlan("C:\\nowhere\\claude.cmd", ["-p", "a&b"], "win32")
    assert.deepEqual(plan.args.slice(0, 3), ["/d", "/s", "/c"])
    assert.equal(plan.args[3], `"C:\\nowhere\\claude.cmd ${quoteCmdArg("-p")} ${quoteCmdArg("a&b")}"`)
    assert.deepEqual(plan.options, { windowsVerbatimArguments: true })
    assert.throws(() => launchPlan("C:\\nowhere\\claude.cmd", ["-p", "line1\nline2"], "win32"), /line break/)
    assert.deepEqual(launchPlan("/usr/bin/claude", ["x"], "linux"), { command: "/usr/bin/claude", args: ["x"], options: {} })
  })

  test("the claude.cmd stub runs this module from its own folder", () => {
    const s = renderCmdStub("claude", "C:\\node\\node.exe")
    assert.match(s, /^@echo off\r\n/)
    assert.match(s, /set "CC_SHIM_NODE=C:\\node\\node\.exe"/)
    assert.match(s, /"%CC_SHIM_NODE%" "%~dp0cc-shim\.mjs" claude %\*/)
    assert.match(s, /exit \/b %ERRORLEVEL%/)
    assert.doesNotMatch(renderCmdStub("cc-shim"), / claude %\*/)
  })

  test("the User Path: the shim first, once; removed on uninstall; %VARS% kept", () => {
    const dir = "C:\\Users\\me\\AppData\\Local\\cc-shim"
    assert.deepEqual(prependPathEntry("%USERPROFILE%\\.local\\bin;C:\\x", dir), { value: `${dir};%USERPROFILE%\\.local\\bin;C:\\x`, changed: true })
    assert.deepEqual(prependPathEntry(`${dir};C:\\x`, dir), { value: `${dir};C:\\x`, changed: false })
    assert.deepEqual(prependPathEntry(`C:\\x;${dir.toUpperCase()}\\`, dir), { value: `${dir};C:\\x`, changed: true }, "moved to the front, case and a trailing \\ ignored")
    assert.deepEqual(prependPathEntry("", dir), { value: dir, changed: true })
    assert.deepEqual(removePathEntry(`${dir};C:\\x;${dir}`, dir), { value: "C:\\x", changed: true })
    assert.deepEqual(removePathEntry("C:\\x", dir), { value: "C:\\x", changed: false })
  })

  // A fake powershell.exe: answers PS_GET with the registry value as base64 UTF-16LE, and
  // records what PS_SET would write. PowerShell 5.1's own stdout would be OEM-code-page text.
  const fakePs = (registry) => {
    const state = { value: registry, writes: 0 }
    const run = (cmd, args, opts) => {
      assert.equal(cmd, "powershell.exe")
      const script = args[args.length - 1]
      if (script === PS_GET) return { status: 0, stdout: Buffer.from(state.value, "utf16le").toString("base64") }
      if (script === PS_SET) {
        assert.equal(opts.env.CC_SHIM_USER_PATH, undefined, "the value never travels as text")
        state.value = Buffer.from(opts.env.CC_SHIM_USER_PATH_B64, "base64").toString("utf16le")
        state.writes++
        return { status: 0, stdout: "" }
      }
      return { status: 1, stdout: "" }
    }
    return { run, state }
  }

  test("the User Path crosses PowerShell as base64 UTF-16 both ways: José and 日本 survive", () => {
    assert.match(PS_GET, /ToBase64String\(\[Text\.Encoding\]::Unicode\.GetBytes/)
    assert.match(PS_SET, /FromBase64String\(\$env:CC_SHIM_USER_PATH_B64\)/)
    const dir = "C:\\Users\\José\\AppData\\Local\\cc-shim"
    const reg = "%USERPROFILE%\\.local\\bin;C:\\Users\\José\\tools;D:\\日本\\bin"
    const ps = fakePs(reg)
    assert.equal(readUserPath(ps.run), reg)
    wireWindowsPath(dir, ps.run)
    assert.equal(ps.state.value, `${dir};${reg}`, "written back byte-for-byte, the shim first")
    wireWindowsPath(dir, ps.run)
    assert.equal(ps.state.writes, 1, "already first: no second write")
    unwireWindowsPath(dir, ps.run)
    assert.equal(ps.state.value, reg)
    assert.equal(fromPsB64(toPsB64("C:\\Jösé\\日本;%X%")), "C:\\Jösé\\日本;%X%")
    // What an old PS_GET would have handed back — OEM text with the é lost — is refused, never written.
    const garbled = (cmd, args) => ({ status: 0, stdout: "C:\\Users\\Jos\uFFFD\\tools" })
    assert.equal(readUserPath(garbled), null)
    assert.equal(readUserPath(() => ({ status: 1, stdout: "" })), null)
  })
})

describe("Windows: the .cmd stub and a non-ASCII node path", () => {
  test("an ASCII node path: the plain stub, the console untouched", () => {
    const s = renderCmdStub("claude", "C:\\node\\node.exe")
    assert.doesNotMatch(s, /chcp/)
  })
  test("a non-ASCII node path: UTF-8 before the line naming node, the saved code page restored, the exit code kept", () => {
    const s = renderCmdStub("claude", "C:\\Users\\José\\node\\node.exe")
    const lines = s.split("\r\n")
    const at = (re) => lines.findIndex((l) => re.test(l))
    assert.ok(at(/^for \/f "tokens=2 delims=:\." %%c in \('chcp'\) do set "CC_SHIM_CP=%%c"$/) > 0, "the code page is saved")
    assert.ok(at(/^chcp 65001 >nul$/) < at(/^set "CC_SHIM_NODE=C:\\Users\\José/), "switched before cmd reads the path")
    assert.ok(at(/^set "CC_SHIM_RC=%ERRORLEVEL%"$/) > at(/cc-shim\.mjs" claude %\*$/))
    assert.ok(at(/^if defined CC_SHIM_CP chcp %CC_SHIM_CP% >nul$/) > at(/^set "CC_SHIM_RC/))
    assert.equal(lines.at(-2), "exit /b %CC_SHIM_RC%")
    assert.ok(Buffer.from(s, "utf8").includes(Buffer.from("José", "utf8")), "written as UTF-8, which 65001 reads")
  })
})

test("environment.d puts the shim first for systemd user sessions", () => {
  assert.equal(renderEnvironmentD("/h/.local/share/cc-shim"), "# cc-shim — the claude wrapper first on PATH for systemd user sessions\nPATH=/h/.local/share/cc-shim:${PATH}\n")
})

// ================================================================== end to end (POSIX)
describe("installed, end to end", { skip: !POSIX && "the POSIX path; Windows is covered by the simulated tests above" }, () => {
  let TMP, HOME, DIR, CONFD, FAKE, ARGFAKE, ENV

  const clean = (env) => {
    for (const k of Object.keys(env)) {
      if (/^(XDG_|CC_SHIM_|CC_SYSPROMPT_|ANTHROPIC_)/.test(k) || k === "CLAUDE_CODE_OAUTH_TOKEN" || k === "CLAUDECODE") delete env[k]
    }
    return env
  }
  const cli = (args, extra = {}) => spawnSync(process.execPath, [SHIM, ...args], { env: { ...ENV, ...extra }, encoding: "utf8" })
  const run = (args = [], extra = {}) =>
    spawnSync(path.join(DIR, "claude"), args, { env: { ...ENV, CC_SHIM_REAL: FAKE, ...extra }, encoding: "utf8" }).stdout.trim()
  const acct = (args = [], extra = {}) =>
    spawnSync(path.join(DIR, "claude"), args, { env: { ...ENV, CC_SHIM_REAL: ARGFAKE, ...extra }, encoding: "utf8" })
  const frag = (name, body) => {
    fs.writeFileSync(path.join(CONFD, name), `${body}\n`)
    fs.chmodSync(path.join(CONFD, name), 0o600)
  }
  const clearFrags = () => {
    for (const f of fs.readdirSync(CONFD)) fs.rmSync(path.join(CONFD, f), { force: true })
  }
  const nlines = (rc) => {
    try {
      return fs.readFileSync(path.join(HOME, rc), "utf8").split("\n").filter((l) => l.includes("# cc-shim")).length
    } catch {
      return 0
    }
  }
  const mode = (p) => (fs.statSync(p).mode & 0o777).toString(8)

  before(() => {
    TMP = fs.mkdtempSync(path.join(os.tmpdir(), "cc-shim-test-"))
    HOME = path.join(TMP, "home")
    fs.mkdirSync(HOME)
    DIR = path.join(HOME, ".local", "share", "cc-shim")
    CONFD = path.join(HOME, ".config", "cc-shim", "conf.d")
    // The invoking shell may carry routing exports (a proxy setup, a prior shim). They would be
    // inherited by the fake claude and break every assertion, so start from a known-clean slate.
    ENV = clean({ ...process.env, HOME, SHELL: "/bin/zsh", CC_SHIM_SRC: SRC })
    fs.mkdirSync(path.join(TMP, "fakebin"))
    FAKE = path.join(TMP, "fakebin", "claude")
    fs.writeFileSync(FAKE, '#!/usr/bin/env bash\nprintf \'REAL:%s:%s:%s\\n\' "${CLAUDE_CODE_OAUTH_TOKEN-}" "${CC_SHIM_CLAIM-}" "${ANTHROPIC_BASE_URL-unset}"\n')
    ARGFAKE = path.join(TMP, "fakebin", "claude-args")
    fs.writeFileSync(ARGFAKE, '#!/usr/bin/env bash\nprintf \'USE:%s ARGV:\' "${CC_SHIM_ACCOUNT-}"\nfor a in "$@"; do printf \'[%s]\' "$a"; done\nprintf \'\\n\'\nexit "${FAKE_EXIT:-0}"\n')
    fs.chmodSync(FAKE, 0o755)
    fs.chmodSync(ARGFAKE, 0o755)
  })
  after(() => fs.rmSync(TMP, { recursive: true, force: true }))

  test("1. install", () => {
    const r = cli(["install"])
    assert.equal(r.status, 0, r.stderr)
    const st = fs.lstatSync(path.join(DIR, "claude"))
    assert.ok(st.isFile() && !st.isSymbolicLink(), "wrapper is a regular file (not a symlink)")
    assert.equal(mode(path.join(DIR, "claude")), "755")
    assert.equal(mode(path.join(DIR, "cc-shim")), "755")
    assert.deepEqual(fs.readFileSync(path.join(DIR, "cc-shim.mjs")), fs.readFileSync(SHIM), "the module is a copy")
    assert.equal(fs.readFileSync(path.join(DIR, "_brand.sh"), "utf8"), fs.readFileSync(path.join(SRC, "_brand.sh"), "utf8"), "the brand words are installed beside the wrapper")
    assert.equal(mode(CONFD), "700")
    assert.equal(nlines(".zshenv"), 1, "~/.zshenv wired exactly once")
    assert.match(fs.readFileSync(path.join(DIR, "claude"), "utf8"), /^#!\/bin\/sh\n/)
  })

  test("2. install is idempotent", () => {
    cli(["install"])
    assert.equal(nlines(".zshenv"), 1)
    assert.ok(!fs.existsSync(path.join(HOME, ".bashrc")), "a zsh user gets no .bashrc")
  })

  test("3. PATH loader", () => {
    const r = spawnSync("/bin/sh", ["-c", '. "$1"; . "$1"; printf %s "$PATH"', "_", path.join(DIR, "env")], { env: { ...ENV, PATH: "/usr/bin:/bin" }, encoding: "utf8" })
    assert.equal(r.stdout, `${DIR}:/usr/bin:/bin`, "the shim dir first, once, however often it is sourced")
  })

  test("4. empty conf.d is pure passthrough", () => assert.equal(run(), "REAL:::unset"))

  test("5. a good fragment applies (claim assigned, not exported)", () => {
    frag("10-good.sh", "export CLAUDE_CODE_OAUTH_TOKEN=tok\nCC_SHIM_CLAIM=good")
    assert.equal(run(), "REAL:tok:good:unset")
  })

  test("6. a claim stops later fragments", () => {
    frag("20-later.sh", "export CLAUDE_CODE_OAUTH_TOKEN=WRONG")
    frag("30-later.mjs", 'process.env.CLAUDE_CODE_OAUTH_TOKEN = "WRONG"')
    assert.equal(run(), "REAL:tok:good:unset")
    fs.rmSync(path.join(CONFD, "20-later.sh"))
    fs.rmSync(path.join(CONFD, "30-later.mjs"))
  })

  describe("7. fail-open matrix", () => {
    const survives = [
      ["05-broken.sh", "set -e + failing command", "set -e\nfalse\nexport CLAUDE_CODE_OAUTH_TOKEN=NOPE"],
      ["05-broken.sh", "bare exit 1", "exit 1\nexport CLAUDE_CODE_OAUTH_TOKEN=NOPE"],
      ["05-broken.sh", "syntax error", "if [ ; then"],
      ["05-broken.sh", "stdout junk", 'echo "chatter on stdout"'],
      ["05-broken.sh", "exec", "exec /bin/true"],
      ["05-broken.mjs", "process.exit(1)", 'process.env.CLAUDE_CODE_OAUTH_TOKEN = "NOPE"; process.exit(1)'],
      ["05-broken.mjs", "a throw", 'throw new Error("boom")'],
      ["05-broken.mjs", "a syntax error", "if ( {"],
      ["05-broken.mjs", "stdout junk", 'console.log("chatter on stdout"); process.stdout.write("more")'],
      ["05-broken.mjs", "an import that fails", 'import "node:does-not-exist"'],
      ["05-broken.mjs", "a timer left running", "setInterval(() => {}, 1000)"],
    ]
    for (const [file, name, body] of survives) {
      test(`survives: ${name} (${path.extname(file)})`, () => {
        frag(file, body)
        assert.equal(run(), "REAL:tok:good:unset")
        fs.rmSync(path.join(CONFD, file))
      })
    }

    // The cases above all have 10-good.sh running afterwards to overwrite the damage, so they
    // prove "claude still launches" but not what each broken fragment contributed. These isolate
    // that, and they behave differently on purpose.
    test("what a broken fragment contributes", () => {
      clearFrags()
      frag("10-exits.sh", "export CLAUDE_CODE_OAUTH_TOKEN=PARTIAL\nexit 1")
      assert.equal(run(), "REAL:::unset", "a fragment that exits contributes NOTHING (child dies before env -0)")
      frag("10-exits.sh", "set -e\nexport CLAUDE_CODE_OAUTH_TOKEN=SURVIVES\nfalse")
      assert.equal(run(), "REAL:SURVIVES::unset", "set -e does NOT abort a fragment (sourced inside a || list)")
      fs.rmSync(path.join(CONFD, "10-exits.sh"))
      frag("10-exits.mjs", 'process.env.CLAUDE_CODE_OAUTH_TOKEN = "PARTIAL"; process.exit(0)')
      assert.equal(run(), "REAL:::unset", "an .mjs that exits contributes nothing, even with code 0")
      frag("10-exits.mjs", 'process.env.CLAUDE_CODE_OAUTH_TOKEN = "PARTIAL"; throw new Error("x")')
      assert.equal(run(), "REAL:::unset", "an .mjs that throws contributes nothing")
      fs.rmSync(path.join(CONFD, "10-exits.mjs"))
    })

    test("survives: unreadable fragment", () => {
      clearFrags()
      frag("10-good.sh", "export CLAUDE_CODE_OAUTH_TOKEN=tok\nCC_SHIM_CLAIM=good")
      frag("05-broken.sh", "export CLAUDE_CODE_OAUTH_TOKEN=NOPE")
      frag("06-broken.mjs", 'process.env.CLAUDE_CODE_OAUTH_TOKEN = "NOPE"')
      fs.chmodSync(path.join(CONFD, "05-broken.sh"), 0o000)
      fs.chmodSync(path.join(CONFD, "06-broken.mjs"), 0o000)
      assert.equal(run(), "REAL:tok:good:unset")
      fs.rmSync(path.join(CONFD, "05-broken.sh"))
      fs.rmSync(path.join(CONFD, "06-broken.mjs"))
    })
  })

  test("8. CC_SHIM_UNSET clears an inherited variable", () => {
    frag("05-unset.sh", 'CC_SHIM_UNSET="ANTHROPIC_BASE_URL"')
    assert.equal(run([], { ANTHROPIC_BASE_URL: "http://leftover:3456" }), "REAL:tok:good:unset")
    fs.rmSync(path.join(CONFD, "05-unset.sh"))
    frag("05-unset.mjs", 'process.env.CC_SHIM_UNSET = "ANTHROPIC_BASE_URL"')
    assert.equal(run([], { ANTHROPIC_BASE_URL: "http://leftover:3456" }), "REAL:tok:good:unset", "the same from an .mjs")
    fs.rmSync(path.join(CONFD, "05-unset.mjs"))
  })

  test("8b. an .mjs fragment's env is imported, and .sh and .mjs run in one lexical order", () => {
    clearFrags()
    frag("10-a.mjs", 'process.env.CLAUDE_CODE_OAUTH_TOKEN = "from-mjs"; process.env.ORDER = (process.env.ORDER ?? "") + "a"')
    frag("20-b.sh", 'export ORDER="${ORDER}b"')
    frag("30-c.mjs", 'await new Promise((r) => setTimeout(r, 50)); process.env.ORDER += "c"; process.env.CC_SHIM_CLAIM = "mjs"')
    frag("40-d.sh", 'export ORDER="${ORDER}d"')
    const probe = path.join(TMP, "fakebin", "claude-order")
    fs.writeFileSync(probe, '#!/bin/sh\necho "$CLAUDE_CODE_OAUTH_TOKEN:$ORDER:$CC_SHIM_CLAIM"\n')
    fs.chmodSync(probe, 0o755)
    const r = spawnSync(path.join(DIR, "claude"), [], { env: { ...ENV, CC_SHIM_REAL: probe }, encoding: "utf8" })
    assert.equal(r.stdout.trim(), "from-mjs:abc:mjs", "top-level await works; the claim stops 40-d.sh")
    clearFrags()
  })

  test("9. a hanging fragment does not hang claude", () => {
    frag("10-good.sh", "export CLAUDE_CODE_OAUTH_TOKEN=tok\nCC_SHIM_CLAIM=good")
    frag("05-hang.sh", "sleep 30")
    let start = Date.now()
    assert.equal(run(), "REAL:tok:good:unset")
    let s = (Date.now() - start) / 1000
    assert.ok(s >= 4.5 && s < 10, `the 5 s cap, took ${s}s`)
    fs.rmSync(path.join(CONFD, "05-hang.sh"))
    frag("05-hang.mjs", "while (true) {}")
    start = Date.now()
    assert.equal(run([], { CC_SHIM_FRAGMENT_TIMEOUT: "1" }), "REAL:tok:good:unset", "a busy loop is cut off too")
    s = (Date.now() - start) / 1000
    assert.ok(s < 4, `CC_SHIM_FRAGMENT_TIMEOUT=1, took ${s}s`)
    fs.rmSync(path.join(CONFD, "05-hang.mjs"))
    frag("05-hang.mjs", "await new Promise(() => setInterval(() => {}, 1000))")
    assert.equal(run([], { CC_SHIM_FRAGMENT_TIMEOUT: "1" }), "REAL:tok:good:unset", "so is an await that never settles")
    fs.rmSync(path.join(CONFD, "05-hang.mjs"))
  })

  test("10. no recursion when resolving via PATH", () => {
    clearFrags()
    const r = spawnSync(path.join(DIR, "claude"), [], { env: { ...ENV, PATH: `${DIR}:${path.join(TMP, "fakebin")}:${ENV.PATH}` }, encoding: "utf8", timeout: 10000 })
    assert.equal(r.stdout.trim(), "REAL:::unset", "resolved the next claude on PATH, not itself")
    // A symlink to the wrapper, elsewhere and earlier on PATH, is still us.
    fs.mkdirSync(path.join(TMP, "linkdir"))
    fs.symlinkSync(path.join(DIR, "claude"), path.join(TMP, "linkdir", "claude"))
    const l = spawnSync(path.join(DIR, "claude"), [], { env: { ...ENV, PATH: `${path.join(TMP, "linkdir")}:${path.join(TMP, "fakebin")}:${ENV.PATH}` }, encoding: "utf8", timeout: 10000 })
    assert.equal(l.stdout.trim(), "REAL:::unset")
    fs.rmSync(path.join(TMP, "linkdir"), { recursive: true })
  })

  test("11. teamclaude migration", () => {
    fs.mkdirSync(path.join(HOME, ".local", "share", "teamclaude-shim"), { recursive: true })
    fs.writeFileSync(path.join(HOME, ".local", "share", "teamclaude-shim", "claude"), "")
    fs.appendFileSync(path.join(HOME, ".zshenv"), '\n# teamclaude shim\n. "$HOME/.local/share/teamclaude-shim/env"\n')
    cli(["install"])
    assert.doesNotMatch(fs.readFileSync(path.join(HOME, ".zshenv"), "utf8"), /teamclaude-shim/)
    assert.ok(!fs.existsSync(path.join(HOME, ".local", "share", "teamclaude-shim")))
    assert.equal(nlines(".zshenv"), 1, "cc-shim line still present exactly once")
  })

  test("12. --account is consumed, routed, or refused", () => {
    clearFrags()
    // Routing is what makes the launch legal: this fragment plays the gateway fragment's part.
    frag("10-route.sh", 'export CC_SHIM_ROUTED="${CC_SHIM_ACCOUNT-}"')
    const out = (args) => acct(args).stdout.trim()
    assert.equal(out(["--account", "x", "-p", "hi"]), "USE:x ARGV:[-p][hi]")
    assert.equal(out(["-p", "hi", "--account", "x"]), "USE:x ARGV:[-p][hi]")
    assert.equal(out(["--dsp", "--agent", "m", "--account", "x"]), "USE:x ARGV:[--dsp][--agent][m]")
    assert.equal(out(["--account=x", "-p", "hi"]), "USE:x ARGV:[-p][hi]")
    assert.equal(out(["--acct", "x", "-p", "hi"]), "USE:x ARGV:[-p][hi]")
    assert.equal(out(["--acct=x", "-p", "hi"]), "USE:x ARGV:[-p][hi]")
    assert.equal(out(["--account", "a", "-p", "hi", "--account", "b"]), "USE:b ARGV:[-p][hi]")
    assert.equal(out(["-p", "hi", "--", "--account", "x"]), "USE: ARGV:[-p][hi][--][--account][x]")
    assert.equal(out([]), "USE: ARGV:")
    assert.equal(out(["--account", "x"]), "USE:x ARGV:")
    frag("10-route.mjs", "process.env.CC_SHIM_ROUTED = process.env.CC_SHIM_ACCOUNT ?? ''")
    fs.rmSync(path.join(CONFD, "10-route.sh"))
    assert.equal(out(["--account", "y"]), "USE:y ARGV:", "an .mjs fragment routes as well")

    // A flag after a bare --account is not its name: the shim asks the picker, which is absent here.
    const bare = acct(["--account", "-p", "hi"], { PATH: "/usr/bin:/bin" })
    assert.match(bare.stderr, new RegExp(`needs cc-gateway or ${BRAND_CLI} \\(the gateway's account list\\) to pick from`))
    assert.equal(bare.status, 127)
    // The brand words are found from the module's real location, so a symlinked wrapper still reaches them.
    fs.mkdirSync(path.join(TMP, "linked"))
    fs.symlinkSync(path.join(DIR, "claude"), path.join(TMP, "linked", "claude"))
    const linked = spawnSync(path.join(TMP, "linked", "claude"), ["--account", "-p", "hi"], { env: { ...ENV, CC_SHIM_REAL: ARGFAKE, PATH: "/usr/bin:/bin" }, encoding: "utf8" })
    assert.match(linked.stderr, new RegExp(`needs cc-gateway or ${BRAND_CLI} `))
    // A picker on PATH answers on stdout; its menu (stderr) is the human's.
    const picker = path.join(TMP, "pickbin")
    fs.mkdirSync(picker)
    fs.writeFileSync(path.join(picker, BRAND_CLI), '#!/bin/sh\necho "menu" >&2\necho "picked@x"\n')
    fs.chmodSync(path.join(picker, BRAND_CLI), 0o755)
    const picked = acct(["--account", "-p"], { PATH: `${picker}:/usr/bin:/bin` })
    assert.equal(picked.stdout.trim(), "USE:picked@x ARGV:[-p]")
    // cc-gateway installed on its own is asked first, as itself: `cc-gateway account choose`.
    const solo = path.join(TMP, "solobin")
    fs.mkdirSync(solo)
    fs.writeFileSync(path.join(solo, "cc-gateway"), '#!/bin/sh\n[ "$*" = "account choose" ] && echo "solo@x" || echo "wrong args: $*"\n')
    fs.chmodSync(path.join(solo, "cc-gateway"), 0o755)
    const soloPicked = acct(["--account", "-p"], { PATH: `${solo}:${picker}:/usr/bin:/bin` })
    assert.equal(soloPicked.stdout.trim(), "USE:solo@x ARGV:[-p]")

    // Routed-or-refuse: the one place this wrapper does NOT fail open.
    clearFrags()
    const refused = acct(["--account", "x"])
    assert.equal(refused.status, 1)
    assert.match(refused.stderr, /refusing to launch on another account/)
    assert.equal(acct(["--prefer", "x"]).status, 0, "--prefer is exempt: rotating is what it asked for")
    // A parent session's CC_SHIM_ROUTED does not count for this launch.
    assert.equal(acct(["--account", "x"], { CC_SHIM_ROUTED: "x" }).status, 1)
  })

  test("12b. claude's exit code comes back, on the exec road and the child road", () => {
    assert.equal(acct([], { FAKE_EXIT: "42" }).status, 42)
    assert.equal(acct([], { FAKE_EXIT: "42", CC_SHIM_NO_EXEC: "1" }).status, 42)
    // The child road passes a SIGTERM sent to the wrapper on to claude.
    const waiter = path.join(TMP, "fakebin", "claude-wait")
    fs.writeFileSync(waiter, "#!/bin/sh\ntrap 'echo got-term; exit 7' TERM\necho ready\nwhile :; do sleep 0.05; done\n")
    fs.chmodSync(waiter, 0o755)
    const script = `
      const { spawn } = require("node:child_process")
      const c = spawn(${JSON.stringify(path.join(DIR, "claude"))}, [], { env: { ...process.env, CC_SHIM_REAL: ${JSON.stringify(waiter)}, CC_SHIM_NO_EXEC: "1" } })
      let out = ""
      c.stdout.on("data", (d) => { out += d; if (out.includes("ready")) c.kill("SIGTERM") })
      c.on("exit", (code) => { console.log(JSON.stringify({ code, out })) })`
    const r = spawnSync(process.execPath, ["-e", script], { env: ENV, encoding: "utf8", timeout: 10000 })
    const got = JSON.parse(r.stdout)
    assert.match(got.out, /got-term/)
    assert.equal(got.code, 7)
  })

  test("13. the system prompt file is prepended as a flag", () => {
    clearFrags()
    const SP = path.join(HOME, ".config", "cc-shim", "system-prompt.md")
    const sp = (args, extra) => acct(args, extra).stdout.trim()
    assert.equal(sp(["-p", "hi"]), "USE: ARGV:[-p][hi]", "missing file injects nothing")
    fs.writeFileSync(SP, "")
    assert.equal(sp(["-p", "hi"]), "USE: ARGV:[-p][hi]", "empty file injects nothing")
    fs.writeFileSync(SP, "be brief\n")
    assert.equal(sp(["-p", "hi"]), `USE: ARGV:[--append-system-prompt-file][${SP}][-p][hi]`)
    assert.equal(sp(["-p", "hi"], { CC_SYSPROMPT_FLAG: "--system-prompt-file" }), `USE: ARGV:[--system-prompt-file][${SP}][-p][hi]`)
    const other = path.join(TMP, "other.md")
    fs.writeFileSync(other, "x\n")
    assert.equal(sp(["-p", "hi"], { CC_SYSPROMPT_FILE: other }), `USE: ARGV:[--append-system-prompt-file][${other}][-p][hi]`)
    fs.rmSync(SP)
  })

  test("the stub reaches claude even when node is gone", () => {
    const stub = fs.readFileSync(path.join(DIR, "claude"), "utf8").replace(/^node='.*'$/m, "node='/nonexistent/node'")
    const lonely = path.join(TMP, "lonely")
    fs.mkdirSync(lonely)
    fs.writeFileSync(path.join(lonely, "claude"), stub)
    fs.chmodSync(path.join(lonely, "claude"), 0o755)
    const r = spawnSync(path.join(lonely, "claude"), ["-p", "x"], { env: { ...ENV, PATH: "/usr/bin:/bin", CC_SHIM_REAL: ARGFAKE }, encoding: "utf8" })
    assert.equal(r.stdout.trim(), "USE: ARGV:[-p][x]")
    assert.match(r.stderr, /node not found — running claude without the shim/)
  })

  test("status --json and doctor", () => {
    clearFrags()
    const env = { PATH: `${DIR}:${path.join(TMP, "fakebin")}:${ENV.PATH}` }
    frag("05-x.mjs", 'process.env.CC_SHIM_CLAIM = "x-router"')
    const s = JSON.parse(cli(["status", "--json"], env).stdout)
    assert.equal(s.wrapper, path.join(DIR, "claude"))
    assert.equal(s.present, true)
    assert.equal(s.pathPos, 1)
    assert.equal(s.claude, path.join(DIR, "claude"))
    assert.equal(s.real, FAKE)
    // Linux also wires ~/.config/environment.d (systemd user sessions), by design
    assert.deepEqual(s.wired, process.platform === "linux" ? [".zshenv", "environment.d"] : [".zshenv"])
    assert.equal(s.confd, CONFD)
    assert.deepEqual(s.fragments, [{ file: "05-x.mjs", claim: "x-router", runs: true }])

    let d = cli(["doctor", "--json"], env)
    let doc = JSON.parse(d.stdout)
    assert.equal(doc.fail, 0, JSON.stringify(doc.checks, null, 1))
    assert.equal(d.status, 0)
    assert.ok(doc.checks.some((c) => c.level === "PASS" && /matches its source/.test(c.msg)))

    frag("06-bad.mjs", "if ( {")
    frag("07-bad.sh", "if [ ; then")
    fs.chmodSync(path.join(CONFD, "05-x.mjs"), 0o644)
    d = cli(["doctor", "--json"], env)
    doc = JSON.parse(d.stdout)
    const msgs = doc.checks.map((c) => `${c.level} ${c.msg}`)
    assert.ok(msgs.includes("FAIL syntax error in 06-bad.mjs"), msgs.join("\n"))
    assert.ok(msgs.includes("FAIL syntax error in 07-bad.sh"))
    assert.ok(msgs.includes("WARN 05-x.mjs is group/world readable — it may hold tokens"))
    assert.equal(d.status, 1)

    // Not first on PATH: named, with both positions.
    const behind = JSON.parse(cli(["doctor", "--json"], { PATH: `${path.join(TMP, "fakebin")}:${DIR}:${ENV.PATH}` }).stdout)
    assert.ok(behind.checks.some((c) => c.level === "FAIL" && /'claude' resolves to .*fakebin\/claude \(PATH pos 1\) — shim is at pos 2/.test(c.msg)))
    clearFrags()
  })

  test("install takes away the old gateway fragment once the native one is there", () => {
    clearFrags()
    frag("05-brain-gateway.sh", "CC_SHIM_CLAIM=ak-gateway")
    cli(["install"])
    assert.ok(fs.existsSync(path.join(CONFD, "05-brain-gateway.sh")), "alone, it still routes: kept")
    frag("05-gateway.mjs", 'process.env.CC_SHIM_CLAIM = "ak-gateway"')
    const r = cli(["install"])
    assert.match(r.stdout, /removed .*05-brain-gateway\.sh/)
    assert.deepEqual(fs.readdirSync(CONFD), ["05-gateway.mjs"])
    clearFrags()
  })

  test("rc wiring for bash, fish and a login .profile", () => {
    const home2 = path.join(TMP, "home2")
    fs.mkdirSync(path.join(home2, ".config", "fish"), { recursive: true })
    fs.writeFileSync(path.join(home2, ".profile"), "# mine\n")
    const env = { HOME: home2, SHELL: "/bin/bash" }
    const r = cli(["install"], env)
    assert.equal(r.status, 0, r.stderr)
    const has = (rc) => fs.readFileSync(path.join(home2, rc), "utf8").split("\n").filter((l) => l.includes("# cc-shim")).length
    assert.equal(has(".bashrc"), 1)
    assert.ok(!fs.existsSync(path.join(home2, ".bash_profile")), "never created: it would hide ~/.profile from a bash login")
    assert.equal(has(".profile"), 1, "an existing .profile is wired too (sh, dash, a bash login)")
    assert.equal(fs.readFileSync(path.join(home2, ".profile"), "utf8"), '# mine\n\n. "$HOME/.local/share/cc-shim/env"  # cc-shim\n')
    assert.ok(!fs.existsSync(path.join(home2, ".zshenv")), "a bash user gets no .zshenv")
    const fish = fs.readFileSync(path.join(home2, ".config", "fish", "conf.d", "cc-shim.fish"), "utf8")
    assert.match(fish, /set -gx PATH "\$HOME\/\.local\/share\/cc-shim" \$PATH/)
    const envd = path.join(home2, ".config", "environment.d", "cc-shim.conf")
    assert.equal(fs.existsSync(envd), process.platform === "linux", "environment.d on Linux only")
    // An XDG data home moves the shim, and the rc line follows it.
    const home3 = path.join(TMP, "home3")
    fs.mkdirSync(home3)
    cli(["install"], { HOME: home3, SHELL: "/bin/zsh", XDG_DATA_HOME: path.join(home3, "data"), XDG_CONFIG_HOME: path.join(home3, "cfg") })
    assert.ok(fs.existsSync(path.join(home3, "data", "cc-shim", "claude")))
    assert.ok(fs.existsSync(path.join(home3, "cfg", "cc-shim", "conf.d")))
    assert.match(fs.readFileSync(path.join(home3, ".zshenv"), "utf8"), /\. "\$HOME\/data\/cc-shim\/env" {2}# cc-shim/)
    // Uninstall takes every wire back out.
    cli(["uninstall"], env)
    for (const rc of [".bashrc", ".profile"]) assert.equal(has(rc), 0, rc)
    assert.ok(!fs.existsSync(path.join(home2, ".config", "fish", "conf.d", "cc-shim.fish")))
    assert.ok(!fs.existsSync(envd))
  })

  test("bash: ~/.profile is created, ~/.bash_profile only ever wired when it is the user's, and one of ours alone is removed", () => {
    const rcs = (h, rc) => path.join(h, rc)
    const tag = (h, rc) => fs.readFileSync(rcs(h, rc), "utf8").split("\n").filter((l) => l.includes("# cc-shim")).length
    // A bare home: .bashrc and .profile, no .bash_profile.
    const bare = path.join(TMP, "home-bare")
    fs.mkdirSync(bare)
    assert.equal(cli(["install"], { HOME: bare, SHELL: "/bin/bash" }).status, 0)
    assert.equal(tag(bare, ".bashrc"), 1)
    assert.equal(tag(bare, ".profile"), 1, "created: bash logins read it when there is no .bash_profile")
    assert.ok(!fs.existsSync(rcs(bare, ".bash_profile")))
    // The user's own .bash_profile is wired, and kept on uninstall.
    const own = path.join(TMP, "home-own")
    fs.mkdirSync(own)
    fs.writeFileSync(rcs(own, ".bash_profile"), "[ -f ~/.bashrc ] && . ~/.bashrc\n")
    cli(["install"], { HOME: own, SHELL: "/bin/bash" })
    assert.equal(tag(own, ".bash_profile"), 1)
    cli(["uninstall"], { HOME: own, SHELL: "/bin/bash" })
    assert.equal(fs.readFileSync(rcs(own, ".bash_profile"), "utf8").trim(), "[ -f ~/.bashrc ] && . ~/.bashrc")
    // One an earlier install made — nothing but our line — goes: doctor warns, install removes it.
    const old = path.join(TMP, "home-old")
    fs.mkdirSync(old)
    fs.writeFileSync(rcs(old, ".bash_profile"), '\n. "$HOME/.local/share/cc-shim/env"  # cc-shim\n')
    assert.ok(onlyOurLine(fs.readFileSync(rcs(old, ".bash_profile"), "utf8")))
    assert.ok(!onlyOurLine("export X=1\n. x  # cc-shim\n") && !onlyOurLine("\n"))
    const doc = JSON.parse(cli(["doctor", "--json"], { HOME: old, SHELL: "/bin/bash" }).stdout)
    assert.ok(doc.checks.some((c) => c.level === "WARN" && /~\/\.bash_profile holds only the cc-shim line/.test(c.msg)))
    const r = cli(["install"], { HOME: old, SHELL: "/bin/bash" })
    assert.match(r.stdout, /removed {4}~\/\.bash_profile/)
    assert.ok(!fs.existsSync(rcs(old, ".bash_profile")))
    assert.equal(tag(old, ".profile"), 1)
  })

  test(".sh fragments import where env has no -0 (busybox): printenv -0, else node — and doctor says which", () => {
    clearFrags()
    frag("10-good.sh", "export CLAUDE_CODE_OAUTH_TOKEN=tok\nCC_SHIM_CLAIM=good")
    const nozero = path.join(TMP, "nozero")
    fs.mkdirSync(nozero, { recursive: true })
    // A busybox-like env and printenv: -0 is an unknown option. The fake claude's #!/usr/bin/env is absolute, unaffected.
    for (const tool of ["env", "printenv"]) {
      fs.writeFileSync(path.join(nozero, tool), `#!/bin/sh\nfor a in "$@"; do [ "$a" = -0 ] && { echo "${tool}: unrecognized option: 0" >&2; exit 1; }; done\nexec /usr/bin/${tool} "$@"\n`)
      fs.chmodSync(path.join(nozero, tool), 0o755)
    }
    const PATH = `${nozero}:${ENV.PATH}`
    assert.equal(run([], { PATH }), "REAL:tok:good:unset", "imported through node")
    const doc = JSON.parse(cli(["doctor", "--json"], { PATH }).stdout)
    assert.ok(doc.checks.some((c) => c.level === "INFO" && /\.sh fragments import through node/.test(c.msg)), JSON.stringify(doc.checks))
    const docOk = JSON.parse(cli(["doctor", "--json"]).stdout)
    assert.ok(docOk.checks.some((c) => c.level === "PASS" && /\.sh fragments import \(env -0\)/.test(c.msg)))
    assert.deepEqual(parseEnvDump("A=1\0B=x=y\0\0"), { A: "1", B: "x=y" })
    clearFrags()
  })

  test("the cc-shim stub runs the installer's verbs", () => {
    const r = spawnSync(path.join(DIR, "cc-shim"), ["help"], { env: ENV, encoding: "utf8" })
    assert.equal(r.status, 0)
    assert.match(r.stdout, /cc-shim doctor/)
    const named = spawnSync(path.join(DIR, "cc-shim"), ["help"], { env: { ...ENV, CC_SHIM_INVOKED_AS: `${BRAND_CLI} shim` }, encoding: "utf8" })
    assert.match(named.stdout, new RegExp(`${BRAND_CLI} shim doctor`))
    assert.equal(spawnSync(path.join(DIR, "cc-shim"), ["nope"], { env: ENV }).status, 1)
  })

  test("14. uninstall", () => {
    frag("30-keepme.sh", "# user data")
    const r = cli(["uninstall"])
    assert.equal(r.status, 0)
    for (const f of ["claude", "cc-shim", "cc-shim.mjs", "env", "_brand.sh", ".src"]) assert.ok(!fs.existsSync(path.join(DIR, f)), `${f} removed`)
    assert.ok(!fs.existsSync(DIR))
    assert.equal(nlines(".zshenv"), 0)
    assert.ok(fs.existsSync(path.join(CONFD, "30-keepme.sh")), "conf.d fragments preserved")
    cli(["uninstall", "--purge"])
    assert.ok(!fs.existsSync(path.join(HOME, ".config", "cc-shim")), "--purge takes them too")
  })
})
