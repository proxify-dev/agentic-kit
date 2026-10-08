# Example Queries

Every question is a verb: `sessions <verb>` (the conversations) and `trace <verb>` (what was
done in them). Each output ends on the next command to run.

## Turns across sessions

```bash
sessions turns --plugin search                  # this project's turns credited to a plugin
sessions turns --plugin search --errors
sessions turns --agent-type search:miner --all  # every project
sessions turns --tool Agent
sessions turns --skill recall                   # recall matches observer:recall too
sessions turns --since 2026-04-01 --until 2026-04-08
sessions turns --branch feat/docs
sessions turns ~/code/app --tool Bash --errors  # another project
```

## Totals and patterns

```bash
sessions turns --all --stats                    # every project, one aggregate
sessions turns --plugin meta --stats
sessions turns --plugin search --errors --patterns
```

## Search

```bash
sessions search "sandbox"                         # every project, ranked; caller + automated left out
sessions search "sandbox" --since 2026-09-01 --limit 20
sessions search "sandbox" --entrypoint all        # claude -p / SDK runs too
sessions search "ENOENT" --tool Bash --outcome error --since 2026-09-01
sessions search "hooks.json" --under ~/agentic-kit   # only projects owning that path
```

## Inspect: session → turn → steps

```bash
sessions show abc12345                     # start here: the session (a uuid prefix is enough)
trace turn abc12345-003                    # one turn's steps, each with what came back
trace turn abc12345-003 --steps 11-40      # just those steps of a long turn
trace turn abc12345-003 --open 1,4,5       # several steps whole, in one call
```

## One session

```bash
sessions show <uuid> --stats    # its totals: interactions, errors, tools, plugins
sessions show <uuid> --patterns
sessions replay <uuid>          # prompt + answer per turn
sessions replay <uuid> --role user
```
