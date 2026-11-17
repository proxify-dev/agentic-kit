#!/usr/bin/env python3
"""Which projects the review reads when the person names them. `run.py pre --only NAME` (review these, and no others)
and `--skip NAME` (leave these out of the review), each repeatable, reach both pickers: anatomy_scan.py, which ranks,
and setup_map.py, which fills empty slots and is the list every tab walks. Both must give one answer, or the tabs name
different projects. With neither flag nothing here runs and the pick is the busiest projects, as before.

A NAME is a folder name ("api-server", any case) or a path ("~/code/api-server"; a worktree or a folder inside a repo
counts for its repo). It fails closed, because a project someone asked to leave out must not land in the review:
- a --skip that matches no project the run knows stops the run: a typo would otherwise review the very repo;
- a --skip that several repos answer to (clones under one folder name) leaves every one of them out;
- an --only that matches nothing, that names a repo the file scan never read (its review would say "no AGENTS.md of
  yours" about files nobody opened), that two chosen repos share a folder name with (the briefs and the page key a
  project by that name), or that names more than MAX_CHOSEN projects stops the run;
- an --only that several repos answer to takes the busiest of them and prints the paths it passed over.
A name in both lists stops the run. What --skip covers is the review: the projects read file by file, which make the
Review tab and the L3 check. The rest of the page still counts that repo's sessions, commits and helpers with the others.
A name typed in answer to the question before the scan (`run.py candidates --only NAME`) is first read by
`resolve_typed`: a unique start of a folder name ("api" for "api-server") is taken and said; anything else stops with
the closest names. `pre` itself takes no such guess: the skill passes it the keys `candidates` printed.
Standard library only."""
import difflib
import os

MAX_CHOSEN = 5   # one AI reader per project; more is a cost the person did not see coming
CHOSEN = "you"            # picked_by: the person named the projects (--only), and they are not simply their busiest
CONFIRMED = "confirmed"   # picked_by: --only named exactly the busiest (the skill passes a confirmed default this way)
BY_ONLY = (CHOSEN, CONFIRMED)   # either way --only took them: nothing is filled in around them


def picked_by(chosen, default):
    """CONFIRMED when the projects --only took are the busiest the run would read with no flag (same repos, any order),
    else CHOSEN. The page then says "your busiest 3, which you confirmed", never "chosen by you" for a list the person
    only accepted (11-17)."""
    return CONFIRMED if chosen and set(chosen) == set(default or ()) else CHOSEN


class Refused(ValueError):
    """A choice that can't be honoured as asked: the run stops and prints this, and nothing is reviewed in its place."""


def _is_path(token):
    return os.sep in token or token.startswith("~") or token.startswith(".")


def _abs(token, home):
    t = token.strip()
    if t == "~" or t.startswith("~" + os.sep):
        t = home + t[1:]
    return os.path.normpath(os.path.abspath(t))


def label(root):
    return os.path.basename(os.path.normpath(str(root)))


def matches(token, root, home, resolve):
    """True when TOKEN names ROOT: its folder name (any case), or a path that is ROOT or resolves to it (`resolve`:
    a folder -> the main checkout of its repo, as the picker that calls this resolves a session's folder)."""
    t = (token or "").strip().rstrip(os.sep)
    if not t or not root:
        return False
    root = os.path.normpath(root)
    if _is_path(t):
        p = _abs(t, home)
        r = resolve(p) if os.path.isdir(p) else None
        return os.path.normpath(r or p) == root
    return label(root).lower() == t.lower()


def skipper(skip, home, resolve):
    """root -> True when a --skip names it. With no --skip: always False."""
    skip = [t for t in skip or () if t and t.strip()]
    if not skip:
        return lambda root: False
    return lambda root: any(matches(t, root, home, resolve) for t in skip)


def check_skips(skip, known, home, resolve):
    """Stop when a --skip names no project this run knows (`known`: every repo the pick could take, filling included)."""
    lost = [t for t in skip or () if t and t.strip() and not any(matches(t, r, home, resolve) for r in known)]
    if lost:
        raise Refused(f"--skip {', '.join(repr(t) for t in lost)}: no project of this run has that name or path, so "
                      "nothing was left out. Check the name (a folder name, or a path) and run it again.")


def pool_extras(inv, anat):
    """(the inventory's repos, busiest by sessions that worked in them, then path; the roots of their own rule files;
    the file scan's targets, None without anatomy.json): what either picker may take besides the repos sessions ran in.
    The same order setup_map.pick_projects fills an empty slot with."""
    rows = sorted((inv or {}).get("repos") or [], key=lambda x: (-(x.get("worked_chats") or 0), str(x.get("repo") or "")))
    repos = [os.path.normpath(x["repo"]) for x in rows
             if x.get("repo") and not x.get("not_a_repo") and os.path.isdir(os.path.join(x["repo"], ".git"))]
    roots = sorted({os.path.normpath(x["project"]) for x in (anat or {}).get("instruction_files") or []
                    if x.get("scope") == "project" and x.get("origin") == "own" and x.get("project")})
    targets = ((anat or {}).get("meta") or {}).get("targets") if anat else None
    return list(dict.fromkeys(repos)), roots, targets


