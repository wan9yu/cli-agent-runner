# agent-runner thesis: what we do and don't do

This document lists explicit **non-goals** of agent-runner. It exists to:

- Save back-and-forth when evaluating new feature requests.
- Prevent gradual scope creep ("just one more knob").
- Communicate boundaries to consumers and future integrators.

---

## What agent-runner IS

agent-runner is a **process supervisor** for long-running CLI agents (claude,
gemini, or any command-line tool). It:

1. Launches the agent subprocess in a loop.
2. Records structured events to a flat JSONL file (`events-YYYY-MM.jsonl`).
3. Provides **defenses** — hard stops for known-harmful states (rate limit
   floods, disk full, OAuth expiry, stuck loops). Each defense codifies a
   specific observed failure mode with a concrete trigger signature.
4. Exposes **plugin hooks** (`PreRoundHook`, `PostRoundHook`, `ContextEnricher`,
   `ServeStartupHook`) for extension without modifying core.

That's the complete scope. The layers are thin by design.

---

## What agent-runner is NOT

### Not a BI tool

No cost dashboards, budget ceilings, spend caps, or trend analytics.

We emit `cost_usd` per round in `agent_usage_recorded`. Five lines of jq
cover 90 % of consumer reporting needs. Consumers who want dashboards or
alerts ship them in their own stack.

> **Example**: A 2026-05-18 proposal requested `[budget] max_cost_per_round_usd = 1.0`
> to stop rounds that exceed a spend threshold. Rejected. We emit the raw cost;
> a PostRoundHook or a 5-line wrapper script enforces any cap the consumer
> wants. Building it natively starts a path toward dashboards, alert rules,
> and report templates that we explicitly don't want to own.

### Not a novelty / anomaly-detection tool

No N-sigma deviation alerts on round duration, token volume, cost trajectory,
or behavior distribution.

We codify **specific scars**: 8× same tool call = stuck loop; `result` event
then silence = hung agent. Not generic anomaly. Per-project variance in
token usage and round duration is large enough that rolling-baseline alerting
would produce constant false positives across diverse workloads.

The `anomaly_repetitive_active` detector (added 0.1.32) is the live example:
it fires when the claude plugin emits `anomaly_repetitive_tool` events
above a fixed threshold within a window — a specific signature, not N-σ.
`max_grace_after_result_s` (0.1.31, refined 0.1.38) is another: a fixed
grace after the `result` event, the subprocess is killed only if its
process group has no live worker left — specific signature, not "is this
subprocess behaving unusually".

> **Example**: A 2026-05-18 proposal requested a "cost spike detector" that
> fires when this round's cost is N× the rolling 7-day average. Rejected.
> The rolling baseline itself requires aggregation we don't own, and the
> threshold N is project-specific. A consumer can compute this from the flat
> events file.

### How we handle transient errors: server-authoritative vs estimated

`transient_error_detected` events carry a `reset_at_epoch` field telling
the supervisor when to retry. Two cases with different policies:

- **Server-authoritative**: Anthropic's `rate_limit_event.resetsAt` is an
  exact unblock time. We respect it verbatim — no backoff multipliers, no
  caps applied. Server knows best.
- **Estimated**: For other classifications (`rate_limit_model`,
  `api_transient_5xx`, `api_timeout`), the plugin emits a default guess
  (`_BACK_OFF_DEFAULTS[bucket]`). Guesses can be wrong; if a round fires
  the same bucket again after waiting our guess, we increase the wait
  exponentially (`2^N`, capped at 32× and 30 minutes absolute).

This split keeps the policy simple: trust the server when it talks, and
back off our own estimates when they prove insufficient. It is **not**
N-σ novelty detection (which we reject — see the section above); it
codifies the specific scar of "fixed-per-bucket backoff insufficient
during sustained upstream outage."

Counter reset: any round that completes without firing a new
`transient_error_detected` event clears all bucket counters back to zero.

> **Example**: A 2026-05-18 field report described sustained 5xx + 529 from
> Anthropic where our previous fixed 60s wait was too short — the next
> round hit the same error, waited 60s again, and again. Rejected: adding
> a config knob (`[runtime] transient_backoff_strategy = "fixed" |
> "exp"`). Instead: upgraded the default policy to exp backoff
> transparently, since "the default was wrong" is the right framing — not
> "the operator should pick between two strategies."

### Not an analytics database

No `--select`-able query language beyond simple peek selectors. No event
aggregation primitives (rollups, group-by, time bucketing, JOIN across rounds).

`events-YYYY-MM.jsonl` is a flat file. Pipe it to `jq`, `duckdb`, or any
tool of the consumer's choice. We deliberately avoid becoming a queryable
store — that path ends with schema migrations, index management, and storage
lifecycle concerns that are not ours to own.

> **Example**: A 2026-05-18 proposal requested `agent-runner peek --aggregate
> cost 7d` to return a 7-day spend rollup. Rejected. DuckDB + 3 lines of SQL
> against the events file gives the same result without us shipping a query
> engine.

### Not a multi-tenant supervisor

