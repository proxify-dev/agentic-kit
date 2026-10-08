"""CLI entry point for conversation-indexer."""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

from ._brand import cmd
from .db import DB_PATH, get_readonly_connection, get_write_connection
from .storage import (
    Manifest,
    discover_projects,
    get_conversations_dir_for_hash,
    get_project_hash,
    incremental_index,
    owning_project_dirs,
    rebuild_from_jsonl,
)
from .drill import DrillError, open_steps, render
from .filetrace import format_file_trace, trace
from .query import (
    QueryFilters,
    aggregate_stats,
    count_interactions,
    format_user_prompts,
    format_dialogue,
    format_results,
    format_stats,
    list_projects,
    query_dialogue,
    query_interactions,
    staleness_warning,
)
from .search import DEFAULT_LIMIT, INCLUDE_AUTOMATED, format_search, parse_entrypoints, search_sessions
from .patterns import (
    extract_tool_sequences,
    group_failure_patterns,
    invocation_summary,
    format_patterns,
)


def cmd_index(args: argparse.Namespace):
    """Run the structural extraction pass."""
    if getattr(args, "session", None):
        from .storage import describe_index_status, ensure_session_indexed

        result = ensure_session_indexed(args.session)
        print(describe_index_status(result, args.session))
        return
    project_path = args.project or str(Path.cwd())

    t0 = time.time()
    if args.rebuild:
        count = rebuild_from_jsonl()
    else:
        count = incremental_index(
            project_path,
            quiet=False,
            index_all=args.all,
        )
    elapsed = time.time() - t0

    print(f"Done in {elapsed:.1f}s — indexed {count} new interactions")
    print(f"Database: {DB_PATH}")
    if DB_PATH.exists():
        print(f"DB size: {DB_PATH.stat().st_size / 1024:.1f} KB")


# How many of the newest matching interactions --patterns reads.
PATTERNS_DEPTH = 500


def cmd_query(args: argparse.Namespace):
    """Query the interaction index (read-only; `tracer trace index` refreshes it)."""
    project_path = args.project or str(Path.cwd())
    project_hash = get_project_hash(project_path)

    con = get_readonly_connection()

    if args.drill:
        # read from the transcript, so an index behind it is no warning worth printing
        drill = args.drill
        # `--drill 004` with `--session <uuid>`: the turn number alone is enough
        if drill.isdigit() and args.session:
            drill = f"{args.session}-{int(drill):03d}"
        try:
            print(open_steps(drill, args.step, con=con) if args.step
                  else render(drill, con=con, steps=args.steps))
        except DrillError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        return

    projects: tuple[str, ...] = ()
    if args.root is not None and not args.all_projects:
        # the folders `tracer sessions ls` counts as this project: itself, its worktrees, the folders inside
        from .sessions import resolve_root, scope

        root = resolve_root(args.root)
        project_hash = get_project_hash(root)
        indexed = {r[0] for r in con.execute("SELECT DISTINCT project_hash FROM interactions")}
        projects = tuple(f.hash for f in scope(root, indexed)) or (project_hash,)

    staleness_warning(project_hash, con)

    filters = QueryFilters(
        project=project_hash if not args.all_projects and not projects else "",
        projects=projects,
        plugin=args.plugin or "",
        agent_type=args.agent_type or "",
        skill_name=args.skill or "",
        tool_name=args.tool or "",
        has_errors=args.errors or False,
        since=args.since or "",
        until=args.until or "",
        git_branch=args.branch or "",
        session_id=args.session or "",
        limit=args.limit,
    )

    if args.stats:
        stats = aggregate_stats(filters, con=con)
        print(format_stats(stats))
        found = stats["total_interactions"]
    elif args.patterns:
        records = query_interactions(dataclasses.replace(filters, limit=PATTERNS_DEPTH), con=con)
        sequences = extract_tool_sequences(records)
        failures = group_failure_patterns(records)
        invocations = invocation_summary(records)
        print(format_patterns(sequences, failures, invocations))
        found = len(records)
    else:
        records = query_interactions(filters, con=con)
        total = count_interactions(QueryFilters(project=filters.project, projects=filters.projects), con=con)
        matched = count_interactions(dataclasses.replace(filters, limit=None), con=con)
        print(format_results(records, filters, total, matched=matched))
        found = len(records)
    if not found and not args.session:  # across sessions, a miss (`tracer sessions turns`); one session's view stays 0
        sys.exit(1)


