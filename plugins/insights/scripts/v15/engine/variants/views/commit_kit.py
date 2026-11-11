"""The share of commits that are the person's, with one rule for every view that shows it. Stdlib only.

inventory.py decides per repo whether the person writes there (`authors_here`: their commits are at least `author_share`
of the repo's commits in the window, or they made its first commit) and gives one row per git repository however many
paths reach it. The share is taken over the repos they write in; a repo they only read, or send an occasional fix to, is
named apart so its commits (and its authors) do not decide who "the biggest other author" is.
"""


def commit_share(inv):
    real = [r for r in inv.get("repos") or [] if str(r.get("repo", "")).startswith("/")]
    # an inventory from before the rule has no flag: every repo counts, as it did
    mine_repos = [r for r in real if r.get("authors_here", True)]
    # a start folder that is not a repo, or a repo with no commit in the window, is not "a repo you have open"
    other = [r for r in real if not r.get("authors_here", True) and r.get("commits_in_window")]
    others = {}
    for r in mine_repos:
        for k, v in (r.get("other_authors") or {}).items():
            others[k] = others.get(k, 0) + v
    share = inv.get("author_share", 0.10)
    return {
        "mine": sum(r["theirs_in_window"] for r in mine_repos),
        "all": sum(r["commits_in_window"] for r in mine_repos),
        "repos": len(mine_repos),
        "open_repos": len(other),
        "open_commits": sum(r["commits_in_window"] for r in other),
        "open_mine": sum(r["theirs_in_window"] for r in other),
        "top_other": sorted(others.items(), key=lambda kv: -kv[1])[:1],
        "rule": f"A repo is counted when your commits are at least {round(share * 100)}% of its commits in the window, "
                "or you made its first commit. Repos you have open but write little in are listed apart.",
    }
