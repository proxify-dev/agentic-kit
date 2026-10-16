"""The observation types are one table (hooks/memory_schema.py): no records, no per-vault switch.

Until 2026-09-25 each type was a record under loadout/memory/episodic/ that a vault could
switch off, and `correction` shipped off; loadout was archived and the table took its words,
correction in (it was armed in the live vault).
"""
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "hooks"))
from _brand import env_name  # noqa: E402
import memory_schema as schema  # noqa: E402


def test_the_table_is_the_vocabulary_in_legend_order():
    assert schema.types() == ["bugfix", "feature", "refactor", "change", "discovery", "decision",
                              "security_alert", "security_note", "correction"]
    assert schema.verdicts() == ["worked", "dead-end", "superseded", "neutral", "violated"]
    assert schema.glyphs()["bugfix"] == "●" and schema.glyphs(emoji=True)["bugfix"] == "🔴"
    assert schema.legend().startswith("Legend: 🎯session ●bugfix ◆feature ↻refactor")
    assert "✋correction" in schema.legend()


def test_the_prompt_block_has_a_line_per_type():
    block = schema.prompt_types()
    assert block.startswith("TYPES (the obs_type vocabulary")
    for name in schema.types():
        assert f"- {name} — " in block


def test_no_type_reads_a_vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "loadout" / "memory" / "episodic").mkdir(parents=True)
    (v / "loadout" / "memory" / "episodic" / "bugfix.md").write_text("---\nstate: off\n---\n")
    monkeypatch.setenv(env_name("PATH"), str(v))
    assert "bugfix" in schema.types()  # a leftover folder switches nothing off
