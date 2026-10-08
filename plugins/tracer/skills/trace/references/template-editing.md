# Replay Template Editing

`/tracer:trace` prefetch output is rendered from a Jinja template and fed into
`SKILL.md` via bash injection. Edit these files depending on what you need:

- Layout and section order: `templates/report.md.j2`
- Limits and toggles: `templates/report.yaml`
- jinja-ls variable contract: `schema/report.globals.json`
- Regenerate globals contract: `python lib/gen_globals.py`

## jinja-ls setup

Install the jinja-ls extension in Cursor/VS Code. The template includes:

```jinja
{#- jinja-ls: globals ../schema/report.globals.json -#}
```

This enables autocomplete and undefined-variable diagnostics based on the
globals schema.

## Runtime paths

- Entry point: `bin/fetch.py`
- Data providers: `lib/sources.py`
- Render template: `templates/report.md.j2`
