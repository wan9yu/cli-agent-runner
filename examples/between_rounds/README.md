# Between-round file swap

Copy `between_rounds.py` next to a project that already runs
`agent-runner serve`. It is not a second supervisor.

Serve stays load-bearing (schedule, isolation, pre-OOM, give-up). This
process only replaces **hot** files between rounds. Semantics:
[configuration.md](../../docs/configuration.md) § Config reload.

## When to use it

You want the next round's prompt or check command lines to change without
restarting serve. Cold and mixed keys (`round_budget_s`, check *count*,
`[agent]` command, schedule, host-health) still need `agent-runner restart`.

Do **not** loop `agent-runner round` yourself. Do **not** auto-restart serve
on exit 78/75/70.

## Use it

```bash
python examples/between_rounds/between_rounds.py \
  --log-dir logs \
  --prompt prompts/main.md \
  --queue /tmp/next-prompt.md
```

After each `round_end`, if `--queue` exists and would pass prompt smoke
(non-empty, ≥ 500 bytes, first character not `-` / whitespace), it is written
onto `--prompt` with tmp + fsync + `os.replace`, then unlinked. The **next**
round child re-reads it.

Extra notes that must survive the treadmill belong in another `[prompt] files`
entry, not `[goal] ledger` (the child prepends advisories and truncates that
file to 8192 bytes).

## Give-up

If the event stream shows `config_broken`, `crash_loop`,
`stalled_no_progress`, or `mem_loop_persistent`, the script prints the kind
and exits **without** restarting serve. Fix the config or host, then
`agent-runner start` / `restart` yourself.

## History vs live config

`peek --json` reloads TOML now; it can disagree with serve's boot copy.
Between-round attribution is `round_start.config_digest` on
`log_dir/events-YYYY-MM.jsonl` ([events.md](../../docs/events.md)): it hashes
listed prompt-file **paths and bytes**, so a same-path body swap moves it.
`config_changed=true` is not proof an operator swap landed — a `[goal]`
ledger listed in `[prompt] files` also moves the digest (treadmill advisory,
or the agent editing it).
