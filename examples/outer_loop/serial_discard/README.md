> English · **[中文](README.zh.md)**

# Serial discard — a red attempt does not merge

This page is one recipe. The [map](../README.md) is not a task list.

## Steps

1. Copy `attempt.py`, `notes.md`, and `prompts/` into a new git repo.
   That repo is the attempt directory. Do not copy `keep/` or
   `visible_check.py` into it.
2. Copy `keep/` beside the repo. It is the planted keep tree. It is not
   a prompt file. Copy `visible_check.py` beside the repo. It prints
   `0.0` and exits 1. It does not read the attempt directory or the
   keep directory.
3. Copy `agent-runner.toml.example` to `agent-runner.toml` beside the
   repo. `--config` opens `agent-runner.toml`, not the example. Replace
   `WORK` with the absolute attempt directory before boot. A relative
   path would resolve against the config directory. Replace `PI` and
   `CHECK`. `CHECK` is the absolute path of `visible_check.py`. Do not
   set `stop_file`. Do not set `auto_commit`.
4. Copy `delay.py` beside the repo. From this recipe directory, run
   `python3 -m py_compile delay.py`, then `python3 delay.py --self-check`.
   It prints `SELF_CHECK_OK`, `no_merge=1`, and `not_karpathy_restore=1`.
   It does not modify `keep/`. That stdout is not a serve gap line.
   On Linux it also prints `proc_create_time_match=1`.
5. Start the delay script, then start one serve. Do not create a worktree.

```bash
SERIAL_WORK=<attempt> SERIAL_CFG=<beside> python3 delay.py
agent-runner --config <path-to-agent-runner.toml> serve
```

`prompts/program.md` asks the child to edit `notes.md` only and to leave
`attempt.py` unchanged. It does not contain `VALUE = 1.0`.

## What the delay script writes

`delay.py` is the only outer writer. It does not start serve, does not
loop `agent-runner round`, and does not restart on 78, 75, or 70.

It reads JSON `logs/serve.pid`. A bare integer does not authorize a
claim. On Linux it matches `/proc` start time within 1 second. It does
not use `ps -o lstart=`.

Before serve, the delay log records that `work_dir` is the attempt
directory. After each red `round_end`, inside the 60 second delay and
before the next `round_start`, it claims `no_merge=1` and
`not_karpathy_restore=1`. It does not copy the attempt into the keep
tree. It does not write a snapshot back over `attempt.py`. `stash` is
not the discard. It does not open a worktree.

A green round 1 is not this landing. Do not start a second serve to
invent a red round.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl` and the delay log.
The work tree after serve is not a substitute for the delay-log lines.
Exit code 0 only means serve stopped.

- Round 1 `goal_check` is red. Bind a check with no `round_num` to the
  surrounding `round_start` and `round_end`.
- The before-serve delay line has `work_dir_is_attempt=1` and
  `toml_work_dir_equal=1`.
- Every red gap has `decision=no_merge no_merge=1
  not_karpathy_restore=1 keep_equals_planted=1 in_delay=1`, and the
  keep hash equals the planted hash. Round 2 red needs that line too.
- `new_worktree=0`. The check command is still the beside-repo
  `visible_check.py` path you set at boot.

If round 1 is already green, the keep directory changes, or the attempt
source is written back by metric, that is not this landing. Do not
start a second serve.

## Settings already in the example

- `[prompt] files` is `prompts/program.md`, then `logs/lessons.md`.
  The keep directory is not a prompt path.
- `restart_delay_s = 60`. `max_rounds = 2`. `round_budget_s = 1800`
  is the per-round wall.
- `dirty_action = "ignore"`. No `auto_commit`. No `stop_file`.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--no-session`.
