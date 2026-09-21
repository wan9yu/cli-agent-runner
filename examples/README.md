# Examples — outer loops against a running serve

These are **not** a second supervisor. Copy a directory next to a project
that already runs `agent-runner serve`. Serve keeps schedule, isolation,
pre-OOM, and give-up. Semantics:
[docs/configuration.md](../docs/configuration.md) § Config reload.

Do **not** loop `agent-runner round`. Do **not** auto-restart serve on
exit 78/75/70.

## How we think about this

Interesting 2026 “harness” work is almost always an **outer** loop:
leave the coding CLI unchanged, change what the *next* expensive step
reads. We occupy the process layer in the middle, not the dreamer.

| Layer | Job | Not our job |
|---|---|---|
| Inner | tool → observe → compact inside one CLI session | reimplement Codex / pi / OpenHands |
| Outer (`serve`) | one round, one process; wall, cgroup, give-up, JSONL | decide what to remember or whether 0.8 should kill |
| Meta (`examples/`) | rewrite hot files, read history | become a second supervisor |

**Fill the seat, don’t take it (补位, 不越位).** Lifetimes are honest
(cold / hot / mixed). Replaces are atomic. Events are readable. The kill
path does not read `config_digest` or goal notes. We do not manage a
curriculum, a replay tree, or an exam mode inside `serve`.

The three directories are that philosophy as code:

- **between_rounds** — you may change the next prompt; you may not own
  scheduling, isolation, or give-up. 78/75/70 stay down until a human
  restarts.
- **digest_watch** — see what the child *actually hashed* (paths and
  bytes). A lamp is not a judge and not a kill input.
- **outer_loop** — map Dream-RSI / ACE / Ralph / Codex onto those two
  primitives. The map is the product; a dreamer is not.

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
