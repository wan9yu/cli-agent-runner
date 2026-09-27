> English · **[中文](README.zh.md)**

# Loop engineering — the check is not the child's "done"

This page is one recipe. The [map](../README.md) is not a task list.

## Steps

1. Copy `add.py`, `suite_add.py`, and `prompts/` into a new git repo.
2. Copy `agent-runner.toml.example` to `agent-runner.toml` beside that
   repo. `--config` opens `agent-runner.toml`, not the example.
3. In that toml, replace `WORK` with the repo path and `PI` with the pi
   binary.
4. Start one serve:

```bash
agent-runner --config <path-to-agent-runner.toml> serve
```

`max_rounds` is 2. That same process runs round 2 even if round 1's check
fails. Do not start another serve to chase a passing check. That chase is
the [Ralph](../ralph/) recipe.

`prompts/main.md` is the child's task. Leave it unchanged between rounds.
It only mentions `add(1, 2)`. The unittest also wants `add("1", 2)` to
raise. You are not trying to make that suite pass in this recipe.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl`.

Round 1 should have both of these:

- An `agent_end` message with `role == assistant` whose text is `DONE`,
  in `logs/rounds/R1-*.log`. User text in that record does not count. A
  quote of the print instruction does not count. `logs/round-1.log` is
  the operator log, not that transcript.
- That round's `goal_check.satisfied` is false.

Round 2 starting is this serve continuing. `prompts/main.md` should be
unchanged. Round 1 `config_changed: true` is the first digest, not an
edit. Compare `config_digest` on the two `round_start` lines, or hash
the prompt file.

The check command is `python3 -m unittest suite_add.py`. The child's
`DONE` is not that check.

## Settings already in the example

- `[prompt] files` is `prompts/main.md`, then `logs/lessons.md`.
- `restart_delay_s = 60`. `round_budget_s = 1800` is the hang wall.
  `max_rounds = 2`.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--no-session`.