One project per `agent-runner.toml`. No cross-project rollups, no shared
event stores, no tenant namespacing.

Multi-project orchestration belongs at a higher layer (a wrapper script, a
scheduler, a platform). agent-runner is a single-project primitive.

> **Example**: A proposal to add a `[projects]` table mapping aliases to
> config paths (to run `agent-runner status --all`) was rejected. Each project
> is autonomous; a shell loop over project directories is the right primitive.

### Not a prompt-engineering framework

No prompt template assembly DSL, no config-driven multi-part composition, no
role-rotation patterns enforced by core.

`PreRoundHook` already handles arbitrary prompt assembly. A plugin can read
any files, call any API, and write the final prompt to disk before the agent
starts. Adding a `[prompt.assembly]` config schema saves consumers ~30 LOC at
the cost of permanent TOML surface area — a bad trade.

> **Example**: A 2026-05-18 proposal requested `[prompt] parts = ["system.md",
> "context.md"]` to compose multi-file prompts natively. Rejected. A
> 20-line PreRoundHook concatenates them. Core TOML surface is permanent;
> plugin surface is not.

### Not a remediation framework

We detect (events) and provide hard-stop defenses for "continuing is harmful"
cases. We do NOT provide config-driven remediation chains (alert vs. stop vs.
hook vs. custom action).

> **Example**: A 2026-05-18 proposal requested `[runtime] transient_error_action`
> to support `alert` (send webhook) in addition to `back_off` and `stop`.
> Rejected. Webhook delivery is a consumer concern (reliability, retries,
> auth, schema). The existing `stop` + PostRoundHook combination covers it
> without us owning webhook plumbing.

### No first-class concept of "the right way to use claude"

We don't enforce a particular prompting style, session discipline, role
rotation pattern, or attestation scheme.

That's prompt-engineering project policy. It varies per use case and evolves
faster than a library version cycle. We are a runtime harness, not a usage
methodology.

### Not a remote-execution portal (agent-local, shell-remote)

agent-runner assumes the agent and the supervisor run on the **same host**.
The unattended model requires it: to survive a disconnected laptop and run
24×7, the agent must live on the supervised host, not stream commands to it
from elsewhere. We do not route an agent's tool calls to a remote shell (SSH,
container, k8s pod). `monitor --host` provides remote *observation*, not remote
*execution*.

Tools like [zmx](https://zmx.sh) cover the complementary case — an
interactive, attended agent that stays local while its shell runs remotely.
That's a different niche (a human watching, full local MCP/skills, ephemeral
sessions), and the two compose: a consumer can point `[agent].command` at an
agent that itself routes through such a portal. But a portal adapter in core
would be an anticipatory feature for a topology our model doesn't use.

> **Example**: The 2026-04 zmx "ai portal" release (agent-local, shell-remote
> via a session) prompted this entry. It validates our CLI-not-MCP stance
> (its own prior-art notes call MCP servers a configuration pain), but adding
> remote-execution routing to agent-runner is rejected until a consumer
> presents a concrete unattended use case that needs it. Note for combined
> deployments: command + output flowing through such a session is a secret
> surface outside agent-runner's control (cf. the 0.1.40 event-log
> containment) — the operator owns it.

---

## How to evaluate a feature request

A proposed feature is **in-scope** if it:

1. Codifies a **specific observed failure mode** with a concrete trigger
   signature (not "watch for anything weird").
2. Is **generic** across agent types (claude, gemini, any future CLI).
3. Requires **no config knobs** whose defaults would be wrong for a
   non-trivial fraction of users.
4. Does **not** grow the BI / analytics / dashboard surface.
5. Cannot be adequately served by a PostRoundHook plugin in ≤ 50 LOC.

A proposed feature is **out-of-scope** if it:

- Aggregates, projects, or reports raw events (belongs in consumer stack).
- Anticipates unknown or workload-specific failure modes.
- Adds tunability for a behavior whose existing default is correct.
- Encodes a particular project's workflow conventions.
- Requires us to own reliability, retries, or auth for external services.

**Default to NO.** Adding surface is permanent; removing it is a breaking
change. The bar for "in-scope" must be high.

---

## Examples

### Accepted: stuck-loop detection

**Proposal**: emit `stuck_loop_detected` when the same tool call appears ≥ 8×
in one round.

**Evaluation**: specific trigger signature (8× repetition), generic across
agents, no wrong-default knob, not BI. Codifies a concrete scar observed in
production. Accepted.

### Rejected: cost budget ceiling

**Proposal**: `[budget] max_cost_per_round_usd = 1.0` — stop the round if
cost exceeds the cap.

**Evaluation**: BI-adjacent (requires us to surface a configurable dollar
threshold), consumer-specific default, easily replaced by a PostRoundHook
that reads `cost_usd` from the emitted event. Rejected.

### Rejected: OAuth tunability

**Proposal**: Make the OAuth failure back-off duration configurable via
`[runtime] oauth_backoff_s = 300`.

**Evaluation**: The existing default (300 s) is correct for all known
deployments. Adding the knob gives users a foot-gun with no benefit. Rejected.
