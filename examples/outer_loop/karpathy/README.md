> English · **[中文](README.zh.md)**

# Karpathy — one file, keep or discard

This page is one recipe. The [map](../README.md) is not a task list.

## Steps

1. Copy `train.py` and `prompts/` into a new git repo. Do not copy
   `snapshot/` or `queue/` into that repo.
2. Copy `snapshot/train.py` beside the repo. That file is the boot
   snapshot. It must match the repo's `train.py` before serve. Copy
   `queue/round2.md` beside the repo. It is not a prompt path.
3. Copy `agent-runner.toml.example` to `agent-runner.toml` beside the
   repo. `--config` opens `agent-runner.toml`, not the example. Replace
   `WORK` with the repo path and `PI` with the pi binary. Do not set
   `stop_file`.
4. Copy `delay.py` beside the repo. Run `python3 -m py_compile delay.py`,
   then `python3 delay.py --self-check`. It prints `SELF_CHECK_OK`.
5. Start the delay script, then start one serve:

```bash
KARPATHY_WORK=<repo> KARPATHY_SNAPSHOT=<snapshot/train.py> \
  KARPATHY_QUEUE=<queue/round2.md> python3 delay.py
agent-runner --config <path-to-agent-runner.toml> serve
```

`prompts/program.md` is the round-1 task. It names one worse edit,
`FLOOR = 1`. It does not name a better edit. `queue/round2.md` asks for
a higher metric and does not repeat `FLOOR = 1`. Do not paste a better
assignment into either file.

## What the delay script writes

`delay.py` reads `goal_check.value` from `logs/events-YYYY-MM.jsonl`.
It does not read assistant text. It writes only while the pid in
`logs/serve.pid` is alive, inside the 60 second delay, before the next
round starts. A file of only digits does not authorize a write.

The boot metric is `10.0`. Larger is better. A missing float is not a
keep.

After round 1, if the value is a finite float below `10.0`, it discards.
It writes the boot snapshot back over `train.py` with `os.replace`, then
writes the queue file over `prompts/program.md` once, also with
`os.replace`, and only if that file still passes prompt smoke. A smoke
failure does neither. It does not run `git checkout` or `stash`.

After round 2, if the value is a finite float above `10.0`, it keeps.
It copies `train.py` onto the snapshot. It does not restore the source
file and it does not swap the prompt again. Round 3 does not write the
snapshot and does not swap the prompt.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl` and the delay log
next to the snapshot.

- One file: `train.py`. One metric: `goal_check` name `metric`.
- Fixed budget: `max_rounds_reached` with `max_rounds` 3. `agent_spawn`
  `timeout_s` 1800 is the per-round wall, not that budget.
- A discard line has `decision=discard`, a restore hash equal to the
  seed hash, and two prompt hashes that differ.
- A keep line has `decision=keep`, a snapshot hash that differs from
  the seed, and `not_restore=1`.
- The check command stays `python3 train.py`.

If one of those lines is missing, that is the result. Do not add rounds
or start a second serve.

## Settings already in the example

- `[prompt] files` is `prompts/program.md`, then `logs/lessons.md`.
  The queue file is not a prompt path.
- `restart_delay_s = 60`. `max_rounds = 3`. `round_budget_s = 1800`
  is the hang wall.
- `dirty_action = "ignore"`. `stash` is not a discard.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--no-session`.
- This recipe does not create `stop_file`.
