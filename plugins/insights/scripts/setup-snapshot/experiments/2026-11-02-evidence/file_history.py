"""The commits that touched a path, for many paths of one repo, from one git call per repo.

`git log -- <path>` once per file is the slow way to ask "who changed this file": one process per file, each walking the
whole history. This module reads the history once per repo (`git log --name-only`, plus one more call for the merge
commits) and answers every path from memory with what `git log -- <path>` would have printed: the same commits, in
the same order, after the same history simplification (a merge that equals one of its parents is not shown, and only
the first such parent is followed; a merge that differs from all of them is shown and all its parents are followed).

    h = Histories(run)               # run(args, cwd, timeout) -> subprocess.CompletedProcess | None, the caller's git
    h.rows(root, "skills/x")         # [(author date, author email, author name), ...] newest first, or None

None means "not answered here": a path git would read as a pattern (`*`, `?`, `[`, a leading `:`), a repo with no
commits or too many, a git call that failed. The caller then asks git about that one path, as it did before, so the
answers are the same by construction. Only a repo asked about `threshold` times or more is read whole: for a few
paths one `git log` each is cheaper. Standard library only; no shell; the same on Windows.
"""
import bisect
import re
import subprocess
import threading

REC, FLD = "\x1e", "\x1f"
LOG_FORMAT = f"--format={REC}%H{FLD}%P{FLD}%aI{FLD}%ae{FLD}%an"
_FROM = re.compile(r"^([0-9a-f]{40,64}) \(from ([0-9a-f]{40,64})\)")
_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, '"': 34, "\\": 92}


def unquote(name: str) -> str:
    """A path as `git` prints it when it holds a quote, a backslash, a tab or a newline: "a\\"b" -> a"b."""
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    body, out, i = name[1:-1], bytearray(), 0
    while i < len(body):
        c = body[i]
        if c != "\\" or i + 1 >= len(body):
            out += c.encode("utf-8")
            i += 1
        elif body[i + 1] in _ESCAPES:
            out.append(_ESCAPES[body[i + 1]])
            i += 2
        elif body[i + 1] in "01234567":
            j = i + 1
            while j < len(body) and j < i + 4 and body[j] in "01234567":
                j += 1
            out.append(int(body[i + 1:j], 8) & 255)
            i = j
        else:
            out += c.encode("utf-8")
            i += 1
    return out.decode("utf-8", "replace")


