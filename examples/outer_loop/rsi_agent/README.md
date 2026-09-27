> English · **[中文](README.zh.md)**

# RSIAgent — the outer queue sets the next task

This page is one recipe. The [map](../README.md) is not a task list.

## Steps

1. Copy `actor.py` and `prompts/` into a new git repo. Do not copy
   `course.md` or `queue/` into that repo.
2. Copy `course.md` beside the repo. It is the round-1 course and
   contains task A only. Copy `queue/task-b.md` beside the repo. It is
   not a prompt path. Task B is only in that queue file before serve.
3. Copy `agent-runner.toml.example` to `agent-runner.toml` beside the
   repo. `--config` opens `agent-runner.toml`, not the example. Replace
   `WORK`, `PI`, and `COURSE`. `COURSE` is the beside-repo course file.
   Do not set `stop_file`.
4. Copy `delay.py` beside the repo. Run `python3 -m py_compile delay.py`,
   then `python3 delay.py --self-check`. It prints `SELF_CHECK_OK` and
   `WRITE_ONCE round1=1 round2=0`.
5. Start the delay script, then start one serve:

```bash
RSI_WORK=<repo> RSI_COURSE=<course.md> RSI_QUEUE=<queue/task-b.md> \
  python3 delay.py
agent-runner --config <path-to-agent-runner.toml> serve
```

`prompts/program.md` is the standing instruction. It does not name task
B. Do not put the course path into either prompt file.

## What the delay script writes

`delay.py` appends the queue file onto the course file once, after
round 1 `round_end` and before round 2 `round_start`. It uses a
temporary file, fsync, and `os.replace`. The boot bytes stay the prefix.
It does not swap `files[0]`. It does not restore `actor.py`. It does not
read assistant text. A missing float does not block this write. Round 2
does not write.

It writes only while the pid in `logs/serve.pid` is alive, inside the
60 second delay. A file of only digits does not authorize a write.

The verifier stays `python3 actor.py`. That command prints one finite
float. It is not an LLM.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl`, the delay log, and
`logs/rounds/R1-*.log`.

- Before the write, the course hash equals the boot hash.
- The round-1 user prompt has `TASK_A_CURRICULUM` and does not have
  `TASK_B_CURRICULUM` or `MARK_B = 1`. The child edits `actor.py`.
- The delay log `decision=write` is before round 2 `round_start`. The
  prefix hash still equals the boot hash, and the file then contains
  task B.
- Round 2 does not write the course again.
- The check command is still `python3 actor.py`. A digest change is the
  course bytes, not a new verifier.

If task B was already in the round-1 prompt, that is not this recipe.
Do not add rounds or start a second serve.

## Settings already in the example

- `[prompt] files` is `prompts/program.md`, then `logs/lessons.md`, then
  `COURSE`. The queue file is not a prompt path. The course file is not
  the ledger.
- `restart_delay_s = 60`. `max_rounds = 2`. `round_budget_s = 1800`
  is the per-round wall.
- `dirty_action = "ignore"`, so the round-1 edit to `actor.py` is still
  there for round 2.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--no-session`.
- This recipe does not create `stop_file`.
