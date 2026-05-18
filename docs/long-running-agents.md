# Long-running agent lineages

This document covers systemic hazards when agent-runner supervises a single
agent (or rotating agents) across hundreds of rounds, and the framework
primitives available to mitigate them.

## Three systemic hazards

These are **structural outcomes** of long-lineage configurations, not model
bugs. Any LLM in the same configuration will eventually exhibit them.

### Confabulation

After enough rounds, the agent's context window is dominated by **prior agent
output** (chat-room, prior memos, peer narrative). The implicit reward
becomes "extend / refine prior content" — and the easiest extension is to
**invent observations consistent with existing narrative**. Numbers
(file LOC, commit SHAs, ticket IDs) without grounding evidence are the
typical fabrications.

Symptoms: claims like "audit.py 1323→1332" or "commit 47f9b88a" with no
preceding shell command that would have produced those numbers.

### Momentum drift

Round N+1 implicitly extends round N's framing. Memo lengths balloon, content
drifts from the original task ("what does this card need") to meta-tasks
("how should we sequence peer sign-offs", "what micro-pattern should we
codify next"). Signal density decreases as output volume grows.

### Lineage rot

Original task spec loses anchor weight as context is increasingly dominated
by accumulated agent output. The agent reasons about its own past memos
rather than the original problem.

## Five primitives agent-runner provides

agent-runner does NOT detect or prevent these hazards directly (see
boundary section below). Instead, it provides infrastructure that
plugin/prompt-layer code can consume.

### 1. `max_rounds` (0.1.21) — bound the lineage

```toml
[runtime]
max_rounds = 30
```

Or via CLI: `agent-runner serve --max-rounds 30`. Hard cap on round count.
The simplest mitigation: confabulation risk grows superlinearly with
lineage length; bounding it at 30-50 rounds eliminates most of the
practical exposure.

### 2. `stop_file` (0.1.21) — operator graceful pause

```toml
[runtime]
stop_file = "~/agent-runner-stop"
```

`touch ~/agent-runner-stop` between rounds → supervisor finishes current
round → emits `stop_file_detected` → exits cleanly. Lets operators halt
runaway lineages without killing in-flight work.

### 3. `substrate_fingerprint_paths` (0.1.22) — raw data for detection

```toml
[runtime]
substrate_fingerprint_paths = ["*.py", "specs/*.md", "schedule/*.toml"]
```

Each round emits `round_substrate_before` and `round_substrate_after` events
with:
- `git_head`: SHA of `work_dir` HEAD (always captured if `.git` exists)
- `paths_hash`: SHA-256 of matching files' contents (null when no paths
  configured)

**Use this as input to a confabulation detector** built as a post_round_hook
plugin or external script. Example detection pattern:

```python
# Pseudocode for a confabulation detector (not shipped — user implements)
def detect(events):
    pairs = pair_substrate_events(events)
    for before, after, memo in pairs:
        if before.git_head == after.git_head and before.paths_hash == after.paths_hash:
            # Substrate unchanged this round
            if memo_claims_state_change(memo):
                yield "CONFABULATION: memo claims mutations but substrate unchanged"
```

The framework provides the `before/after` data; the detection logic is
project-specific (depends on memo format, claim conventions, etc.).

### 4. `fresh_eyes_every_n` (0.1.22) — periodic context refresh signal

```toml
[runtime]
fresh_eyes_every_n = 50
```

Every 50th round, `AGENT_RUNNER_FRESH_EYES=1` is injected into the round
subprocess env. The round also emits `fresh_eyes_round_triggered` event.

**The framework does not construct fresh-eyes prompt content** — it only
provides the signal. A typical consumer:

```python
# Pre-round hook that swaps prompt context on fresh-eyes rounds
def before_round(ctx):
    if os.environ.get("AGENT_RUNNER_FRESH_EYES") == "1":
        # Swap the prompt file to a "fresh-eyes" variant
        # that contains ONLY original task spec + current substrate state,
        # excluding chat-room / accumulated narrative
        cfg.prompt.file = "prompts/fresh-eyes.md"
```

Or in the agent's own prompt template (using your preferred templating
tool — agent-runner does not preprocess prompt files itself, but
operators commonly run a Jinja2 / similar templating pass beforehand):

```jinja2
{% if env.AGENT_RUNNER_FRESH_EYES == "1" %}
You are doing a fresh-eyes round. Ignore prior memos and chat-room
context. Re-evaluate progress against the ORIGINAL task spec only.
Re-run `wc -l`, `git log`, etc. before making any numeric claims.
{% endif %}
```

### 5. `agent_usage_recorded` (0.1.24+) — per-round token + cost data

Each round (claude or gemini) emits an `agent_usage_recorded` event with
token breakdown + cost (where the underlying CLI exposes it).

```toml
# No config needed — events emit automatically when a built-in plugin
# (claude_error_detector / gemini_error_detector) is registered.
# To suppress: [plugins] disable = ["claude_error_detector", "gemini_error_detector"]
```

