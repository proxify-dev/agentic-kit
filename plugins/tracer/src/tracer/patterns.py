"""Pattern detection: tool sequences, failure clusters, invocation frequencies."""

from __future__ import annotations

from collections import Counter

from .models import InteractionRecord


def extract_tool_sequences(records: list[InteractionRecord], min_count: int = 2) -> list[tuple[tuple[str, ...], int]]:
    """Find common ordered tool/agent call sequences across interactions.

    Returns list of (sequence, count) sorted by frequency descending.
    """
    sequences: Counter[tuple[str, ...]] = Counter()

    for r in records:
        seq: list[str] = []
        # Build ordered sequence: agents first (they're spawned in order), then tools
        for a in r.agents_spawned:
            seq.append(f"Agent({a.type})")
        for t in r.tools_called:
            seq.append(t.name)

        if len(seq) >= 2:
            # Record both full sequence and all 2-grams
            key = tuple(seq)
            sequences[key] += 1
            for i in range(len(seq) - 1):
                bigram = (seq[i], seq[i + 1])
                sequences[bigram] += 1

    return [(seq, count) for seq, count in sequences.most_common(30) if count >= min_count]


def group_failure_patterns(records: list[InteractionRecord]) -> list[dict]:
    """Cluster error interactions by error type and tool combination.

    Returns list of pattern groups with example interaction IDs.
    """
    error_records = [r for r in records if r.error_signals]
    if not error_records:
        return []

    # Group by (error_type, tools_involved)
    groups: dict[tuple[str, str], list[str]] = {}
    for r in error_records:
        for err in r.error_signals:
            tools_key = ",".join(sorted(t.name for t in r.tools_called))
            key = (err.type, tools_key)
            groups.setdefault(key, []).append(r.id)

    result = []
    for (err_type, tools), ids in sorted(groups.items(), key=lambda x: -len(x[1])):
        result.append({
            "error_type": err_type,
            "tools_involved": tools,
            "count": len(ids),
            "example_ids": ids[:5],
        })

    return result


def invocation_summary(records: list[InteractionRecord]) -> dict:
    """Frequency analysis of agent-type combinations per plugin.

    Returns dict mapping plugin -> list of (agent_combo, count).
    """
    plugin_combos: dict[str, Counter[tuple[str, ...]]] = {}

    for r in records:
        agent_types = tuple(sorted(a.type for a in r.agents_spawned))
        for plugin in r.plugins:
            if plugin not in plugin_combos:
                plugin_combos[plugin] = Counter()
            plugin_combos[plugin][agent_types] += 1

    result = {}
    for plugin, combos in sorted(plugin_combos.items()):
        result[plugin] = [
            {"agents": list(combo), "count": count}
            for combo, count in combos.most_common(10)
        ]

    return result


def format_patterns(
    sequences: list[tuple[tuple[str, ...], int]],
    failures: list[dict],
    invocations: dict,
) -> str:
    """Format pattern analysis results."""
    lines = []

    if sequences:
        lines.append("## Common Tool Sequences")
        lines.append("")
        for seq, count in sequences[:15]:
            lines.append(f"  {' → '.join(seq)}  ({count}x)")
        lines.append("")

    if failures:
        lines.append("## Failure Patterns")
        lines.append("")
        for group in failures[:10]:
            lines.append(f"  {group['error_type']} with [{group['tools_involved']}]: {group['count']}x")
            lines.append(f"    examples: {', '.join(group['example_ids'][:3])}")
        lines.append("")

    if invocations:
        lines.append("## Invocation Patterns by Plugin")
        lines.append("")
        for plugin, combos in invocations.items():
            lines.append(f"  {plugin}:")
            for combo in combos[:5]:
                agents = ", ".join(combo["agents"]) if combo["agents"] else "(no agents)"
                lines.append(f"    [{agents}]: {combo['count']}x")
        lines.append("")

    return "\n".join(lines) if lines else "No patterns detected."
