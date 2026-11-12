# Contributing

Pull requests are welcome, from anyone. Issues are too.

## Before you open one

- **One change per pull request.** Two fixes are two pull requests.
- **Say what the tool did before and after.** Write it for a person who uses the tool.
  One short example is better than a long story.
- **Leave versions and changelogs alone.** Do not edit `version` in a `plugin.json`, a `pyproject.toml` or a `uv.lock`,
  any `CHANGELOG.md`, `.claude-plugin/marketplace.json` or `plugins/ak/src/ak/setup/kit.json`.
  A maintainer sets a version when it is released, so two pull requests never claim the same number.
- **Add a test where the folder already has tests.** Put it beside the code it tests.
- **Nothing personal, anywhere.** Not in the code, the tests, the comments, the pull request text or the commit messages:
  no names, no paths from your machine, no output of your own scan, no secrets.
  To keep your email out of the log, set your commit email to your GitHub no-reply address.

## Run the checks that fit your change

Run these from the repo root unless a line says otherwise.

| You changed | Run |
|---|---|
| anything | `claude plugin validate .` (it passes with a few warnings) |
| `plugins/tracer`, `plugins/observer`, `plugins/web` | `cd plugins/<name> && uv run pytest -q` |
| `plugins/agentic-engineering` | `cd plugins/agentic-engineering && uv run pytest -q --ignore=tests/test_blocks_deployed.py` |
| `plugins/ak` | `cd plugins/ak && uv run pytest -q`; failures in `tests/test_setup_*.py` are known (they expect plugins that are not public), any other is yours |
| `plugins/modes` | `cd plugins/modes && CC_SYSPROMPT_FILE=/dev/null CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude plugin test .` |
| `plugins/insights/scripts` | `cd plugins/insights/scripts/v15/engine/variants/views && uv run --no-project python -m unittest test_view_built test_view_mylevel` |
| `gateway` | `cd gateway && npm ci && npm run typecheck && npm run test:wire` |
| `shim` | `cd shim && npm test` |

Maintainers run more checks you cannot run here, such as a scan for private words. If one fails, we say so on your pull request.

## How it lands

This repo is published from sources kept elsewhere, and some files here are copies or are written by a tool.
So a change lands in more than one place:

1. A maintainer reviews your pull request and merges it here. Your commit stays yours, with your name on it.
2. We carry the same commit into the sources this repo is published from, with your name kept on it.
   Redone at a file's source if it is a copy or written by a tool.
3. A maintainer releases the plugin with a new version. Merged work reaches people only then.
   The release notes on GitHub list the pull requests it carries, so your pull request is credited there.

When a pull request needs small edits, a maintainer may add commits on top of yours on your branch rather than ask you to
redo it, and your commits stay yours. If it needs bigger rework, we ask on the pull request; you can change it yourself.
If we have to redo the change ourselves, you stay as co-author of the commit.

## Licence

By sending a pull request you agree that your change is under the MIT licence of this repo.