class RepoHistory:
    """The whole history of one repo (what HEAD reaches), indexed by path."""

    def __init__(self):
        self.order: list[str] = []            # commits, in the order `git log` prints them
        self.pos: dict[str, int] = {}
        self.parents: dict[str, list[str]] = {}
        self.who: dict[str, tuple[str, str, str]] = {}
        self.by_path: dict[str, list[tuple[str, int]]] = {}   # path -> [(commit, -1)] or [(merge, parent index)]
        self.merges = 0
        self.paths: list[str] = []

    @classmethod
    def read(cls, root, run, max_commits: int = 80000, timeout: float = 180):
        """The index of `root`, or None (empty repo, too many commits, a failed git call, anything unexpected)."""
        n = run(["rev-list", "--count", "HEAD"], root, 30)
        if n is None or n.returncode != 0 or not n.stdout.strip().isdigit() or not 0 < int(n.stdout) <= max_commits:
            return None
        r = run(["-c", "core.quotepath=off", "log", "--root", "--no-renames", "--name-only", "--no-abbrev-commit", LOG_FORMAT], root, timeout)
        if r is None or r.returncode != 0:
            return None
        h = cls()
        for rec in r.stdout.split(REC)[1:]:
            head, _, files = rec.partition("\n")
            parts = head.split(FLD)
            if len(parts) != 5:
                return None
            sha, ps, when, email, name = parts
            h.pos[sha] = len(h.order)
            h.order.append(sha)
            h.parents[sha] = ps.split()
            h.who[sha] = (when, email, name)
            if len(h.parents[sha]) > 1:
                h.merges += 1
                continue                       # a merge shows no files here; its differences come below
            for p in files.split("\n"):
                if p:
                    h.by_path.setdefault(unquote(p), []).append((sha, -1))
        if h.merges:
            m = run(["-c", "core.quotepath=off", "log", "-m", "--merges", "--root", "--no-renames", "--name-only", "--pretty=oneline", "--no-abbrev-commit"],
                    root, timeout)
            if m is None or m.returncode != 0:
                return None
            cur = None   # (merge, parent index): a parent whose difference is empty prints nothing, so it is named each time
            for line in m.stdout.split("\n"):
                hit = _FROM.match(line)
                if hit:
                    sha, par = hit.groups()
                    if sha not in h.parents or par not in h.parents[sha]:
                        return None
                    cur = (sha, h.parents[sha].index(par))
                elif line and cur:
                    h.by_path.setdefault(unquote(line), []).append(cur)
        h.paths = sorted(h.by_path)
        return h

    def rows(self, rels):
        """[(author date, email, name)] of the commits `git log -- <rels>` prints, in its order; None for a path git would
        treat as a pattern. `rels` is a path (a file, or a folder) or a tuple of them, relative to the repo root."""
        rels = (rels,) if isinstance(rels, str) else tuple(rels)
        hits = set()
        for rel in rels:
            q = rel.replace("\\", "/") if "\\" in rel and "/" not in rel and ":" not in rel[:2] else rel
            if not q or q == "." or q.startswith((":", "/", "..")) or any(c in q for c in "*?[\\") or q.endswith("/"):
                return None
            if q in self.by_path:
                hits.add(q)
            lo, hi = bisect.bisect_left(self.paths, q + "/"), bisect.bisect_left(self.paths, q + "0")
            hits.update(self.paths[lo:hi])
        plain: set[str] = set()                 # commits with one parent (or none) that changed a hit
        merged: dict[str, set[int]] = {}        # merges, and the parents each differs from at a hit
        for p in hits:
            for sha, pi in self.by_path[p]:
                if pi < 0:
                    plain.add(sha)
                else:
                    merged.setdefault(sha, set()).add(pi)
        if not self.merges:
            shown = plain
        else:
            shown, seen, stack = set(), set(), [self.order[0]]
            while stack:
                c = stack.pop()
                if c in seen:
                    continue
                seen.add(c)
                ps = self.parents[c]
                if len(ps) <= 1:
                    if c in plain:
                        shown.add(c)
                    stack.extend(ps)
                    continue
                differs = merged.get(c, ())
                same = [i for i in range(len(ps)) if i not in differs]
                if same:
                    stack.append(ps[same[0]])   # equal to a parent: not shown, only the first such parent is followed
                else:
                    shown.add(c)
                    stack.extend(ps)
        return [self.who[s] for s in sorted(shown, key=self.pos.__getitem__)]


class Histories:
    """Per-repo indexes, built on the `threshold`-th question about a repo."""

    def __init__(self, run, threshold: int = 8):
        self.run, self.threshold = run, threshold
        self._asked: dict = {}
        self._index: dict = {}
        self._lock = threading.Lock()

    def rows(self, root, rels):
        key = str(root)
        with self._lock:
            if key not in self._index:
                self._asked[key] = self._asked.get(key, 0) + 1
                if self._asked[key] < self.threshold:
                    return None
                try:
                    self._index[key] = RepoHistory.read(root, self.run)
                except (OSError, ValueError, subprocess.SubprocessError):
                    self._index[key] = None
            idx = self._index[key]
        return idx.rows(rels) if idx is not None else None


# ---------------------------------------------------------------- objects: does this repo hold it, and what does it say

