> English · **[中文](README.zh.md)**

# Outer loop — beside serve

Serve starts each round and writes the log. This page is a map, not a task
list. `—` has no recipe. Copy one linked recipe and follow only its steps.
Layers: [examples/README.md](../README.md).

One `agent-runner --config <beside-repo>/agent-runner.toml serve`. Do not
loop `agent-runner round`. Exit 78, 75, or 70 stays stopped.

**Key point** is the problem this idea is for, and what is special about it.
**Round, then the gap** is what one round does, then what changes before the
next. The gap is the coordination.

## Map

| Idea | Key point | Round, then the gap | Example |
| --- | --- | --- | --- |
| [Dream-RSI](https://www.dream-rsi.com/) O1–O3 (Zheng et al., 2026, [arXiv:2609.14858](https://arxiv.org/abs/2609.14858)) | The coder stays frozen. Only the policy changes, and the next round replays what already happened. Solves learning from history without turning it into a search tree. | Round: one frozen child. Gap: dump the line of realized rounds, swap only the policy, next round replays that line. Key: not a branch. Receipt is `config_digest`. | — |
| [Karpathy autoresearch](https://github.com/karpathy/autoresearch) | One file, one number, a fixed budget. Keep the edit only if the number improved. Solves tinkering that never decides. | Round 1: child edits the one file; the check prints the metric. Gap: if worse than boot, restore the outside snapshot and swap `files[0]` once. Round 2: child reads the new prompt. Gap: if better than boot, copy the file onto the snapshot; do not swap again. Round 3: no write. Key: keep or discard is the gap. Budget is `max_rounds = 3`. | [karpathy/](karpathy/) |
| [RSIAgent](https://arxiv.org/abs/2609.15364) curriculum / actor / verifier | The next task comes from outside the child. A program grades the result, not another model. Solves the child both setting the task and marking its own homework. | Round 1: course is task A only; child acts; check is `[[goal.checks]]`. Gap: queue appends task B once. Round 2: child re-reads and acts; same check. After round 2: no second write. Key: the gap changes the task; the verifier stays the check, not an LLM. | [rsi_agent/](rsi_agent/) |
| [RSI-Exam](https://rsi-exam.ai/) hidden split | Practice on data the child can see. Score once on data it cannot see. Solves a grade that leaks into the next prompt and gets gamed. | Each round: child iterates on the visible set; the visible check runs. Gap: do not copy the hidden grader into the next prompt. After the last round: outer scores the sealed data once. Key: iterate every round, score once, off `[prompt] files`. | [rsi_exam/](rsi_exam/) |
| [Ralph Wiggum / harness engineering](https://openai.com/index/harness-engineering/) | Same prompt until a mechanical check passes. The repo is the record, not the chat. Solves "the model said done" while the tests are still red. | Each round: one source edit, one unittest, then stop even if red. Same prompt next round. Gap: create `stop_file` only after a later check passes. A red check does not. Key: serve is the until-loop; the child is not told to repeat until green. | [ralph/](ralph/) |
| [Loop engineering](https://aipatternbook.com/loop-engineering) | Discover, isolate, verify, persist, and stop stay separate. Done is the check, not the child's last sentence. Solves treating "DONE" as the result. | Round 1: child may print `DONE` while the check is still false. Gap: prompt stays. Round 2 starts in the same serve. Key: the check is not the child's done. Passing it is the [Ralph](ralph/) row. | [loop_engineering/](loop_engineering/) |
| [ACE](https://arxiv.org/abs/2510.04618) (ICLR 2026) | A playbook of short bullets that grows by append, not by rewriting the whole note. Solves old lessons disappearing when the prompt is replaced. | Round 1: child edits the source; the check runs. Gap: append one bullet from `goal_check` fields; the old entry stays. Not the ledger. Round 2: child re-reads that one new bullet. After round 2: no second bullet. Key: the gap appends; it does not rewrite the prompt. | [ace/](ace/) |
| [Codex inner harness](https://openai.com/index/unrolling-the-codex-agent-loop/) | Compact and the tool loop belong inside one round. Solves pushing the inner loop's memory tricks into the outer supervisor. | One round is the inner tool loop, including compact. Gap: only the wall, `round_budget_s`. Do not add compact between rounds. | — |
| [OpenHands workspace](https://docs.openhands.dev/sdk) | Each agent gets its own workspace. Solves one agent trampling another's files. The supervisor's isolation is the machine, not another SDK. | Each round: one workspace, via `[agent] exec_prefix` / [container-pi](../../docs/recipes/container-pi.md). Gap: no second supervisor. Supervisor isolation stays systemd + cgroup. | — |
| [Letta / MemGPT](https://github.com/letta-ai/letta-code) memory OS | The agent can rewrite its own notes, but those notes are files, not hidden memory. Solves a memory system the stop path cannot see. | Each round may rewrite extra prompt files. Gap: those files are re-read. The ledger truncates; it is not RAM. The kill path does not read the notes. | — |
| Subagent fan-out (Codex workers, etc.) | Parallel children, each with its own context. Solves wanting many workers. One round here stays one process. | One round, one process. Gap: do not spawn workers. Parallel is another `serve` or the inner CLI. | — |
| [Levels, ticks, cascaded intelligence](https://arxiv.org/abs/2609.19519) | Call a stronger model only after review fails. One tick is one driver step. Solves calling the expensive model every step, or switching models mid-round. | Each round uses one phase command that existed at boot. Gap: a failed review can select the other phase. Do not change `[agent] command` between rounds. Two phases that share the `pi` binary share one throttle key. | — |
| [Harness-of-Harness](https://arxiv.org/abs/2609.01481) | Checks the child can see are not the score on sealed data. Same problem as RSI-Exam: the exam must not leak into practice. | The RSI-Exam row. Visible check each round. Gap: sealed score stays off the prompt. | — |
| [LongHorizon-Harness](https://arxiv.org/abs/2608.01964) Manage-Execute-Audit | Task state lives outside the child. The next step is a fixed word from the environment, not free prose. Solves a long task steered by "I'm done". | Red round: in the gap, write `retry` and change only the instruction line. First green: append the check value to `state.txt` once. A later green does not append. No `stop_file`. Key: the next step comes from the check, not the child's prose. | [longhorizon/](longhorizon/) |
| Serial discard workspace | One attempt lives in one directory. A red attempt does not merge. Solves a failed try contaminating the tree you meant to keep. | One serve, one attempt directory, set before boot. After a red round, the gap does not merge that directory into the keep tree. `stash` is not the discard. Serve does not open a worktree for the next round. | [serial_discard/](serial_discard/) |
| Repeated-failure stop | Stop when the same failure comes back, not when the model says it is stuck. Solves spinning on an unchanged red check. | Round 1 red: record `satisfied` and `value`; do not stop. Next red round: if that fingerprint is unchanged, create `stop_file` in the gap before the following round. A different value does not stop. No `value`: fingerprint is only the bool. An advisory in the gap does not stop. | [repeat_fail/](repeat_fail/) |
| [Spend ceiling](https://aiarch.dev/patterns/bounded-agentic-loop) | Tokens or money can stop the loop even when steps and wall clock remain. Solves a loop that is short in rounds and large in bill. | Each round may emit `cost_usd`. Gap: sum those events and create `stop_file` at the ceiling. A missing usage event is not zero. `max_rounds` and `round_budget_s` are not that sum. | [spend_ceiling/](spend_ceiling/) |
| Tool allow-list | A budget does not stop one destructive call that is still inside the budget. A sentence in the goal is not permission. Solves "the prompt said don't" while the tool is still there. | Before round 1, remove the tool in the child CLI. A prompt line in the gap that says do not is not a gate. The kill path does not read goal notes. | [tool_allow/](tool_allow/) |
| Lease-coordinated workers | A heartbeat claim so a dead worker can be replaced. Solves a dead worker holding a task forever. This row does not add that heartbeat. | No heartbeat in the gap. One round, one process. A dead worker is not replaced between rounds. | — |
| Tree search over attempts | Keep a tree and expand a promising branch. Solves revisiting old attempts. Here history is a line, not a tree. | The gap sees a line of rounds already run. There is no branch to choose for the next round. | — |
| [ralphctl](https://github.com/lukas-grigis/ralphctl) | A host that drives many CLIs. Solves wrapping claude, codex, and the rest. Do not put a second host on a tree serve is already driving. | Do not insert that host in the gap. The loop is the Ralph row. | — |
| [snarktank/ralph](https://github.com/snarktank/ralph) | A shell loop around Amp or Claude Code. Same problem as Ralph, a different host. Do not run both. | Do not insert that shell loop in the gap. The loop is the Ralph row. | — |
| [ralphex](https://github.com/umputun/ralphex) | A plan file plus reviewers for another CLI. Solves where the plan lives. Here a plan file is just the outer queue. | Do not insert it in the gap. A plan file here is the outer queue in the RSIAgent row. | — |
| [longrun-prd-runner](https://github.com/kyunbit/longrun-prd-runner) | A control plane that picks the model as it goes. Solves runtime model choice. Here that choice is fixed before round 1. | Do not insert it in the gap. Model choice is the cascaded row: both commands exist before round 1. | — |
| [madhavajay/ralph](https://github.com/madhavajay/ralph) | An outer model watches the inner one, and it installs a different pi package. Solves supervision by another model. That is an LLM judge, which this row rejects. | Do not install it beside serve. An outer model in the gap would be an LLM judge. Checks stay `[[goal.checks]]`. | — |

Cold keys (schedule, host-health, `[agent] command`, check *count*, the
`stop_file` path) still need `agent-runner restart`. Hot keys (prompt bytes,
check *cmdlines*) do not. Creating the boot-configured `stop_file` finishes
the current round, emits `stop_file_detected`, and exits 0. Deleting the
file does not start serve again. See
[configuration.md](../../docs/configuration.md) § Config reload.

## Reading a recipe

Follow the recipe's numbered steps. The file you pass to `--config` is
`agent-runner.toml` beside the git repo. Each recipe starts from
`agent-runner.toml.example`.

`logs/serve.pid` is JSON, `{"pid": <int>, "create_time": <number>}`. A delay
script reads that object and checks the process is alive. A file of only
digits is the old form. Serve deletes the file when it exits.

Round 1 `config_changed: true` means serve recorded the digest for the first
time. It does not mean someone edited the prompt. Compare `config_digest`
on later `round_start` lines, or hash the prompt file.

The child's transcript is `logs/rounds/R<n>-*.log`. `logs/round-<n>.log` is
the short operator log. For pi `--mode json`, assistant text is an
`agent_end` message with `role == assistant`. User text in that same record
does not count.

Exit code 0 means serve stopped. Open `logs/events-YYYY-MM.jsonl` to see
whether that was `stop_file_detected` or `max_rounds_reached`.

If the recipe edits a file inside the repo between rounds, `dirty_action`
is `ignore`. The default `stash` would remove that edit before the next
round.

## What this repo will not become

- Inner tool loop, compact, or prompt cache
- Replay engine / discovery-tree store / policy-development agent
- Explore / exam *modes* inside `serve`
- LLM-as-judge or query engine ([thesis.md](../../docs/thesis.md))
- Memory OS or automatic playbook curator

`history_index.py` only **reads** complete JSONL lines.