Use as input to a cost-tracking detector or external billing reconciler.
See `docs/migrations/0.1.28.md` for the current 12-field payload schema
(includes `cache_creation_tokens`, `tool_call_count`, `phase`, `success`)
plus a consumer dispatcher sketch. Aggregation (rollups, budget warnings)
is the consumer's responsibility — agent-runner emits raw per-round
events; downstream tooling computes daily/hourly/per-phase summaries.

## What agent-runner does NOT do (intentional boundary)

agent-runner is a **process supervisor**. It runs subprocesses, captures
their I/O to events.jsonl and round logs, and provides extension hooks.
It does **not**:

- **Parse agent output content.** Memos / chat-room / narrative files
  are written by the agent; the framework does not read them semantically.
- **Maintain a "claim" or "attestation" abstraction.** There is no
  built-in concept of what constitutes a verifiable claim. Agent CLIs
  (claude, aider, gemini) don't emit structured claim events; the
  framework grepping their text output for claims would be a layering
  violation.
- **Auto-detect confabulation.** The framework provides substrate
  fingerprint events; semantic detection ("did the memo claim X when
  substrate says Y?") is the consumer's responsibility.
- **Construct fresh-eyes prompt content.** The framework provides the
  trigger signal (env var + event); the operator's prompts decide what
  fresh-eyes semantically means for their project.

**Rationale**: the right level for semantic detection is the consumer's
prompt-engineering layer with project-specific conventions. A generic
"claim parser" in the framework would either be too rigid (assume one
memo format) or too vague (false positives). Operators ship their own
detectors as post_round_hook plugins that consume the framework's raw
events.

## Recommended prompt-engineering practices

These are **operator decisions, not framework enforcement**. Adopt the ones
that fit your workflow.

### Pair numeric/identifier claims with attestation blocks

In your agent's prompt template (e.g. `prompts/main.md`):

> Every numeric or identifier claim in your memo must be paired with the
> exact shell command output that produced it. List all such evidence in
> a `## Attestation` section at the top of the memo. If you cannot re-run
> the command in the current substrate, do not make the claim. Absence
> of evidence = procedurally invalid, not just unsupported.

A post_round_hook can later parse the memo for unattested claims and
flag them.

### Default to short bounded runs while building detectors

For stress tests, set `max_rounds ≤ 30` until you've built a confabulation
detector that consumes the substrate fingerprint events. This caps
the exposure window.

### Use fresh-eyes rounds to force re-attestation

In your fresh-eyes prompt variant, instruct the agent to re-verify at
least one numeric claim from the prior round's memo. This catches drift
across role rotations.

### Cap shared-account stress tests

The 5h rolling rate limit is per OAuth account (see `docs/runbook.md`).
Long lineages that share an account with production scheduling will
trigger throttling. agent-runner auto-detects and backs off (0.1.20+
for 5h quota; 0.1.23+ also covers 5xx server outages, 429 model overloads,
and 408 timeouts via the unified `transient_error_*` event family), but
the underlying problem is unbounded lineage on a shared resource.

**Transient-error events (0.1.23+)**: what was the `rate_limit_rejected`
event family is now `transient_error_detected` with a `classification`
field (`rate_limit_account`, `rate_limit_model`, `api_transient_5xx`,
`api_timeout`). The same back-off mechanism covers all 4 classifications.
The legacy `rate_limit_rejected` aliases were removed in 0.1.29 — subscribe
to `transient_error_detected` (filter by `classification == "rate_limit_account"`
if you only want 5h-quota events). See `docs/migrations/0.1.27.md` for the
consumer dispatch recipe and `docs/migrations/0.1.29.md` for alias-removal
migration recipes.

## Writing post_round_hook plugins

### Reading agent stdout from a plugin

Use `ctx.agent_log_path` (added in 0.1.25). This points to the agent's
actual JSONL stdout for the current round
(`log_dir/rounds/R<N>-<timestamp>.log`). Do NOT compute the path from
`ctx.log_dir + round_num` — historical naming conventions in that directory
are subject to change.

```python
def after_round(self, ctx: HookContext, result: Any) -> None:
    log_path = ctx.agent_log_path
    if log_path is None or not log_path.exists():
        return
    # parse log_path for agent JSONL output ...
```

The `None` guard is required: the field defaults to `None` for backward
compatibility with manually-constructed HookContext instances in unit tests.
In production, the supervisor always populates it.

## Related primitives

- `docs/runbook.md` § Rate limits — 5h OAuth account quota + transient error handling
- `docs/runbook.md` § Bounded runs — max_rounds + stop_file workflow
- `docs/migrations/0.1.22.md` — substrate fingerprint + fresh-eyes (0.1.22)
- `docs/migrations/0.1.23.md` — unified transient-error classifier (0.1.23)
- `docs/migrations/0.1.24.md` — usage events + gemini plugin (0.1.24)
- `docs/migrations/0.1.25.md` — plugin path hotfix + HookContext.agent_log_path (0.1.25)
