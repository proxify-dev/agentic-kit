"""A hook's words for the model go in additionalContext or permissionDecisionReason, never in systemMessage alone.

systemMessage reaches only the person; the model never reads it. The first hook built for a CLAUDE.md edit
(~/code/app scripts/check-config-edit.sh, found 2026-09-27 in session 0d5ed015) put its rule
for the model there, so the rule never landed. memory's session-start.py says the split in one line:
"`additionalContext` is the plain index for Claude … `systemMessage` is what the person sees".

Read statically, since a hook's output is built at runtime: every place a hook under plugins/*/hooks/ (and
every script a hooks.json runs) sets systemMessage must, in the same function, also build additionalContext
or permissionDecisionReason. That pairs "what the person sees" with "what the model reads". A systemMessage
with nothing beside it is the config-check bug, unless it is listed in PERSON_ONLY with why only the person
needs it (plugins/HOOKS.md §4: saying what a hook created is the person's line).
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODEL_FIELDS = {"additionalContext", "permissionDecisionReason"}

# (plugin-relative path, function) → why only the person reads it. Empty today: every systemMessage in
# plugins/ rides with an additionalContext.
PERSON_ONLY: dict[tuple[str, str], str] = {}


def hook_files() -> list[Path]:
    """Every Python or shell hook under plugins/*/hooks/, and every file a plugin's hooks.json runs."""
    files = set(ROOT.glob("plugins/*/hooks/*.py")) | set(ROOT.glob("plugins/*/hooks/*.sh"))
    for hj in ROOT.glob("plugins/*/hooks/hooks.json"):
        plugin = hj.parent.parent
        for cmd in re.findall(r'"command"\s*:\s*"((?:[^"\\]|\\.)*)"', hj.read_text(encoding="utf-8")):
            for rel in re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}"?/([\w./-]+\.(?:py|sh))', cmd.replace('\\"', '"')):
                if (plugin / rel).is_file():
                    files.add(plugin / rel)
    return sorted(files)


def _scopes(tree: ast.AST):
    """(name, node) for the module and every function in it, innermost last."""
    yield "<module>", tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def _own_strings(scope: ast.AST) -> set[str]:
    """String constants in SCOPE, not counting those inside the functions it defines."""
    out, stack = set(), list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
        stack.extend(ast.iter_child_nodes(node))
    return out


def unpaired_python(source: str) -> list[str]:
    """Functions of SOURCE that set systemMessage (a dict key, or out["systemMessage"] = …) and build
    neither additionalContext nor permissionDecisionReason."""
    tree = ast.parse(source)
    return [name for name, scope in _scopes(tree)
            if "systemMessage" in (s := _own_strings(scope)) and not s & MODEL_FIELDS]


def unpaired_shell(source: str) -> bool:
    return "systemMessage" in source and not any(f in source for f in MODEL_FIELDS)


def findings() -> list[tuple[str, str]]:
    out = []
    for f in hook_files():
        rel = str(f.relative_to(ROOT / "plugins"))
        text = f.read_text(encoding="utf-8")
        if f.suffix == ".py":
            out += [(rel, fn) for fn in unpaired_python(text)]
        elif unpaired_shell(text):
            out.append((rel, "<script>"))
    return out


def emitters() -> list[str]:
    return [str(f.relative_to(ROOT / "plugins")) for f in hook_files() if "systemMessage" in f.read_text(encoding="utf-8")]


def test_the_scan_sees_the_hooks():
    # per plugin, not a count: the public kit carries three plugins, the workspace a dozen
    seen = {f.relative_to(ROOT / "plugins").parts[0] for f in hook_files()}
    wired = {hj.parent.parent.name for hj in ROOT.glob("plugins/*/hooks/hooks.json")
             if re.search(r"\.(?:py|sh)\b", hj.read_text(encoding="utf-8"))}
    assert wired and wired <= seen, f"hooks.json runs files the scan does not see: {sorted(wired - seen)}; the glob or the tree moved"
    assert emitters(), "found no systemMessage in plugins/; the scan is reading the wrong files"


def test_model_facing_text_never_rides_systemmessage_alone():
    bad = [f"{path}: {fn}()" for path, fn in findings() if (path, fn) not in PERSON_ONLY]
    assert not bad, (
        "these set systemMessage (only the person reads it) and build no additionalContext or "
        "permissionDecisionReason for the model:\n  " + "\n  ".join(bad) +
        "\nPut what the model must read in hookSpecificOutput.additionalContext (permissionDecisionReason on a "
        "deny). If only the person needs it, list it in PERSON_ONLY with why.")


def test_every_exception_still_exists():
    live = set(findings())
    stale = [k for k in PERSON_ONLY if k not in live]
    assert not stale, f"PERSON_ONLY names what no longer sets systemMessage alone: {stale}"


CONFIG_CHECK_LIKE = '''
import json, sys
def main():
    path = json.load(sys.stdin)["tool_input"]["file_path"]
    print(json.dumps({"systemMessage": f"You are editing {path}: you MUST save a project memory."}))
'''
PAIRED = '''
import json
def main(ctx, shown):
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}
    if shown:
        out["systemMessage"] = shown
    print(json.dumps(out))
'''
DENY = '''
import json
def emit(text):
    out = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": text}
    doc = {"hookSpecificOutput": out}
    doc["systemMessage"] = "blocked: " + text
    print(json.dumps(doc))
'''
HELPER_ELSEWHERE = '''
def ctx():
    return {"additionalContext": "x"}
def main():
    print({"systemMessage": "only the person sees this"})
'''


@pytest.mark.parametrize("source,want", [(CONFIG_CHECK_LIKE, ["main"]), (PAIRED, []), (DENY, []), (HELPER_ELSEWHERE, ["main"])],
                         ids=["config-check-like", "paired", "deny", "context-built-elsewhere"])
def test_what_the_scan_flags(source, want):
    assert unpaired_python(source) == want


def test_a_shell_hook_is_read_too():
    assert unpaired_shell(json.dumps({"systemMessage": "rule for the model"}))
    assert not unpaired_shell('jq -n --arg c "$ctx" \'{hookSpecificOutput:{additionalContext:$c}, systemMessage:"x"}\'')
