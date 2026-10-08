"""The templates /new-agent and /new-skill copy (hooks/new.tsx), read as fill() reads them.

fill() sets the first `name: ` and `description: ` lines, each replaced whole, and turns `](./file)` links into
the skill's name. The mod's own tests use cut-down templates (they have no disk), so a change to a real one is
checked here.
"""
import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / "skills"


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
