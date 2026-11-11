"""The verification discipline page of the Substrate section: full substrate text + this laptop's run (see substrate_kit.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import substrate_kit as S  # noqa: E402
import substrate_pages as P  # noqa: E402

DISC = "verification"
ORDER = 70 + ["context", "prompt", "verification", "harness", "orchestration"].index(DISC)
TITLE = P.title(DISC)
QUESTION = P.question(DISC)
PROXIFY_ONLY = True
CSS = S.css_disc("sub_verification")


def render(ctx):
    return P.render(ctx, DISC)
