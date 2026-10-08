#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from lib.sources import (
    ReportConfig,
    artifacts,
    delegated,
    dialogue,
    ensure_indexed,
    observer_rows,
    resolve_args,
    search_candidates,
    session_header,
    session_title,
    structure,
)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_config(path: Path) -> ReportConfig:
    config = ReportConfig()
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "dialogue_limit":
                config.dialogue_limit = int(value)
            elif key == "query_limit":
                config.query_limit = int(value)
            elif key == "observer_limit":
                config.observer_limit = int(value)
            elif key == "include_structure":
                config.include_structure = _parse_bool(value)
            elif key == "include_observer":
                config.include_observer = _parse_bool(value)
            elif key == "dialogue_full":
                config.dialogue_full = _parse_bool(value)
            elif key == "include_delegated":
                config.include_delegated = _parse_bool(value)
            elif key == "include_artifacts":
                config.include_artifacts = _parse_bool(value)
            elif key == "artifact_lines":
                config.artifact_lines = int(value)

    config.dialogue_limit = int(os.environ.get("REF_DIALOGUE_LIMIT", config.dialogue_limit))
    config.query_limit = int(os.environ.get("REF_QUERY_LIMIT", config.query_limit))
    config.observer_limit = int(os.environ.get("REF_OBSERVER_LIMIT", config.observer_limit))
    if "REF_INCLUDE_STRUCTURE" in os.environ:
        config.include_structure = _parse_bool(os.environ["REF_INCLUDE_STRUCTURE"])
    if "REF_INCLUDE_OBSERVER" in os.environ:
        config.include_observer = _parse_bool(os.environ["REF_INCLUDE_OBSERVER"])
    if "REF_DIALOGUE_FULL" in os.environ:
        config.dialogue_full = _parse_bool(os.environ["REF_DIALOGUE_FULL"])
    if "REF_INCLUDE_DELEGATED" in os.environ:
        config.include_delegated = _parse_bool(os.environ["REF_INCLUDE_DELEGATED"])
    if "REF_INCLUDE_ARTIFACTS" in os.environ:
        config.include_artifacts = _parse_bool(os.environ["REF_INCLUDE_ARTIFACTS"])
    return config


def _build_context(raw: str, cwd: str, config: ReportConfig) -> dict:
    resolved = resolve_args(raw)
    ctx = {
        "mode": resolved.mode,
        "session_uuid": resolved.session_uuid,
        "query": resolved.query,
        "dialogue_limit": config.dialogue_limit,
        "query_limit": config.query_limit,
        "observer_limit": config.observer_limit,
        "include_structure": config.include_structure,
        "include_observer": config.include_observer,
        "dialogue_full": config.dialogue_full,
        "include_delegated": config.include_delegated,
        "include_artifacts": config.include_artifacts,
        "resolve_needed_message": "RESOLVE_NEEDED: pass a session UUID, slug, date, or topic phrase.",
        "search_instruction": (
            "INSTRUCTION: If one candidate clearly matches, use its session UUID. "
            "If multiple, list top 3 with date + user preview and ask the user."
        ),
        "title_text": "",
        "index_text": "",
        "header_text": "",
        "dialogue_text": "",
        "structure_text": "",
        "delegated_text": "",
        "artifacts_text": "",
        "observer_text": "",
        "search_text": "",
    }

    if resolved.mode == "uuid":
        # First, so a session the index never saw is indexed before it is read.
        ctx["index_text"] = ensure_indexed(resolved.session_uuid, cwd)
        ctx["title_text"] = session_title(resolved.session_uuid)
        ctx["header_text"] = session_header(resolved.session_uuid)
        ctx["dialogue_text"] = dialogue(
            resolved.session_uuid, config.dialogue_limit, cwd, full=config.dialogue_full
        )
        if config.include_structure:
            ctx["structure_text"] = structure(resolved.session_uuid, config.query_limit, cwd)
        if config.include_delegated:
            ctx["delegated_text"] = delegated(resolved.session_uuid)
        if config.include_artifacts:
            ctx["artifacts_text"] = artifacts(resolved.session_uuid, max_lines=config.artifact_lines)
        if config.include_observer:
            ctx["observer_text"] = observer_rows(resolved.session_uuid, config.observer_limit)
    elif resolved.mode == "search":
        ctx["search_text"] = search_candidates(resolved.query, 5, cwd)

    return ctx


def main() -> int:
    skill_dir = SKILL_DIR
    template_dir = skill_dir / "templates"
    template_path = template_dir / "report.md.j2"
    config_path = template_dir / "report.yaml"

    raw = " ".join(sys.argv[1:]).strip()
    cwd = os.getcwd()
    config = _load_config(config_path)
    context = _build_context(raw, cwd, config)

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    try:
        template = env.get_template(template_path.name)
        out = template.render(**context)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: failed to render template: {exc}")
        print("DEBUG_CONTEXT=" + json.dumps({k: v for k, v in context.items() if not k.endswith('_text')}))
        return 0

    print(out.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
