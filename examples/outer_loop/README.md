# Outer loop — industry RSI, landed on serve

`agent-runner serve` is the **online host**: one coding CLI per round, isolation,
pre-OOM, give-up. It is not the dreamer, the curriculum, or the exam.

2026 papers and loops share a split that matches this repo: a **fixed discovery
agent** plus a **lightweight orchestration layer** that only rewrites policy
between expensive online steps. You own that layer. Copy this directory; do
not put it in core.

Do **not** loop `agent-runner round`. Do **not** auto-restart on 78/75/70.

## Map

| Idea | What they change | What you use here |
|---|---|---|
| [Dream-RSI](https://www.dream-rsi.com/) (Zheng et al., 2026, [arXiv:2609.14858](https://arxiv.org/abs/2609.14858)) **O1 online explore** | Policy guides a frozen coding agent; evaluator scores artifacts | `serve` + `[agent] command`. Child LLM stays the discovery agent. |
| Dream-RSI **O2 history as simulator** | Completed discovery trees are an *exact* replay of realized search — not a learned world model | `log_dir/events-YYYY-MM.jsonl` is that history for *rounds* (linear, not a branch tree). `history_index.py` dumps it. Dreaming over the dump is yours. |
| Dream-RSI **O3 policy improve, then redeploy** | Only exploration-policy *code* changes; models/evaluator stay fixed | Atomic swap of prompt / policy markdown with [between_rounds](../between_rounds/). Next child re-reads. `config_digest` (paths **and bytes**) is the receipt — [digest_watch](../digest_watch/). |
| [Karpathy autoresearch](https://github.com/karpathy/autoresearch) | One file, one metric, fixed budget, keep or discard | One prompt file; `[[goal.checks]]` as the metric; `round_budget_s` as the wall; `[vcs] dirty_action` keep/stash. Human edits the prompt (their `program.md`); the agent edits the repo. |
| [RSIAgent](https://arxiv.org/abs/2609.15364) curriculum / actor / verifier | Curriculum proposes tasks; actor acts; verifier grounds in the environment | Curriculum = this outer process (queue a new prompt). Actor = the round child. Verifier = `[[goal.checks]]` / tests — **not** an LLM judge in core. |
| [RSI-Exam](https://rsi-exam.ai/) hidden split | Visible iterate, score once on sealed data | Keep the hidden grader *off* `[prompt] files`. Outer holds the exam; the child never hashes those bytes. |

Paper claim that matches us: *leave the underlying coding agent unchanged;
orchestration is a thin layer*. Serve is that host. JSONL is append-only
history ([docs/events.md](../../docs/events.md)); `peek --json` is live TOML,
not a replay of which config produced a past round.

## What this repo will not become

- A replay engine, discovery-tree store, or policy-development agent
- Explore / exam *modes* inside `serve`
- An LLM-as-judge or query engine (see [thesis.md](../../docs/thesis.md))

`history_index.py` only **reads** complete JSONL lines. It does not score
policies and does not spawn rounds.

## Minimal lap

1. Run `agent-runner serve` (systemd or a long-lived process).
2. Online: the child expands the repo; events land in JSONL.
3. Between rounds: `python examples/outer_loop/history_index.py --log-dir logs`
   (your dreamer reads that list).
4. Redeploy: write the next policy onto [between_rounds](../between_rounds/)
   `--queue`. Confirm with [digest_watch](../digest_watch/).
5. If JSONL shows `config_broken` / `crash_loop` / `stalled_no_progress` /
   `mem_loop_persistent`, stop. Fix by hand; do not auto-restart.

Cold keys (schedule, host-health, `[agent] command`, check *count*) still
need `agent-runner restart`. Hot keys (prompt bytes, check *cmdlines*) do not.
See [configuration.md](../../docs/configuration.md) § Config reload.
