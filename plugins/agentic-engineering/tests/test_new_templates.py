"""The templates /new-agent, /new-skill and /new-plugin copy (hooks/new.tsx), read as the mod reads them.

fill() sets the first `name: ` and `description: ` lines, each replaced whole, and turns `](./file)` links into
the skill's name. scaffold() replaces `{{name}}` in the plugin's README and nothing else. The mod's own tests use
cut-down templates (they have no disk), so a change to a real one is checked here.
"""
import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


@pytest.mark.parametrize("template", ["plugin-README.md", "plugin-AGENTS.md"])
def test_plugin_templates_have_only_the_name_to_fill(template):
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    assert set(re.findall(r"\{\{(\w+)\}\}", text)) == {"name"}, f"scaffold() fills {{{{name}}}} alone; another {{{{…}}}} would stay in the user's {template}"


def test_plugin_agents_md_names_the_skills_for_advanced_customization():
    text = (TEMPLATES / "plugin-AGENTS.md").read_text(encoding="utf-8")
    assert "/plugin-authoring" in text and "/agentic-engineering:harness-engineering" in text
    assert (SKILLS / "harness-engineering" / "SKILL.md").is_file(), "plugin-AGENTS.md names a skill this plugin no longer ships"


@pytest.mark.parametrize("skill", ["agent-development", "skill-development"])
def test_fill_finds_one_line_name_and_description_in_the_frontmatter(skill):
    text = (SKILLS / skill / "_template.md").read_text(encoding="utf-8")
    frontmatter = text.split("---\n")[1]
    for key in ("name", "description"):
        lines = re.findall(rf"^{key}: (.*)$", frontmatter, re.M)
        assert len(lines) == 1 and lines[0].strip(), f"{skill}/_template.md: fill() replaces one `{key}: ` line"
        assert not lines[0].lstrip().startswith(("|", ">")), f"{skill}/_template.md: `{key}:` must be one line, not a block"
    for target in re.findall(r"\]\(\./([^)]+)\)", text):
        assert (SKILLS / skill / target).is_file(), f"{skill}/_template.md links ./{target}, which does not exist"