def in_reach(root, targets):
    """True when the file scan read this repo's files (it is one of anatomy.json's targets, or below one). `targets`
    None: no file scan to ask, every repo is as unread as the next."""
    if targets is None:
        return True
    root = os.path.normpath(root)
    return any(root == t or root.startswith(t.rstrip(os.sep) + os.sep) for t in (os.path.normpath(x) for x in targets))


def choose(only, ranked, home, resolve, targets=None, skipped=lambda r: False, tilde=lambda p: p):
    """(the repos --only names, in `ranked` order: the busiest first, whose brief carries the extra jobs; notes to print).
    `ranked`: every repo the pick could take, the busiest first. Raises Refused as the module says."""
    only = [t for t in only or () if t and t.strip()]
    chosen, notes = [], []
    for t in only:
        hits = [r for r in ranked if matches(t, r, home, resolve)]
        if not hits:
            raise Refused(f"--only {t!r}: no project of this run has that name or path (the run knows the repos your "
                          "sessions worked in during the last 30 days). Check the name and run it again.")
        if all(skipped(r) for r in hits):
            raise Refused(f"--only {t!r} is also left out by --skip. Name it in one of the two and run it again.")
        hits = [r for r in hits if not skipped(r)]
        read = [r for r in hits if in_reach(r, targets)]
        if not read:
            raise Refused(f"--only {t!r}: this run's file scan did not open {tilde(hits[0])} (it reads the folders your "
                          "sessions used most), so a review of it would judge files nobody read. Choose another project.")
        if read[0] not in chosen:
            chosen.append(read[0])
        if len(read) > 1:
            notes.append(f"--only {t!r}: {len(read)} repos have that name; took {tilde(read[0])} (most sessions), not "
                         + ", ".join(tilde(r) for r in read[1:4]) + (" …" if len(read) > 4 else "") + ". Give a path to take another.")
    if len(chosen) > MAX_CHOSEN:
        raise Refused(f"--only names {len(chosen)} projects; the review reads at most {MAX_CHOSEN} (one AI reader each). "
                      "Choose fewer.")
    names = {}
    for r in chosen:
        names.setdefault(label(r).lower(), []).append(r)
    same = [rs for rs in names.values() if len(rs) > 1]
    if same:
        raise Refused(f"--only: {', '.join(tilde(r) for r in same[0])} share the folder name {label(same[0][0])!r}, and the "
                      "review keys a project by that name. Choose one of them.")
    order = {r: i for i, r in enumerate(ranked)}
    return sorted(chosen, key=lambda r: order.get(r, len(order))), notes


def resolve_typed(tokens, pool, home, resolve, key_of, flag="--only"):
    """(tokens, notes): each typed name as a name the pick takes. A name that already names a project of `pool` (its
    folder name in any case, or its path) is kept; one that is the start of exactly one project's folder name (any case)
    becomes that project's key (`key_of(root)`), with a note saying so. Anything else raises Refused naming the closest
    keys, so a person is asked again and never reviewed on a guess. `pool`: the projects the list shows, busiest first."""
    out, notes = [], []
    for t in tokens or ():
        if not t or not t.strip() or _is_path(t) or any(matches(t, r, home, resolve) for r in pool):
            out.append(t)   # a path is never completed: named_repos and choose read it as it is
            continue
        low = t.strip().lower()
        starts = [r for r in pool if label(r).lower().startswith(low)]
        if len(starts) == 1:
            out.append(key_of(starts[0]))
            notes.append(f"{flag} {t!r}: read as {key_of(starts[0])} ({_tilde(starts[0], home)})")
            continue
        keys = [key_of(r) for r in pool]
        near = [key_of(r) for r in starts][:5] or difflib.get_close_matches(
            t.strip(), keys, n=5, cutoff=0.5) or [k for k in keys if low in k.lower()][:5]
        what = ("several projects start with it" if len(starts) > 1 else "no project with sessions has that name")
        raise Refused(f"{flag} {t!r}: {what}. " + (f"Closest: {', '.join(near)}. " if near else "")
                      + "Give one of the names the list shows.")
    return out, notes


def _tilde(p, home):
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def named_repos(only, home, resolve, is_repo):
    """The repos an --only PATH names, read before anything slow runs, so a chosen repo the logs never ranked can still be
    taken (and the inventory told to open it). Raises Refused for a path that is no folder, or a folder in no git repo
    (the review reads a repo's tracked files and its history). A folder name is left to the pick's pool."""
    out = []
    for t in only or ():
        t = (t or "").strip().rstrip(os.sep)
        if not t or not _is_path(t):
            continue
        p = _abs(t, home)
        if not os.path.isdir(p):
            raise Refused(f"--only {t!r}: there is no folder there. Check the path and run it again.")
        root = resolve(p)
        if not root or not is_repo(root):
            raise Refused(f"--only {t!r}: that folder is not in a git repo, so the review can't read it (it reads a "
                          "repo's tracked files and history). Choose a git repo.")
        if root not in out:
            out.append(root)
    return out
