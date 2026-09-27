> English · **[中文](README.zh.md)**

# Repeated failure — stop on the same red

This page is one recipe. The [map](../README.md) is not a task list.

## Steps

1. Copy `notes.md` and `prompts/` into a new git repo. Do not copy
   `fail_source.py` or `delay.py` into that repo.
2. Copy `fail_source.py` beside the repo. It prints `0.25` and exits 1.
   It does not read the repo. The assignment that would flip that value
   is `VALUE = 1.0`. That line is not in the prompt and not in the real
   failure source.
3. Copy `agent-runner.toml.example` to `agent-runner.toml` beside the
   repo. `--config` opens `agent-runner.toml`, not the example. Replace
   `WORK`, `PI`, `FAIL_SOURCE`, and `STOP`. `FAIL_SOURCE` and `STOP` are
   absolute paths beside the repo. `STOP` must not exist yet.
4. Copy `delay.py` beside the repo. From this recipe directory, run
   `python3 -m py_compile delay.py`, then `python3 delay.py --self-check`.
   It prints `SELF_CHECK_OK`, `first_red_recorded=1`, and
   `would_stop_after_second_identical_red=1`. It does not create `STOP`.
   On Linux it also prints `proc_create_time_match=1`.
5. Start the delay script, then start one serve:

```bash
REPEAT_WORK=<repo> REPEAT_CFG=<beside> REPEAT_STOP=<STOP> python3 delay.py
agent-runner --config <path-to-agent-runner.toml> serve
```

`prompts/program.md` asks the child to edit `notes.md` only. It does not
ask to make the check pass, and it does not contain `VALUE = 1.0`.

## What the delay script writes

`delay.py` is the only outer writer. It does not start serve, does not
loop `agent-runner round`, and does not restart on 78, 75, or 70. It
does not read assistant text. An advisory is not a stop.

It reads JSON `logs/serve.pid`. A bare integer, or a record without
`create_time`, does not authorize a write. On Linux it matches `/proc`
start time within 1 second. It does not use `ps -o lstart=`.

Round 1 red: record `satisfied` and `value`. Do not create `STOP`.
Round 2 red: if the fingerprint is unchanged, create `STOP` inside the
60 second delay, before round 3 `round_start`.

A finite value makes the fingerprint `(satisfied, value)`. Two reds
with no `value` still match on `satisfied` only. One value and one
missing value do not match. Different values do not match.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl` and the delay log.
Exit code 0 only means serve stopped.

- Two red `goal_check` lines share the fingerprint. Bind a check with
  no `round_num` to the surrounding `round_start` and `round_end`.
- The round-1 delay line has `decision=record` and `stop_created=0`.
- The round-2 delay line has `fingerprints_equal=1`, then
  `decision=create stop_created=1 in_delay=1 before_round3_start=1`.
- The terminal event is `stop_file_detected`, not `max_rounds_reached`.
  There is no round 3 `round_start`.

If the values differ, the stop is late, or the terminal event is not
`stop_file_detected`, that is not this landing. Do not add rounds or
start a second serve.

## Settings already in the example

- `[prompt] files` is `prompts/program.md`, then `logs/lessons.md`.
  `fail_source.py` is not a prompt path.
- `restart_delay_s = 60`. `max_rounds = 3`. `round_budget_s = 1800`
  is the per-round wall, so a stop is not the round cap.
- `dirty_action = "ignore"`. `stash` is not the stop.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--no-session`.
- `stop_file` is set at boot. The file itself is absent until the gap
  creates it.
