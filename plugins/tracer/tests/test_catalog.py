"""Plugin attribution from the machine's own plugin catalog.

The parser credited plugins from a hard-coded list — {"search", "meta", "teocns", …} —
that knew none of the old brain-* plugins  (brand: historical) and nothing a stranger installs. A plugin's MCP
tool is `mcp__plugin_<P>_<K>__<tool>`, and the `_` between P and K cannot be split back
from the string, so attribution is a lookup in what the machine has: installed plugins,
every cached version, marketplaces, and the marketplace catalog cache.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import asst, call, result, user, write_jsonl

from tracer.catalog import Catalog, normalise, session_listings
from tracer.db import get_write_connection
from tracer.parser import parse_jsonl, segment_interactions
from tracer.query import QueryFilters, query_interactions
from tracer.storage import IndexWriter


def _plugin(d: Path, name: str, servers: dict | None = None, inline: dict | None = None,
            skills=(), agents=()) -> Path:
    (d / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest = {"name": name}
    if inline:
        manifest["mcpServers"] = inline
    (d / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
    if servers:
        (d / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    for s in skills:
        (d / "skills" / s).mkdir(parents=True, exist_ok=True)
        (d / "skills" / s / "SKILL.md").write_text(f"---\nname: {s}\ndescription: x\n---\n")
    for a in agents:
        f = d / "agents" / f"{a}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("---\ndescription: x\n---\n")
    return d


@pytest.fixture
def plugins_root(tmp_path):
    root = tmp_path / "plugins"
    srv = {"command": "x"}
    (tmp_path / "skills" / "mine").mkdir(parents=True)  # the user's own skill, beside plugins/
    (tmp_path / "skills" / "mine" / "SKILL.md").write_text("---\nname: mine\n---\n")
    alpha = _plugin(tmp_path / "src" / "alpha", "alpha", servers={"alpha-srv": srv},
                    skills=["recall"], agents=["review/security"])
    (root).mkdir()
    (root / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"alpha@mkt": [{"installPath": str(alpha)}]}}))
    _plugin(root / "cache" / "mkt" / "beta" / "1.0.0", "beta", inline={"db": srv})
    _plugin(root / "cache" / "mkt" / "beta" / "0.9.0", "beta", inline={"old_db": srv})  # an old version
    _plugin(root / "cache" / "mkt" / "a_b" / "1", "a_b", servers={"c": srv})  # two names that
    _plugin(root / "cache" / "mkt" / "a" / "1", "a", servers={"b_c": srv})    # normalise alike
    market = tmp_path / "market"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"plugins": [{"name": "delta", "source": "./plugins/delta"}]}))
    _plugin(market / "plugins" / "delta", "delta", servers={"d": srv})
    (root / "known_marketplaces.json").write_text(json.dumps(
        {"local": {"source": {"source": "directory", "path": str(market)}}}))
    (root / "plugin-catalog-cache.json").write_text(json.dumps({"catalog": {"plugins": {
        "gamma@official": {"plugin": "gamma", "components": {
            "mcpServers": ["Gamma Server"], "skills": [{"name": "sky"}], "agents": [], "commands": []}}}}}))
    return root


def test_mcp_names_are_looked_up_not_split(plugins_root):
    cat = Catalog.load(plugins_root)
    a = cat.mcp("mcp__plugin_alpha_alpha-srv__search")  # installed
    assert (a.plugin, a.server, a.how) == ("alpha", "alpha-srv", "exact")
    a = cat.mcp("mcp__plugin_beta_old_db__q")  # only an old cached version had this server
    assert (a.plugin, a.server, a.how) == ("beta", "old_db", "exact")
    g = cat.mcp("mcp__plugin_gamma_Gamma_Server__x")  # a marketplace listing, not installed
    assert (g.plugin, g.server, g.how) == ("gamma", "Gamma Server", "exact")
    d = cat.mcp("mcp__plugin_delta_d__x")  # a directory marketplace
    assert (d.plugin, d.how) == ("delta", "exact")
    amb = cat.mcp("mcp__plugin_a_b_c__x")
    assert amb.how == "ambiguous" and amb.plugin is None and amb.candidates == ("a:b_c", "a_b:c")
    inf = cat.mcp("mcp__plugin_nowhere-plugin_srv__x")
    assert (inf.plugin, inf.server, inf.how) == ("nowhere-plugin", "srv", "inferred")
    known = cat.mcp("mcp__plugin_alpha_new_srv__x")  # a known plugin, a server it has since dropped
    assert (known.plugin, known.server, known.how) == ("alpha", "new_srv", "inferred")
    assert cat.mcp("mcp__GitHub__get_me").how == "configured"
    assert cat.mcp("mcp__claude_ai_Claude_Docs__x").how == "configured"
    assert cat.mcp("Bash").how == "none"
    assert normalise("plugin:my.plugin:db@x") == "plugin_my_plugin_db_x"


def test_skills_and_agents_resolve_against_the_session(plugins_root):
    cat = Catalog.load(plugins_root)
    assert (cat.skill("alpha:recall").plugin, cat.skill("alpha:recall").how) == ("alpha", "exact")
    assert cat.skill("alpha:alpha:recall").plugin == "alpha"  # the doubled prefix of 2.1.216–245
    listed = cat.skill("recall", frozenset({"alpha:recall", "user-skill"}))
    assert (listed.plugin, listed.how) == ("alpha", "exact")
    assert cat.skill("user-skill", frozenset({"user-skill"})).how == "none"
    assert (cat.skill("recall").plugin, cat.skill("recall").how) == ("alpha", "inferred")  # catalog only
    assert (cat.skill("sky").plugin, cat.skill("sky").how) == ("gamma", "inferred")
    assert cat.skill("nowhere:thing").how == "inferred"
    assert (cat.agent("alpha:review:security").plugin, cat.agent("alpha:review:security").how) == ("alpha", "exact")
    assert cat.agent("Explore", frozenset({"Explore"})).how == "none"
    # not plugins: a nested project skill, a claude.ai synced one, the user's own
    assert cat.skill("apps/web:deploy").how == "none"
    assert cat.skill("anthropic-skills:pdf").how == "none"
    assert (cat.skill("mine").plugin, cat.skill("mine").how) == (None, "none")
    assert (cat.skill("sky").plugin, cat.skill("sky").how) == ("gamma", "inferred")  # nobody else has it


def test_session_listings():
    msgs = [
        {"type": "attachment", "attachment": {"type": "skill_listing",
                                              "content": "- user-skill: does x: y\n- alpha:recall: Recall things"}},
        {"type": "attachment", "attachment": {"type": "agent_listing_delta", "addedTypes": ["Explore", "alpha:review"]}},
    ]
    assert session_listings(msgs) == (frozenset({"user-skill", "alpha:recall"}), frozenset({"Explore", "alpha:review"}))


def test_the_parser_credits_plugins_with_how_sure(plugins_root, tmp_path):
    cat = Catalog.load(plugins_root)
    path = write_jsonl(tmp_path / "s.jsonl", [
        {"type": "attachment", "attachment": {"type": "skill_listing", "content": "- alpha:recall: x"}},
        user("go", 0),
        asst([call("t1", "mcp__plugin_alpha_alpha-srv__search", q="x")], 1, "m1"),
        result("t1", "ok", 2),
        asst([call("t2", "Skill", skill="recall")], 3, "m2"),  # bare: resolved by the listing
        result("t2", "ok", 4),
        asst([call("t3", "mcp__plugin_nowhere_srv__q")], 5, "m3"),
        result("t3", "ok", 6),
        asst([call("t4", "mcp__plugin_a_b_c__q")], 7, "m4"),  # ambiguous: credits no one
        result("t4", "ok", 8),
        asst([call("t5", "Edit", file_path="/repo/plugins/beta/src/x.py", old_string="a", new_string="b")], 9, "m5"),
        result("t5", "ok", 10),
        asst([call("t6", "Edit", file_path="/repo/plugins/notaplugin/x.py", old_string="a", new_string="b")], 11, "m6"),
        result("t6", "ok", 12),
    ])
    rec = segment_interactions(parse_jsonl(path), "s", str(path), catalog=cat)[0]
    assert [(c.plugin, c.via, c.how) for c in rec.plugin_credits] == [
        ("alpha", "mcp", "exact"), ("alpha", "skill", "exact"), ("beta", "file", "exact"),
        ("nowhere", "mcp", "inferred")]
    assert rec.plugins == ["alpha", "beta", "nowhere"]
    assert [(s.name, s.plugin) for s in rec.skills_invoked] == [("recall", "alpha")]

    con = get_write_connection(tmp_path / "t.db")
    IndexWriter(con, "-p").replace_session_records("s", [rec])
    assert con.execute("SELECT plugin, via, how FROM i_plugins ORDER BY plugin, via").fetchall() == [
        ("alpha", "mcp", "exact"), ("alpha", "skill", "exact"), ("beta", "file", "exact"), ("nowhere", "mcp", "inferred")]
    assert [r.id for r in query_interactions(QueryFilters(plugin="alpha"), con=con)] == ["s-000"]
    assert [r.id for r in query_interactions(QueryFilters(skill_name="recall"), con=con)] == ["s-000"]
    assert query_interactions(QueryFilters(skill_name="other"), con=con) == []
    con.close()
