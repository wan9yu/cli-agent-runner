> 中文 · **[English](README.md)**

# 外环 — 跑在 serve 旁边

每一轮仍由 serve 启动，日志也由 serve 写。这一页是地图，不是任务清单。格子是
`—` 的，还没有配方。只拷一份已链接的配方，并且只按它的步骤做。分层见
[examples/README.zh.md](../README.zh.md)。

用 `agent-runner --config <仓库旁边的>/agent-runner.toml serve` 起一次。
不要循环 `agent-runner round`。78、75、70 退出就停着。

**关键点**是这个思路要解决什么问题，特点是什么。
**一轮，以及轮间**写的是这一轮什么样，下一轮之前改什么。轮间才是协调。

## 对照

| 思路 | 关键点 | 一轮，以及轮间 | 现成例子 |
| --- | --- | --- | --- |
| [Dream-RSI](https://www.dream-rsi.com/) O1–O3（Zheng et al., 2026, [arXiv:2609.14858](https://arxiv.org/abs/2609.14858)） | 编码的人冻住，只改策略。下一轮重放已经发生的历史。要解决的是从历史里学，又不把历史做成搜索树。 | 一轮：一个冻结的孩子。轮间：把已发生的 round 列成一条线，只换策略，下一轮重放这条线。关键：不是分叉。回执是 `config_digest`。 | — |
| [Karpathy autoresearch](https://github.com/karpathy/autoresearch) | 一个文件、一个数、固定预算。数变好才留下这次修改。要解决的是改个不停、从不做决定。 | 第 1 轮：孩子改那一个文件，检查打印指标。轮间：差于启动就恢复仓库外快照，并换一次 `files[0]`。第 2 轮：孩子读新 prompt。轮间：好于启动就把文件拷进快照，不再换。第 3 轮：不写。关键：留下或丢掉发生在轮间。预算是 `max_rounds = 3`。 | [karpathy/](karpathy/README.zh.md) |
| [RSIAgent](https://arxiv.org/abs/2609.15364) 课程 / 演员 / 阅卷 | 下一题从孩子外面来。阅卷是程序，不是另一个模型。要解决的是孩子既出题又给自己打分。 | 第 1 轮：课程只有任务 A，孩子执行，检查是 `[[goal.checks]]`。轮间：队列追加任务 B 一次。第 2 轮：孩子重读并执行，检查命令不变。第 2 轮之后不再写。关键：轮间改的是任务，阅卷仍是检查，不是 LLM。 | [rsi_agent/](rsi_agent/README.zh.md) |
| [RSI-Exam](https://rsi-exam.ai/) 隐藏集 | 在孩子看得到的数据上练。在它看不到的数据上只打一次分。要解决的是分数漏进下一轮 prompt、被针对。 | 每一轮：孩子在可见集上迭代，可见检查照跑。轮间：不要把隐藏阅卷拷进下一轮 prompt。最后一轮之后：外层对密封数据只打一次分。关键：每轮迭代，只打一次分，而且不进 `[prompt] files`。 | [rsi_exam/](rsi_exam/README.zh.md) |
| [Ralph / harness engineering](https://openai.com/index/harness-engineering/) | 同一份 prompt，直到机械检查通过。记录在仓库里，不在聊天里。要解决的是模型说做完了，测试还是红的。 | 每一轮：改一个源文件，跑一次 unittest，失败也结束。下一轮仍是同一份 prompt。轮间：只有之后的检查通过，才创建 `stop_file`。红的不创建。关键：直到变绿的是 serve，不是孩子自己重复。 | [ralph/](ralph/README.zh.md) |
| [循环工程](https://aipatternbook.com/loop-engineering) | 发现、隔离、验证、留下、停止是分开的。做完以检查为准，不以孩子最后一句为准。要解决的是把「DONE」当成结果。 | 第 1 轮：孩子可以在检查仍失败时打印 `DONE`。轮间：prompt 不动。同一轮 serve 进入第 2 轮。关键：检查不是孩子说的做完。把检查跑过是 [Ralph](ralph/README.zh.md) 那一行。 | [loop_engineering/](loop_engineering/README.zh.md) |
| [ACE](https://arxiv.org/abs/2510.04618)（ICLR 2026） | 短子弹的 playbook，只追加，不整本重写。要解决的是整份 prompt 被换掉后，旧教训消失。 | 第 1 轮：孩子改源文件，检查照跑。轮间：从 `goal_check` 字段追加一条子弹，旧条目仍在。这不是 ledger。第 2 轮：孩子重读这一条新子弹。第 2 轮之后不再追加。关键：轮间是追加，不是重写 prompt。 | [ace/](ace/README.zh.md) |
| [Codex 内环](https://openai.com/index/unrolling-the-codex-agent-loop/) | compact 和 tool 循环属于一轮里面。要解决的是把内环的记忆技巧搬进外层监督者。 | 一轮就是内环的 tool 循环，compact 也在这一轮里。轮间只有墙钟 `round_budget_s`。不要在轮间加 compact。 | — |
| [OpenHands workspace](https://docs.openhands.dev/sdk) | 每个 agent 一块自己的工作区。要解决的是一个 agent 踩另一个的文件。监督者的隔离是机器，不是再做一个 SDK。 | 每一轮一块工作区，走 `[agent] exec_prefix` / [container-pi](../../docs/recipes/container-pi.md)。轮间不再做一个监督者。supervisor 的隔离仍是 systemd + cgroup。 | — |
| [Letta / MemGPT](https://github.com/letta-ai/letta-code) 记忆 OS | 孩子可以改自己的笔记，但笔记是文件，不是藏起来的内存。要解决的是停进程的路径看不见的记忆。 | 每一轮可以改额外的 prompt 文件。轮间重读这些文件。ledger 会截断，它不是内存。停进程的路径不读这些笔记。 | — |
| Subagent 扇出（Codex workers 等） | 并行的孩子，各自一份上下文。要解决的是想要很多 worker。这里一轮仍是一个进程。 | 一轮一进程。轮间不要 spawn worker。并行是另一个 `serve`，或内环 CLI。 | — |
| [时间尺度、tick、级联智能](https://arxiv.org/abs/2609.19519) | 审查失败之后才换更强的模型。一个 tick 是驱动者的一步。要解决的是每一步都叫贵的模型，或一轮中途换模型。 | 每一轮用启动时就有的一个阶段命令。轮间：审查失败可以改选另一个阶段。不要在轮间改 `[agent] command`。两个阶段若共用 `pi` 二进制，限流键也共用。 | — |
| [Harness-of-Harness](https://arxiv.org/abs/2609.01481) | 孩子看得到的检查，不是密封数据上的分数。和 RSI-Exam 同一个问题：考卷不能漏进练习。 | 同 RSI-Exam 那一行。每一轮跑可见检查。轮间：密封评分不进 prompt。 | — |
| [LongHorizon-Harness](https://arxiv.org/abs/2608.01964) Manage-Execute-Audit | 任务状态在孩子外面。下一步是环境给出的固定词，不是自由散文。要解决的是长任务被「我做完了」带着走。 | 红的一轮：轮间写入 `retry`，只改 instruction 行。第一次变绿：把检查的 value 追加到 `state.txt` 一次。后再绿不再追加。不创建 `stop_file`。关键：下一步来自检查，不来自孩子的散文。 | [longhorizon/](longhorizon/README.zh.md) |
| 串行丢弃工作区 | 一次尝试一个目录。红的尝试不合并。要解决的是失败的一次弄脏本来要留的那棵树。 | 一次 serve，一个尝试目录，启动前定死。红的一轮之后，轮间不把那个目录合并进保留树。`stash` 不是丢掉。serve 不为下一轮再开 worktree。 | [serial_discard/](serial_discard/README.zh.md) |
| 重复失败就停 | 同一种失败再出现就停，不是模型说卡住了才停。要解决的是红的结果没变还在空转。 | 第 1 轮红：记下 `satisfied` 和 `value`，不停。下一轮仍红：指纹没变，就在下一轮开始前的轮间创建 `stop_file`。value 变了不停。没有 `value` 时，指纹只是布尔。轮间的 advisory 不停。 | [repeat_fail/](repeat_fail/README.zh.md) |
| [花费上限](https://aiarch.dev/patterns/bounded-agentic-loop) | token 或钱可以单独让循环停，步数和墙钟还有剩也停。要解决的是轮数很少、账单很大。 | 每一轮可能发出 `cost_usd`。轮间把这些事件加总，到上限就创建 `stop_file`。没有 usage 事件，不等于账单是零。`max_rounds` 和 `round_budget_s` 不是这个加总。 | [spend_ceiling/](spend_ceiling/README.zh.md) |
| 工具白名单 | 预算挡不住一次仍在预算内的破坏性调用。目标里写一句不是授权。要解决的是 prompt 写了「不要」，工具却还在。 | 第 1 轮之前，在孩子 CLI 里拿掉那个工具。轮间写「不要……」不是门。停进程的路径不读 goal 笔记。 | [tool_allow/](tool_allow/README.zh.md) |
| 租约协调的 worker | 用心跳认领任务，死掉的 worker 可以被换掉。要解决的是死进程一直占着任务。这一行不加心跳。 | 轮间没有心跳。一轮一进程。死掉的 worker 不会在轮间被换掉。 | — |
| 对尝试做树搜索 | 留一棵树，展开有希望的分支。要解决的是回头再试旧的分支。这里的历史是一条线，不是树。 | 轮间看到的是已经跑过的一条线。没有分叉可以选给下一轮。 | — |
| [ralphctl](https://github.com/lukas-grigis/ralphctl) | 一个宿主去驱动很多 CLI。要解决的是包住 claude、codex 等等。不要在 serve 已经在跑的树上再放一个宿主。 | 不要把那个宿主插进轮间。循环是 Ralph 那一行。 | — |
| [snarktank/ralph](https://github.com/snarktank/ralph) | Amp 或 Claude Code 外面的 shell 循环。和 Ralph 同一个问题，换了一个宿主。不要两个一起跑。 | 不要把那个 shell 循环插进轮间。循环是 Ralph 那一行。 | — |
| [ralphex](https://github.com/umputun/ralphex) | 计划文件加评审，给另一个 CLI 用。要解决的是计划放在哪。这里的计划文件只是外层队列。 | 不要把它插进轮间。这里的计划文件是 RSIAgent 那一行的外层队列。 | — |
| [longrun-prd-runner](https://github.com/kyunbit/longrun-prd-runner) | 一边跑一边选模型的控制面。要解决的是运行中换模型。这里这个选择在第 1 轮之前就定了。 | 不要把它插进轮间。模型选择是级联那一行：两个命令在第 1 轮之前就有。 | — |
| [madhavajay/ralph](https://github.com/madhavajay/ralph) | 外层模型看着内层模型，装的还是另一个 pi 包。要解决的是用另一个模型来监督。那就是 LLM 法官，这一行不要。 | 不要把它和 serve 并排安装。轮间再放一个外层模型，就是 LLM 法官。检查仍是 `[[goal.checks]]`。 | — |

冷键（日程、host-health、`[agent] command`、check **条数**、`stop_file`
的路径）仍要 `agent-runner restart`。热键（prompt 字节、check **命令行**）
不用。创建启动时配好的 `stop_file`，会做完当前轮，发出
`stop_file_detected`，退出 0。删掉文件不会再把 serve 拉起来。见
[configuration.md](../../docs/configuration.md) § Config reload。

## 怎么读一份配方

按配方的编号步骤做。`--config` 打开的是仓库旁边的 `agent-runner.toml`。
每份配方都从 `agent-runner.toml.example` 拷出这份文件。

`logs/serve.pid` 是 JSON，`{"pid": <int>, "create_time": <number>}`。
delay 脚本读这个对象，并确认那个进程还在。文件里只有数字是旧格式。
serve 退出时会删掉这个文件。

第 1 轮 `config_changed: true` 表示 serve 第一次记下 digest。这不是有人改了
prompt。比后面各轮 `round_start` 的 `config_digest`，或自己对 prompt 文件
做哈希。

孩子的成绩单是 `logs/rounds/R<n>-*.log`。`logs/round-<n>.log` 是短的操作
日志。pi 用 `--mode json` 时，助手文本是 `agent_end` 且 `role == assistant`
的消息。同一条记录里的 user 文本不算。

退出码 0 只表示 serve 停了。打开 `logs/events-YYYY-MM.jsonl`，看停的原因是
`stop_file_detected` 还是 `max_rounds_reached`。

配方若要在两轮之间改仓库里的文件，`dirty_action` 是 `ignore`。默认的
`stash` 会在下一轮之前把那次修改清掉。

## 这个仓库不会变成

- 内环 tool loop、compact 或 prompt cache
- replay 引擎 / discovery-tree 存储 / 策略开发 agent
- `serve` 里的 explore / exam **模式**
- LLM 法官或查询引擎（[thesis.md](../../docs/thesis.md)）
- 记忆 OS 或自动 playbook 策展器

`history_index.py` 只**读**完整的 JSONL 行。