def cmd_search(args: argparse.Namespace):
    """Which sessions talked about a phrase: raw transcript scan, ranked (search.py)."""
    project_path = args.project or str(Path.cwd())
    project_hash = get_project_hash(project_path)

    if args.under:
        dirs = owning_project_dirs(args.under)
        if not dirs:
            print(f'No indexed project owns "{args.under}".')
            return
        scope = f"the {len(dirs)} project(s) owning {args.under}"
    elif args.all_projects:
        dirs = [d for _hash, d in discover_projects()]
        scope = f"all {len(dirs)} projects"
    else:
        dirs = [get_conversations_dir_for_hash(project_hash)]
        scope = project_hash

    include_automated, entrypoints = parse_entrypoints(args.entrypoint)
    report = search_sessions(
        args.query,
        dirs,
        scope=scope,
        include_meta=args.include_meta,
        since=args.since or "",
        until=args.until or "",
        tool=args.tool or "",
        role=args.role or "",
        outcome=args.outcome or "",
        errors=args.errors,
        exclude_session=args.exclude_session,
        include_automated=include_automated or args.include_automated,
        entrypoints=entrypoints,
        turn_limit=args.limit,
    )
    print(format_search(report, limit=args.limit))


def cmd_trace(args: argparse.Namespace):
    """Trace file provenance via i_files table + raw JSONL drill-down (read-only)."""
    project_path = args.project or str(Path.cwd())
    project_hash = get_project_hash(project_path)

    operations = None
    if args.op:
        operations = [op.strip().lower() for op in args.op.split(",")]

    con = get_readonly_connection()

    staleness_warning(project_hash, con)
    if args.all_projects:
        dirs = [d for _hash, d in discover_projects()]
    else:
        dirs = [get_conversations_dir_for_hash(project_hash)]
    ft = trace(args.path, con, dirs, project="" if args.all_projects else project_hash,
               operations=operations, limit=args.limit)
    print(format_file_trace(ft, limit=args.limit))


def cmd_projects(args: argparse.Namespace):
    """List all indexed projects with summary stats."""
    # Index all projects first if requested
    if args.refresh:
        incremental_index("", quiet=False, index_all=True)

    con = get_readonly_connection()
    projects = list_projects(con=con)

    if not projects:
        print(f"No indexed projects. Run `{cmd('trace index')} --all` first.")
        return

    print(f"{len(projects)} indexed projects:\n")
    for p in projects:
        path = p["project_path"] or p["project_hash"]
        interactions = p["interactions"]
        sessions = p["sessions"]
        last = p["last_ts"][:10] if p["last_ts"] else "?"
        tokens = p["total_tokens"]
        print(f"  {path}")
        print(f"    {interactions} interactions, {sessions} sessions, last: {last}, ~{tokens:,} tokens")
        print()


def cmd_dialogue(args: argparse.Namespace):
    """Show prompt+answer transcript for a session."""
    project_path = args.project or str(Path.cwd())
    project_hash = get_project_hash(project_path)

    con = get_readonly_connection()
    staleness_warning(project_hash, con)

    filters = QueryFilters(
        project=project_hash if not args.all_projects else "",
        since=args.since or "",
        until=args.until or "",
        git_branch=args.branch or "",
        session_id=args.session or "",
        # --role user reads the whole session by default: a backstory is at the start
        limit=args.limit if args.limit is not None else (None if args.role == "user" else 30),
    )
    records = query_dialogue(filters, con=con)
    if args.role == "user":
        print(format_user_prompts(records, filters, con=con))
        return
    full = "all" if getattr(args, "full", False) else "last"
    print(format_dialogue(records, filters, con=con, full=full))


def cmd_config(args: argparse.Namespace):
    """Get or set indexer configuration."""
    project_path = args.project or str(Path.cwd())
    project_hash = get_project_hash(project_path)

    con = get_write_connection()
    manifest = Manifest(con, project_hash)

    if args.mode:
        if args.mode not in ("active", "pull"):
            print(f"Invalid mode: {args.mode}. Must be 'active' or 'pull'.", file=sys.stderr)
            sys.exit(1)
        manifest.set_mode(args.mode)
        con.close()
        print(f"Indexing mode set to: {args.mode}")
    else:
        mode = manifest.get_mode()
        con.close()
        print(f"mode: {mode}")
        print(f"database: {DB_PATH}")


