> English · **[中文](README.zh.md)**

# Ralph — same prompt until the check passes

This page is one recipe. The [map](../README.md) is not a task list.
Shared notes are in [Reading a recipe](../README.md#reading-a-recipe).

## Steps

1. Copy `lane.py`, `bonus.py`, `test_pair.py`, and `prompts/` into a new
   git repo. Leave `lane.py` and `bonus.py` as they are. Both return 0.
   Do not correct them before serve.
2. Copy `agent-runner.toml.example` to `agent-runner.toml` beside that
   repo, next to a copy of `stop-watch.py`. `--config` opens
   `agent-runner.toml`, not the example.
3. In that toml, replace `WORK` with the repo path, `PI` with the pi
   binary, and `STOP` with a path beside the repo. `STOP` must not exist
   yet. It is `stop_file`.
4. Run `python3 -m py_compile stop-watch.py`, then
   `python3 stop-watch.py --self-check`. It prints `SELF_CHECK_OK`.
5. Start the watcher, then start one serve:

```bash
RALPH_WORK=<repo> RALPH_STOP=<STOP> python3 stop-watch.py
agent-runner --config <path-to-agent-runner.toml> serve
```

The child ends each round after one source-file edit and one
`python3 -m unittest test_pair`, even if the suite fails. `serve` keeps
running. The watcher creates `STOP` only after `round_end`, inside the 60
second delay, when that round's `goal_check.satisfied` is true, a later
round has not started, and the pid in `logs/serve.pid` is still alive. A
false check does not create the file.

`prompts/main.md` is the child's task. It is not a step for you. Leave it
unchanged between rounds. The tests stay readable.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl` and search for
`goal_check`. You want an earlier line with `"satisfied": false` and a
later line with `"satisfied": true`. `prompts/main.md` and `test_pair.py`
should be unchanged. `test_pair.py` should not be in `dirty_detected.files`.
The check command should still be `python3 -m unittest test_pair`.

Exit code 0 only means serve stopped. `stop_file_detected` means the
watcher created `STOP`. `max_rounds_reached` means six rounds finished
without that file.

If round 1 is already green, stop. Do not add rounds to invent an earlier
failure. If six rounds end with no passing check, that is the result. Do
not start a second serve.

## Settings already in the example

- `[prompt] files` is `prompts/main.md`, then `logs/lessons.md`. A missing
  ledger file at boot is skipped. That skip is not a check-command change.
- `restart_delay_s = 60`. `round_budget_s = 1800` is the hang wall.
  `max_rounds = 6`.
- `dirty_action = "ignore"`, so the first file edit is still there for
  round 2. `stash` would remove that edit.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--no-session`.
