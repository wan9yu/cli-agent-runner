> English · **[中文](README.zh.md)**

# LongHorizon — the fact comes from the check

This page is one recipe. The [map](../README.md) is not a task list.

## Steps

1. Copy `check_marker.py` and `prompts/` into a new git repo. Do not copy
   `snapshot.md` into that repo.
2. Put a copy of `snapshot.md` beside the repo. Create an empty `state.txt`
   in that same beside-repo directory.
3. Copy `agent-runner.toml.example` to `agent-runner.toml` beside the repo.
   `--config` opens `agent-runner.toml`, not the example. Replace `WORK`
   with the repo path, `PI` with the pi binary, and `SNAPSHOT` with the
   beside-repo `snapshot.md` path. Do not set `stop_file`.
4. Copy `delay.py` beside the repo. Start it, then start one serve:

```bash
LH_WORK=<repo> LH_SNAPSHOT=<snapshot.md> LH_STATE=<state.txt> python3 delay.py
agent-runner --config <path-to-agent-runner.toml> serve
```

`prompts/main.md` is the child's task. You change only its last line,
`Instruction:`, and only during the delay. The lines above it stay put.

## What the delay script writes

`delay.py` reads `goal_check` from `logs/events-YYYY-MM.jsonl`. It does
not read assistant text when deciding what to write. It writes only while
the pid in `logs/serve.pid` is alive, inside the 60 second delay.

On a red check, it makes exactly two writes:

1. Put the word `retry` in `snapshot.md`.
2. Replace only the `Instruction:` line so it tells the child to create
   `marker.txt` in the repository root.

It does not append `state.txt` on red. It does not create `stop_file`.

On the first green check, it makes exactly two writes:

1. Append `value=<goal_check.value>` to `state.txt` once. The value comes
   from the event.
2. Put that same value and the word `done` in `snapshot.md`.

It does not put `done` or the value into the round-3 `Instruction:` line.
A later green round does not append `state.txt` again.

This recipe does not use the words `blocked` or `ask`, and it does not
create `stop_file`.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl`.

- Round 1: an `agent_end` message with `role == assistant` contains
  `DONE`, that round's `goal_check.satisfied` is false, and `state.txt`
  is unchanged. User text in that record does not count. The transcript
  is `logs/rounds/R1-*.log`.
- Round 2: the check command is still `python3 check_marker.py`,
  `marker.txt` exists, and the new `state.txt` line equals that event's
  `value`.
- Round 3: the `Instruction:` line contains neither `done` nor the value.

`dirty_action` is `ignore` because the instruction-line edit and
`marker.txt` must still be in the repo for the next round. `stash` would
remove both.

Round 1 `config_changed: true` is the first digest. A later digest change
is expected when the instruction line or `snapshot.md` changes. The check
command must stay `python3 check_marker.py`.

## Settings already in the example

- `[prompt] files` is `prompts/main.md`, then `logs/lessons.md`, then
  `SNAPSHOT`. `state.txt` is not a prompt file and is not the ledger.
- `restart_delay_s = 60`. `max_rounds = 3`.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--no-session`.
