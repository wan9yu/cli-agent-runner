> English · **[中文](README.zh.md)**

# Spend ceiling — sum the events, then stop

This page is one recipe. The [map](../README.md) is not a task list.

## Steps

1. Copy `marker.md` to `README.md` in a new git repo. Copy `prompts/`
   into that repo. Do not copy `delay.py` or this page into the repo.
2. Copy `agent-runner.toml.example` to `agent-runner.toml` beside the
   repo. `--config` opens `agent-runner.toml`, not the example. Replace
   `WORK`, `PI`, and `STOP` with absolute paths. A relative `WORK`
   resolves against the config directory. A relative `STOP` resolves
   inside the repo, and the delay script will not create it there.
   `STOP` is beside the repo and must not exist yet. Replace
   `PROVIDER/MODEL` with a model that emits `cost_usd`. The child argv
   is the pi binary, then `-p -na --mode json --model PROVIDER/MODEL`.
   No `--tools`. No `--no-session`. Do not add a budget table.
3. Copy `delay.py` beside the repo. From this recipe directory, run
   `python3 -m py_compile delay.py`, then `python3 delay.py --self-check`.
   It prints `SELF_CHECK_OK`, `missing_usage_advanced=0`, and
   `ceiling_stop_temp_only=1`. It does not create `STOP`. It does not
   print a money or token total. That stdout is not a serve gap line.
   On Linux it also prints `proc_create_time_match=1`.
4. Start the delay script, then start one serve. Do not loop
   `agent-runner round`. Do not restart on 78, 75, or 70. Do not add
   rounds. Do not start a second serve.

```bash
SPEND_WORK=<repo> SPEND_CFG=<beside> SPEND_STOP=<STOP> python3 delay.py
agent-runner --config <path-to-agent-runner.toml> serve
```

`prompts/program.md` asks for one sentence that includes `MARKER`. It
does not mention `cost_usd`.

## What the delay script writes

`delay.py` is the only outer writer. It does not start serve. It does
not edit the cold command. It does not read goal notes. It does not
write a money or token total.

It reads JSON `logs/serve.pid`. A bare integer, or a record without
`create_time`, does not authorize a write. On Linux it matches `/proc`
start time within 1 second. It does not use `ps -o lstart=`.

After a round `round_end`, it sums positive finite `cost_usd` values
from `agent_usage_recorded` events for ended rounds. A missing usage
event does not enter that list and is not treated as zero. Null and
zero do not enter it either. The ceiling is a positive threshold inside
the script. One positive finite `cost_usd` crosses it. The script does
not write that threshold or the sum.

When the ceiling is crossed, it creates `STOP` inside the 60 second
delay, before the next `round_start`. The file body is the word
`ceiling`. `max_rounds` and `round_budget_s` are not that sum.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl` and the delay log.
Exit code 0 only means serve stopped. Do not copy a money or token
total out of those files.

- The delay line has `decision=write`, `summed=1`,
  `missing_usage_advanced=0`, `missing_not_zero=1`,
  `ceiling_crossed=1`, `stop_created=1`, `in_delay=1`,
  `before_next_round_start=1`, and `total_recorded=0`.
  `total_recorded=0` means no total was written. It does not mean a
  missing event is zero. `cost_usd_events_in_sum` is a count, not a sum.
- `STOP` exists. Its body has no digit and no `$`.
- The terminal event is `stop_file_detected`. There is no round 2 and
  no `max_rounds_reached`.
- `max_rounds` is still 3. `round_budget_s` is still 1800. Neither is
  the sum. There is no `[budget]` table.

If the usage event is missing, the ceiling does not cross. Do not treat
that missing event as zero. Do not invent a positive value. Do not add
rounds and do not start a second serve.

## Settings already in the example

- `[prompt] files` is `prompts/program.md`, then `logs/lessons.md`.
- `restart_delay_s = 60`. `max_rounds = 3`. `round_budget_s = 1800`
  is the per-round wall, so a stop is not the round cap.
- `dirty_action = "ignore"`. `stash` is not the ceiling.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--tools`. No `--no-session`.
- `stop_file` is set at boot. The file itself is absent until the gap
  creates it.