class ObjectChecker:
    """`git cat-file -e <sha>` for many shas of the same repos, one long-lived `git cat-file --batch-check` per repo instead of
    one process per question. A repo git cannot open answers False, as `cat-file -e` does (exit code not 0)."""

    def __init__(self, env=None, keep: int = 160):
        # ownership asks every repo about each file in turn, so fewer open processes than repos means a new one for nearly every
        # question; a process holds two pipes, so the open-file limit sets how many can stay (raised where the system allows)
        try:
            import resource
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            want = 4096 if hard == resource.RLIM_INFINITY else min(hard, 4096)
            if soft != resource.RLIM_INFINITY and soft < want:
                resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
                soft = want
            if soft != resource.RLIM_INFINITY:
                keep = max(8, min(keep, (soft - 128) // 3))
        except (ImportError, ValueError, OSError):   # Windows has no such limit to raise
            pass
        self.env, self.keep, self._procs, self._lock = env, keep, {}, threading.Lock()

    def _proc(self, repo):
        p = self._procs.pop(repo, None)
        if p is not None and p.poll() is None:
            self._procs[repo] = p
            return p
        while len(self._procs) >= self.keep:
            old = self._procs.pop(next(iter(self._procs)))
            self._close(old)
        p = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch-check"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=self.env)
        self._procs[repo] = p
        return p

    @staticmethod
    def _close(p):
        try:
            p.stdin.close()
            p.stdout.close()
            p.kill()
            p.wait(timeout=5)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

    def has(self, repo, sha) -> bool:
        with self._lock:
            try:
                p = self._proc(repo)
                p.stdin.write(f"{sha}\n".encode())
                p.stdin.flush()
                line = p.stdout.readline().decode("utf-8", "replace").strip()
            except (OSError, ValueError, subprocess.SubprocessError):
                self._procs.pop(repo, None)
                return False
            return bool(line) and not line.endswith((" missing", " ambiguous"))

    def close(self):
        with self._lock:
            for p in self._procs.values():
                self._close(p)
            self._procs.clear()


def speed_up_ownership(mod):
    """ownership.py asks whether a repo holds a version of a file once per file and per repo (`_git_has_blob`). Where it has no better
    way (a repo not known to be mostly someone else's: for those it lists the repo's objects once, and answers from that list), answer
    from one open process per repo instead of one git process per question. The module's own function answers for the listed repos
    and when git cannot be started, so the answers are the module's."""
    import atexit
    checker = ObjectChecker()
    atexit.register(checker.close)
    slow = mod._git_has_blob

    def _git_has_blob(repo, sha, rc):
        key = (repo, sha)
        if key not in rc._has_blob:
            if rc._mostly_others.get(repo):
                return slow(repo, sha, rc)       # the module lists these repos' objects once (one process, memory for few repos)
            try:
                rc._has_blob[key] = checker.has(repo, sha)
            except Exception:   # noqa: BLE001 - git or the pipe failed: ask the old way
                return slow(repo, sha, rc)
        return rc._has_blob[key]
    mod._git_has_blob = _git_has_blob
    # `_held_since(file, tracked)` asks git when the file's path was first added: the same answer for the same file whichever
    # repo's copy of it is being compared, and `_committed_copy` asks it once per repo that holds the bytes (3,042 asks for
    # a few hundred files on a laptop with several clones)
    held = mod._held_since
    seen: dict = {}

    def _held_since(f, tracked):
        key = (f, tracked)
        if key not in seen:
            seen[key] = held(f, tracked)
        return seen[key]
    mod._held_since = _held_since

    return checker


def blobs_at(repo, specs, timeout: float = 60):
    """[bytes | None] for each `<revision>:<path>` in specs (what `git show <spec>` prints for a file), from one
    `git cat-file --batch`. None when the object is missing or is not a file."""
    if not specs:
        return []
    try:
        r = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input="".join(f"{s}\n" for s in specs).encode(),
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return [None] * len(specs)
    out, i, res = r.stdout, 0, []
    for _ in specs:
        j = out.find(b"\n", i)
        if j < 0:
            res.append(None)
            continue
        head = out[i:j].decode("utf-8", "replace").split()
        i = j + 1
        if len(head) == 3 and head[1] == "blob" and head[2].isdigit():
            n = int(head[2])
            res.append(out[i:i + n])
            i += n + 1
        else:
            res.append(None)
    return res
