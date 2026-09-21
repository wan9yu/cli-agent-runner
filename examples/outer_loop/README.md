> English · **[中文](README.zh.md)**

# Outer loop — 2026 harness ideas, landed on serve

`agent-runner serve` is the **online host**: one coding CLI per round, isolation,
pre-OOM, give-up. Layers and constraints:
[examples/README.md](../README.md).

Copy this directory. Do **not** loop `agent-runner round`. Do **not** auto-restart
on 78/75/70.

JSONL is append-only history ([events.md](../../docs/events.md)). `peek --json`
is live TOML, not which config produced a past round.

## Map

| Idea | What they change | What you use here |
|---|---|---|
| [Dream-RSI](https://www.dream-rsi.com/) O1–O3 (Zheng et al., 2026, [arXiv:2609.14858](https://arxiv.org/abs/2609.14858)) | Frozen coding agent; history as *exact* replay; only policy code changes | `serve` + CLI; `history_index.py` dumps realized **rounds** (linear, not a branch tree); swap policy via [between_rounds](../between_rounds/); receipt = `config_digest` ([digest_watch](../digest_watch/)) |
| [Karpathy autoresearch](https://github.com/karpathy/autoresearch) | One file, one metric, fixed budget, keep/discard | One prompt; `[[goal.checks]]`; `round_budget_s`; `[vcs] dirty_action`. Human edits the prompt (`program.md`); the agent edits the repo |
| [RSIAgent](https://arxiv.org/abs/2609.15364) curriculum / actor / verifier | Curriculum proposes; actor acts; verifier grounds | Curriculum = outer queue. Actor = round child. Verifier = `[[goal.checks]]` — **not** an LLM judge in core |
| [RSI-Exam](https://rsi-exam.ai/) hidden split | Visible iterate; score once on sealed data | Keep the hidden grader **off** `[prompt] files`. Outer holds the exam |
| [Ralph Wiggum / harness engineering](https://openai.com/index/harness-engineering/) | Same prompt until mechanical reviewers pass; repo is the system of record | Unattended `serve`; `AGENTS.md` / prompt as hot files; linters as `[[goal.checks]]`; encode taste in tests, not in core |
| [ACE](https://arxiv.org/abs/2510.04618) (ICLR 2026) | Playbook of *itemized* bullets; Generator / Reflector / Curator; **delta** updates, not full rewrites (avoid context collapse) | Generator = child. Reflector+Curator = outer. Playbook = extra `[prompt] files` entries (not `[goal] ledger` — that truncates at 8192). Append bullets; do not rewrite the whole prompt |
| [Codex inner harness](https://openai.com/index/unrolling-the-codex-agent-loop/) | Compact, cache prefix, tool loop *inside* one thread | Leave it in Codex. Outer wall is `round_budget_s`. Do not add compact to serve |
| [OpenHands workspace](https://docs.openhands.dev/sdk) | Isolated workspace per agent | `[agent] exec_prefix` / [container-pi](../../docs/recipes/container-pi.md). Isolation of the *supervisor* is systemd + cgroup, not a second SDK |
| [Letta / MemGPT](https://github.com/letta-ai/letta-code) memory OS | Core / archival / recall; agent rewrites its own memory | Not in core (kill path stays blind). Durable notes = extra prompt files. Ledger is a treadmill cap, not RAM |
| Subagent fan-out (Codex workers, etc.) | Parallel children, own context | **One round, one process.** Parallel = more `serve` units or the *inner* CLI. Do not teach serve to spawn workers |

## Recipes you can run without new core

**Ralph.** Leave serve up. Same prompt until `[[goal.checks]]` go green. When they
stay red, queue a tighter prompt with between_rounds — still one serve.

**ACE playbook.** `prompts/playbook.md` listed in `[prompt] files`. Outer appends
a bullet after `goal_check` fails (delta, not a full rewrite). Confirm the next
`config_digest` moved. Generator (the child) only *reads* the playbook.

**Dream lap.** `history_index.py --log-dir logs` → your policy rewriter →
between_rounds `--queue`. Stop on give-up kinds; do not auto-restart.

**Autoresearch.** One editable file in the repo, one check command, `max_rounds`
or `stop_file` when you want the lineage to end.

Cold keys (schedule, host-health, `[agent] command`, check *count*) still need
`agent-runner restart`. Hot keys (prompt bytes, check *cmdlines*) do not.
See [configuration.md](../../docs/configuration.md) § Config reload.

## What this repo will not become

- Inner tool loop, compact, or prompt cache
- Replay engine / discovery-tree store / policy-development agent
- Explore / exam *modes* inside `serve`
- LLM-as-judge or query engine ([thesis.md](../../docs/thesis.md))
- Memory OS or automatic playbook curator

`history_index.py` only **reads** complete JSONL lines.
