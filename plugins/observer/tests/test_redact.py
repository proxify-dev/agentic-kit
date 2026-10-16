"""redact() runs on every row, prompt, summary and note before it is stored.

The Bearer case is the leak it was rewritten for: the old KV_SECRET took the word `Bearer` as the value of
`Authorization:` and left the token in plain text. The vendor shapes follow claude-mem 13.40's redaction.ts.
"""
import importlib.util
from pathlib import Path

import pytest

SUMMARIZER = Path(__file__).resolve().parent.parent / "scripts" / "summarize-session.py"
_spec = importlib.util.spec_from_file_location("summarize_session_redact", SUMMARIZER)
summarizer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summarizer)


@pytest.mark.parametrize("text, secret", [
    ('curl -H "Authorization: Bearer abcDEF123456789xyz" x', "abcDEF123456789xyz"),
    ('{"Authorization": "Bearer eyab12345678zz"}', "eyab12345678zz"),
    ("then Bearer 0a1b2c3d4e5f6a7b8c9d was sent", "0a1b2c3d4e5f6a7b8c9d"),
    ("export GITHUB_TOKEN=notvendorAbc12345", "notvendorAbc12345"),
    ('DB_PASSWORD="hunter2secret"', "hunter2secret"),
    ('{\\"api_key\\": \\"abcd1234efgh\\"}', "abcd1234efgh"),
    ("AWS_SECRET_ACCESS_KEY=" + "A" * 39 + "b", "A" * 39 + "b"),
    ("key sk_live_" + "a1" * 12, "sk_live_"),
    ("key AIza" + "B" * 35, "AIza"),
    ("key sk-ant-api03-abc_def-ghijklmnopqrstu", "sk-ant-"),
    ("key github_pat_" + "a" * 82, "github_pat_"),
])
def test_a_secret_is_redacted(text, secret):
    out = summarizer.redact(text)
    assert secret not in out and "[REDACTED]" in out


@pytest.mark.parametrize("text", [
    "max_tokens=4096",                       # a number is not a secret
    "use Bearer authentication here",        # prose: no digit after Bearer
    "the token was the cause",
    "PWD=/Users/x",                          # a working directory
    "cwd: /tmp",
])
def test_ordinary_text_is_left_alone(text):
    assert summarizer.redact(text) == text