def main():
    from ._brand import utf8_stdio
    utf8_stdio()  # a cp1252 console or pipe cannot encode what a transcript quotes
    parser = argparse.ArgumentParser(
        prog="conversation_indexer",
        description="Index and query Claude Code conversation histories",
    )
    parser.add_argument("--project", help="Project path (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    # Index command
    idx = sub.add_parser("index", help="Run the structural extraction pass")
    idx.add_argument("--rebuild", action="store_true", help="Force full re-index")
    idx.add_argument("--all", action="store_true", help="Index all discovered projects")
    idx.add_argument("--session", help="Index one session's transcript (UUID) if new or changed")

    # Query command
    q = sub.add_parser("query", help="Query the interaction index")
    q.add_argument("--plugin", help="Filter by plugin name")
    q.add_argument("--agent-type", help="Filter by agent type")
    q.add_argument("--skill", help="Filter by skill name")
    q.add_argument("--tool", help="Filter by tool name")
    q.add_argument("--errors", action="store_true", help="Only interactions with errors")
    q.add_argument("--since", help="Filter from date (ISO format)")
    q.add_argument("--until", help="Filter to date, inclusive (a bare date keeps that day)")
    q.add_argument("--branch", help="Filter by git branch (its start, e.g. feat/)")
    q.add_argument("--session", help="Filter by session ID")
    q.add_argument("--limit", type=int, default=20, help="Max results (default 20)")
    q.add_argument("--stats", action="store_true", help="Show aggregation statistics")
    q.add_argument("--patterns", action="store_true", help="Show invocation/failure patterns")
    q.add_argument("--drill", help="A session at a glance (uuid or prefix), or one turn's steps "
                                   "(interaction ID, or a turn number with --session)")
    q.add_argument("--step", help="With --drill: open steps whole — 5, 1-6, 1,4,5 or all")
    q.add_argument("--steps", help="With --drill on a turn: list just these steps — 11-40, 3,7")
    q.add_argument("--all-projects", action="store_true", help="Query across all projects")
    q.add_argument("--root", help="A folder's sessions as `tracer sessions ls` scopes them: itself, its worktrees "
                                  "and the folders inside (. = this project)")

    # Search command
    srch = sub.add_parser("search", help="Search conversation content (scans raw JSONL)")
    srch.add_argument("query", help="Plain-text query to search for")
    srch.add_argument("--tool", help="Only hits in this tool's calls and results")
    srch.add_argument("--role", choices=["user", "assistant", "thinking", "tool-output", "notification", "teammate"],
                      help="Only hits in one role's words, as the hit counts print them")
    srch.add_argument("--outcome", choices=["error", "ok"], help="Only hits in tool results that ended this way")
    srch.add_argument("--errors", action="store_true", help="Only hits in failed tool results (= --outcome error)")
    srch.add_argument("--since", help="Only hits from this date or time on (ISO format)")
    srch.add_argument("--until", help="Only hits up to this date or time, inclusive (ISO; a bare date keeps that day)")
    srch.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                      help=f"Sessions shown (default {DEFAULT_LIMIT}); every session is scanned")
    srch.add_argument("--include-meta", action="store_true",
                      help="Count hits in hook output, attachments and other metadata records")
    srch.add_argument("--exclude-session", default=None, metavar="UUID",
                      help="Leave out this session (default: $CLAUDE_CODE_SESSION_ID, the caller; '' keeps all)")
    srch.add_argument("--entrypoint", metavar="all|NAME[,NAME]",
                      help="Sessions by transcript entrypoint: all, or only these (default: the ones a person starts)")
    srch.add_argument("--include-automated", action="store_true", default=INCLUDE_AUTOMATED,
                      help="Also list sessions a program started (claude -p, the SDKs) (= --entrypoint all)")
    srch.add_argument("--all-projects", action="store_true", help="Search across all projects")
    srch.add_argument(
        "--under",
        help="Scope to projects whose cwd is an ancestor of this path (fast owning-project search)",
    )

    # Trace command
    trc = sub.add_parser("trace", help="Trace file provenance by path")
    trc.add_argument("path", help="File path or basename to trace")
    trc.add_argument("--op", help="Filter by operation: read, write, edit (comma-separated)")
    trc.add_argument("--limit", type=int, default=20, help="Max results (default 20)")
    trc.add_argument("--all-projects", action="store_true", help="Trace across all projects")

    # Projects command
    proj = sub.add_parser("projects", help="List all indexed projects")
    proj.add_argument("--refresh", action="store_true", help="Re-index all projects before listing")

    # Dialogue command
    dlg = sub.add_parser("dialogue", help="Prompt+answer transcript for a session")
    dlg.add_argument("--session", help="Session UUID (required for useful output)")
    dlg.add_argument("--since", help="Filter from date (ISO format)")
    dlg.add_argument("--until", help="Filter to date")
    dlg.add_argument("--branch", help="Filter by git branch")
    dlg.add_argument("--limit", type=int, default=None, help="Max interactions (default 30; with --role user, all)")
    dlg.add_argument("--all-projects", action="store_true", help="Query across all projects")
    dlg.add_argument("--full", action="store_true",
                     help="every answer whole, not only the last (previews are 500 chars)")
    dlg.add_argument("--role", choices=["user"],
                     help="user: only what the user typed — every turn, each prompt whole, notices and teammates left out")

    # Config command
    cfg = sub.add_parser("config", help="Get or set indexer configuration")
    cfg.add_argument("--mode", help="Set indexing mode: 'active' or 'pull'")

    args = parser.parse_args()
    if args.command == "index":
        cmd_index(args)
    elif args.command == "query":
        cmd_query(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "trace":
        cmd_trace(args)
    elif args.command == "dialogue":
        cmd_dialogue(args)
    elif args.command == "projects":
        cmd_projects(args)
    elif args.command == "config":
        cmd_config(args)


if __name__ == "__main__":
    main()
