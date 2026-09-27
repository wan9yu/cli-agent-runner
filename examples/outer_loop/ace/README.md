> English · **[中文](README.zh.md)**

# ACE — append one playbook bullet

This page is one recipe. The [map](../README.md) is not a task list.

## Steps

1. Copy `generator.py` and `prompts/` into a new git repo. Do not copy
   `playbook.md` into that repo.
2. Copy `playbook.md` beside the repo. It is the seeded playbook, not
   the ledger. It has one fixed entry and no delta bullet.
3. Copy `agent-runner.toml.example` to `agent-runner.toml` beside the
   repo. `--config` opens `agent-runner.toml`, not the example. Replace
   `WORK`, `PI`, and `PLAYBOOK`. `PLAYBOOK` is the beside-repo playbook.
   Do not set `stop_file`.
4. Copy `delay.py` beside the repo. Run `python3 -m py_compile delay.py`,
   then `python3 delay.py --self-check`. It prints `SELF_CHECK_OK`,
   `WRITE_ONCE round1=1 round2=0`, and `BULLET_FROM_FIELD`.
5. Start the delay script, then start one serve:

```bash
ACE_WORK=<repo> ACE_PLAYBOOK=<playbook.md> python3 delay.py
agent-runner --config <path-to-agent-runner.toml> serve
```

`prompts/program.md` tells the child to edit `generator.py` only. Do not
put the playbook path into either prompt file. Do not put `ACE_DELTA`
into the seed.

## What the delay script writes

`delay.py` appends one bullet after round 1 `round_end` and before
round 2 `round_start`. The bullet copies `goal_check` `name`, `value`,
and `satisfied` only. It does not read assistant text and it does not
call another model. A missing finite value does not append. It uses a
temporary file, fsync, and `os.replace`. The seed bytes stay the prefix.
That is not a wholesale replace. Round 2 does not append.

It writes only while the pid in `logs/serve.pid` is alive, inside the
60 second delay. A file of only digits does not authorize a write. It
does not write the ledger and it does not swap `files[0]`.

## What to look at

After serve exits, open `logs/events-YYYY-MM.jsonl`, the delay log, and
`logs/rounds/R1-*.log`.

- Before the append, the playbook hash equals the seed hash. The fixed
  entry is still the prefix.
- The delay log `decision=write` is before round 2 `round_start`. The
  prefix hash still equals the seed. The suffix is one new bullet,
  `- ACE_DELTA name=... value=... satisfied=...`, taken from that
  round's `goal_check` fields.
- Round 1 has no `ACE_DELTA`. Round 2 still starts with the fixed entry
  and does not replace the file with a different entry.
- Round 2 does not append a second bullet.
- The check command stays `python3 generator.py`. The playbook path is
  not `logs/lessons.md`.

If the file was replaced wholesale, or the bullet came from child text,
that is not this recipe. Do not add rounds or start a second serve.

## Settings already in the example

- `[prompt] files` is `prompts/program.md`, then `logs/lessons.md`, then
  `PLAYBOOK`. The playbook is not `[goal] ledger`. The ledger still
  truncates at 8192. This file does not.
- `restart_delay_s = 60`. `max_rounds = 2`. `round_budget_s = 1800`
  is the per-round wall.
- `dirty_action = "ignore"`.
- Child argv is `pi -p -na --mode json --model PROVIDER/MODEL`. No
  `--no-session`.
- This recipe does not create `stop_file`.
