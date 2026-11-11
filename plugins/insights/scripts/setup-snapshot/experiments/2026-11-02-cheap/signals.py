#!/usr/bin/env python3
"""The cheap version, code part 3: the scan's who-did-it counts → practice rungs, by the signal map in the substrate
(../../../v15/substrate/signals.yaml, built into engine/substrate.json). No AI; standard library only; read-only.

Each count Claude Code writes only when the person acts ("you") can show L2; each count their own mode, hook, agent or
rule file produced unasked ("setup") can show L3. A count sets the rung only when it is a habit (the map's `enough`).
"Probably you", "Can't tell" and "Claude by itself" never set a rung. L4 stays with the AI pass, which reads what they
built. assemble.py folds these rows into the AI pass's rows: the higher rung wins, both reasons stay.

    python3 signals.py RUN_DIR   → RUN_DIR/signal-rows.json

signal-rows.json is read by assemble.py and by the page's Who did it view (view_who.py tags a count L2/L3 from it), so
keep its shape: {"practices": {practice: {"rung", "signals": [{"id", "rung", "says", ...}], "below_bar": [...]}},
"not_in_scan": [ids]}.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUB = os.path.normpath(os.path.join(HERE, "..", "..", "..", "v15", "engine", "substrate.json"))
RANK = {"": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def items_by_id(who_did_it):
    """who_did_it groups → {id: item}. The scan's ids are "<group>.<name>" and never renamed (labels may be)."""
    return {i["id"]: {**i, "group": g["id"]} for g in who_did_it.get("groups") or [] for i in g.get("items") or [] if i.get("id")}


def enough(item, bar):
    """Is this count a habit by the map's bar? n times, in at least `chats` chats (when the scan counted chats),
    and at least `share` of `of` (when the scan gave a total)."""
    n = item.get("n") or 0
    if n < bar.get("n", 1):
        return False
    if bar.get("chats") and item.get("chats") is not None and item["chats"] < bar["chats"]:
        return False
    if bar.get("share") and item.get("of"):
        return n / item["of"] >= bar["share"]
    return True


def sentence(template, item):
    of = item.get("of")
    return template.format(n=item.get("n"), chats=item.get("chats") if item.get("chats") is not None else "several",
                           of=of if of is not None else "", pct=f"{round(100 * item['n'] / of)}%" if of else "")


def rows(inventory, signal_map):
    """{practice: {"rung", "signals": [met, strongest first], "below_bar": [seen but not a habit]}}, plus the map's ids
    this scan doesn't have (an older scan, or an item that moved group and so changed id)."""
    found = items_by_id(inventory.get("who_did_it") or {})
    out, missing = {}, []
    for s in signal_map.get("signals") or []:
        item = found.get(s["id"])
        if item is None:
            missing.append(s["id"])
            continue
        met = enough(item, s["enough"])
        for c in s["counts_for"]:
            row = out.setdefault(c["practice"], {"rung": "", "signals": [], "below_bar": []})
            entry = {"id": s["id"], "who": item["group"], "rung": c["rung"], "n": item.get("n"), "chats": item.get("chats"),
                     "of": item.get("of"), "says": sentence(c["says"], item), "proof": item.get("proof") or ""}
            (row["signals"] if met else row["below_bar"]).append(entry)
            if met and RANK[c["rung"]] > RANK[row["rung"]]:
                row["rung"] = c["rung"]
    for row in out.values():
        row["signals"].sort(key=lambda e: (-RANK[e["rung"]], -(e["n"] or 0)))
    return {p: r for p, r in out.items() if r["signals"] or r["below_bar"]}, missing


def main(run):
    inv = json.load(open(os.path.join(run, "inventory.json")))
    signal_map = json.load(open(SUB)).get("signals") or {}
    if not inv.get("who_did_it"):
        print("signals: this scan has no who_did_it block; no code rungs")
    out, missing = rows(inv, signal_map)
    json.dump({"practices": out, "not_in_scan": missing}, open(os.path.join(run, "signal-rows.json"), "w"), indent=1,
              ensure_ascii=False)
    rung = {p: r["rung"] for p, r in out.items() if r["rung"]}
    print(f"signal-rows.json: {len(rung)} practices get a rung from who-did-it counts "
          f"({sum(v == 'L3' for v in rung.values())} at L3, {sum(v == 'L2' for v in rung.values())} at L2)")
    if missing and inv.get("who_did_it"):
        print(f"   not in this scan, so no rung from them: {', '.join(missing)}")


if __name__ == "__main__":
    main(os.path.expanduser(sys.argv[1]))
