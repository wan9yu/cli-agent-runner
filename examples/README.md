> English · **[中文](README.zh.md)**

# Examples — outer loops against a running serve

These are **not** a second supervisor. Copy a directory next to a project
that already runs `agent-runner serve`. Serve keeps schedule, isolation,
pre-OOM, and give-up. Semantics:
[docs/configuration.md](../docs/configuration.md) § Config reload.

Do **not** loop `agent-runner round`. Do **not** auto-restart serve on
exit 78/75/70.

## How we think about this

2026 “harness” write-ups are usually an **outer** loop: leave the coding
CLI unchanged, change what the next round reads. `serve` is the process
layer in the middle. These examples are not a second supervisor.

| Layer | Job | Not our job |
|---|---|---|
| Inner | tool → observe → compact inside one CLI session | reimplement Codex / pi / OpenHands |
| Outer (`serve`) | one round, one process; wall, cgroup, give-up, JSONL | curriculum, replay, or exam mode |
| Meta (`examples/`) | rewrite hot files, read history | own scheduling, isolation, or give-up |

Lifetimes are honest (cold / hot / mixed). Replaces are atomic. Events
are readable. The kill path does not read `config_digest` or goal notes.

- **between_rounds** — change the next prompt; do not own scheduling,
  isolation, or give-up. 78/75/70 stay down until a human restarts.
- **digest_watch** — print what the child hashed (paths and bytes). Not
  a give-up input.
- **outer_loop** — map Dream-RSI / ACE / Ralph / Codex onto those two
  scripts. The loops stay outside `serve`.

| Directory | What |
|---|---|
| [between_rounds/](between_rounds/) | Atomic prompt swap after `round_end` |
| [digest_watch/](digest_watch/) | Print `config_digest` / `config_changed` |
| [outer_loop/](outer_loop/) | 2026 harness map (Dream-RSI, ACE, Ralph, Codex inner vs our outer) |

Per-CLI setup (pi, aider, containers) stays in
[docs/recipes/](../docs/recipes/).

## Patterns that need no extra process

**Day / night prompt.** Write `night.md` onto the between_rounds `--queue`
before the evening window; `day.md` before morning. Cold keys (`round_budget_s`,
`[agent] command`, schedule) still need `agent-runner restart`.

**Check failed → next instruction.** When `[[goal.checks]]` goes red, drop a
prompt that says "fix that check" onto the same queue. The curriculum stays
outside serve.
