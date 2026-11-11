#!/usr/bin/env python3
"""All live v15 practices with their ladders and the rung rulings, in one file for the cheap version's single AI pass.
Reuses the lab packets' rules text so both versions judge rungs the same way.

    python3 practices_packet.py RUN_DIR   → RUN_DIR/cheap-practices.md
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("packets", os.path.join(HERE, "..", "2026-11-02-evidence", "packets.py"))
packets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packets)


def main(run):
    S = json.load(open(os.path.join(packets.V15, "engine", "substrate.json")))
    live = set(packets.live_practices(S))
    n = len(live)  # counted from the substrate: a number written here drifted to 33 when 26 were live
    L = [f"# The {n} practices, their ladders, and how a level is read", "", packets.RULES, ""]
    for d in S["disciplines"]:
        L.append(f"\n## {d['name']} (`{d['id']}`)")
        for pid in (x for x in d["practices"] if x in live):
            p = S["practices"][pid]
            L.append(f"\n### `{pid}`: {p['name']}")
            L.append(f"What it is: {packets.short(p.get('what_you_do'), 30)}")
            for k in ("L2", "L3", "L4"):
                if (p.get("ladder") or {}).get(k):
                    L.append(f"- {k}: {packets.short(p['ladder'][k], 45)}")
    t = "\n".join(L)
    open(os.path.join(run, "cheap-practices.md"), "w").write(t)
    print(f"cheap-practices.md: {len(t.split())} words")


if __name__ == "__main__":
    main(os.path.expanduser(sys.argv[1]))
