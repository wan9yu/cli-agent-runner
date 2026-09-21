# Examples — outer loops against a running serve

These are **not** a second supervisor. Copy a directory next to a project
that already runs `agent-runner serve`. Serve keeps schedule, isolation,
pre-OOM, and give-up. Semantics:
[docs/configuration.md](../docs/configuration.md) § Config reload.

Do **not** loop `agent-runner round`. Do **not** auto-restart serve on
exit 78/75/70.

| Directory | What |
|---|---|
| [between_rounds/](between_rounds/) | Atomic prompt swap after `round_end` |
| [digest_watch/](digest_watch/) | Print `config_digest` / `config_changed` |

Per-CLI setup (pi, aider, containers) stays in
[docs/recipes/](../docs/recipes/).

## Patterns that need no extra process

**Day / night prompt.** Write `night.md` onto the between_rounds `--queue`
before the evening window; `day.md` before morning. Cold keys (`round_budget_s`,
`[agent] command`, schedule) still need `agent-runner restart`.

**Check failed → next instruction.** When `[[goal.checks]]` goes red, drop a
prompt that says "fix that check" onto the same queue. The curriculum stays
outside serve.
