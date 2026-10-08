"""runner — apply, undo or resume one move from its own folder, with nothing but python3.

    <home>/moves/<id>/
        plan.json       what the planner decided: the steps, in order, as plain JSON
        journal.jsonl   write-ahead: {"seq","status":"begun"} before a step, "done"+result after,
                        "undone" once reverted, "failed"+error when it raised
        backup/         clonefile copies of every file edited in place, taken just before the edit
        stash/          derived things set aside (a moved folder's index cache, a venv)
        engine/         this file, ops.py and lineage.py, copied at plan time

The CLI plans with the whole package, then hands off to the copy in engine/:

    python3 <dir>/engine/runner.py apply|undo|resume <dir>

From then on nothing is imported from the repo, the release clone or a venv, so a move can relocate
any of them — including the checkout the planner ran from. The undo command is printed before the
first step and needs nothing but this folder.

A step that raised leaves the journal at "begun"; `undo` asks that step's revert to look at the disk
and put back what it finds. AK_MOVE_CRASH_AFTER=n exits hard after step n — the tests use it to
prove undo and resume from every point.
"""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ops  # noqa: E402
from _brand import env  # noqa: E402  (frozen beside this file)


def _journal(d):
    return os.path.join(d, "journal.jsonl")


def load(d):
    with open(os.path.join(d, "plan.json"), encoding="utf-8") as f:
        plan = json.load(f)
    state = {}
    try:
        with open(_journal(d), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                state.setdefault(e["seq"], {}).update(e)
    except OSError:
        pass
    return plan, state


def _log(d, entry):
    data = (json.dumps({**entry, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n").encode()
    fd = os.open(_journal(d), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _ctx(d, plan):
    return {"dir": d, "id": plan["id"]}


def apply(d, resume=False):
    plan, state = load(d)
    ctx = _ctx(d, plan)
    crash = int(env("MOVE_CRASH_AFTER") or 0)
    if not resume and state:
        return _say({"ok": False, "error": "this move already ran — resume or undo it"}, 2)
    for seq, step in enumerate(plan["steps"], 1):
        st = state.get(seq, {}).get("status")
        if st in ("done", "undone"):
            continue
        _log(d, {"seq": seq, "status": "begun", "step": step["name"]})
        try:
            apply_fn, _ = ops.OPS[step["op"]]
            result = apply_fn(step["args"], ctx)
        except Exception as e:
            _log(d, {"seq": seq, "status": "failed", "error": f"{type(e).__name__}: {e}",
                     "trace": traceback.format_exc()[-1500:]})
            undone = undo(d, quiet=True)
            return _say({"ok": False, "failed": step["name"], "error": f"{e}", "undone": undone}, 1)
        _log(d, {"seq": seq, "status": "done", "result": result})
        if crash and seq == crash:
            os._exit(9)
    return _say({"ok": True, "steps": len(plan["steps"])}, 0)


def undo(d, quiet=False):
    plan, state = load(d)
    ctx = _ctx(d, plan)
    undone, errors = 0, []
    for seq in range(len(plan["steps"]), 0, -1):
        e = state.get(seq)
        if not e or e.get("status") == "undone":
            continue
        step = plan["steps"][seq - 1]
        result = e.get("result") if e.get("status") == "done" else None
        try:
            _, revert_fn = ops.OPS[step["op"]]
            revert_fn(step["args"], result, ctx)
            _log(d, {"seq": seq, "status": "undone"})
            undone += 1
        except Exception as ex:  # keep going: one stuck step must not strand the rest
            errors.append(f"{step['name']}: {ex}")
    if quiet:
        return {"undone": undone, "errors": errors}
    return _say({"ok": not errors, "undone": undone, "errors": errors}, 0 if not errors else 1)


def _say(obj, code):
    print(json.dumps(obj))
    return code


if __name__ == "__main__":
    verb, d = sys.argv[1], os.path.abspath(sys.argv[2])
    if verb == "apply":
        sys.exit(apply(d))
    if verb == "resume":
        sys.exit(apply(d, resume=True))
    if verb == "undo":
        sys.exit(undo(d))
    print(json.dumps({"ok": False, "error": f"unknown verb {verb}"}))
    sys.exit(64)
