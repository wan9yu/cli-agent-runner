> 中文 · **[English](README.md)**

[![CI](https://github.com/wan9yu/agent-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/wan9yu/agent-runner/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/cli-agent-runner.svg)](https://pypi.org/project/cli-agent-runner/) [![Python](https://img.shields.io/pypi/pyversions/cli-agent-runner.svg)](https://pypi.org/project/cli-agent-runner/) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![codecov](https://codecov.io/gh/wan9yu/agent-runner/branch/main/graph/badge.svg)](https://codecov.io/gh/wan9yu/agent-runner) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

# agent-runner

把任意 CLI agent（Claude Code、自研 agent、任何长跑命令）包装成可被
systemd / launchd 拉起、能被远程观测的服务。**每轮跑完进程退出**，外层
supervisor 重启 —— 这是核心模式。中间穿插 11 条防御，避开 production 上
最容易翻车的几条路：

- 轮卡死、Tool 调用空转 → 硬墙 timeout
- agent 留下脏文件没 commit → SHA 锁定的 orphan stash
- OAuth 失效却继续重试烧 quota → 自动停服
- 磁盘写满还在 emit 事件 → 95% 时自动停服
- 进程没死透留下僵尸 → 进程组隔离 + SIGTERM reaper
- 同时跑两个 supervisor → flock 防并发

## 三层架构

```
┌──────────────────────────────────────────┐
│ Layer 3：Witness（monitor）              │  9 个检测器 + 自动停服
├──────────────────────────────────────────┤
│ Layer 2：Loop（serve，~60 LOC 薄壳）     │  捕获信号，循环拉起 round
├──────────────────────────────────────────┤
│ Layer 1：Round（round）                  │  跑一次 agent，跑完即退
└──────────────────────────────────────────┘
```

每层都能独立运行，上层都是可选的：

- 只用 `agent-runner round` 即可手动跑一轮，调试方便
- `serve` 包住 round 形成长服务
- `monitor` 可以从本机也可以远程通过 ssh 观测一台 pi 上的 supervisor

## 上手

```bash
git clone https://github.com/wan9yu/agent-runner.git
cd agent-runner
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 在你的项目目录里：
agent-runner init                 # 生成 agent-runner.toml + prompts/main.md
$EDITOR agent-runner.toml         # 把 agent.command 指向你的 CLI
agent-runner install --monitor    # 装 systemd user unit（serve + monitor）
agent-runner status               # 确认已启动
agent-runner peek                 # 看当前项目状态快照
agent-runner monitor              # 实时异常检测，OAuth/磁盘 critical 时自动停服
```

完整上手流程：[`docs/quickstart.md`](docs/quickstart.md)。

## 13 个动词

| 生命周期 | 观察 |
|---|---|
| `init` / `install` / `uninstall` | `peek` —— 项目状态快照 |
| `start` / `stop` / `kill` / `cancel` | `watch` —— peek 在刷新循环里 |
| `restart` / `status` | `monitor` —— 9 个检测器 + 告警 + 自动停服 |
| `round` / `serve` | |

**停服三动词**有清晰的语义分层：
- `stop` —— 优雅，等当前 round 跑完再退（最常用）
- `kill` —— 强制，SIGTERM 后 5 秒还活着就 SIGKILL（卡死时用）
- `cancel` —— 给 claude 进程发 SIGINT，请求"提交并退出"（最轻）

动词参考：[`docs/commands.md`](docs/commands.md)。

## 内置防御（11 条）

防御以数据形式定义在 `agent_runner/defenses.py`，可通过
`agent-runner peek --select defenses` 直接拿到。每条防御自带：

- `name` —— 稳定标识
- `value` —— 当前 runtime 取值
- `codifies` —— 防的是哪一条历史教训（argus R\* 编号或 issue）
- `guarded_by` —— 守护它的 invariant test 文件
- `current_state` —— `active` / `degraded` / `off`

几个亮点：

- **round_timeout_s** —— 硬墙超时，不信任 agent 自报的状态
- **process_group_isolation** —— 杀整个进程组，不只 parent
- **orphan_stash_idempotency_s** —— 同一秒内重复 stash 去重
- **sha_locked_stash** —— 用 SHA 不用 `stash@{N}`（并发下 index 会漂）
- **set_diff_classification** —— 用 set 差集判 auto-tool vs human，不解析 unified diff
- **startup_smoke_check** —— prompt 文件明显残缺（<500 字节）直接拒跑

完整列表 + 历史出处：[`docs/architecture.md`](docs/architecture.md)。

## Monitor：9 个检测器

**只告警**（warning 级，服务继续跑）：
`timeout_rate` / `hung` / `orphan_chain` / `disk_warning` /
`mem_pressure` / `smoke_fail_rate` / `network_fail`

**自动停服**（critical 级，继续是 net negative）：

- `oauth_fail` —— 最近 10 轮里 ≥ 20% 命中 auth pattern；继续就是烧 API quota
- `disk_critical` —— 磁盘 >= 95%；继续 emit events 风险 corruption

本机或远程都行：

```bash
agent-runner monitor                  # 本地，30s 一拍
agent-runner monitor --host pi        # 通过 ssh 观测 pi，60s 一拍
agent-runner monitor --json | jq -c   # 输出 JSON 给下游 consumer
```

## 文档与代码同步

为减少文档维护负担，agent-runner 把文档按"是否能机器生成"分了三层：

| 层 | 文件 | 来源 |
|---|---|---|
| 手写 | `README*`、`docs/runbook.md` | 价值主张 + 操作经验，机器没法替代 |
| 数据型 | `docs/commands.md` / `configuration.md` / `architecture.md` 的表格 | 从代码 SSOT（`defenses.catalog()` / `KNOWN_*_KINDS` / `Config` dataclass / argparse）自动生成；CI gate 守一致 |
| 可执行 | `docs/quickstart.md` | markdown 里的 bash 块在 pytest 里实际执行验证 |

开发者改代码后 `./build.sh check` 会自动验证文档没漂移。机制详见
[`docs/internal/specs/2026-05-12-docs-as-tests-design.md`](docs/internal/specs/2026-05-12-docs-as-tests-design.md)。

## 文档导航

- [`docs/quickstart.md`](docs/quickstart.md) —— 5 步上手 + 第一轮跑通
- [`docs/commands.md`](docs/commands.md) —— 动词参考（含 --json / --select / --round 等下钻 flag）
- [`docs/configuration.md`](docs/configuration.md) —— `agent-runner.toml` schema
- [`docs/runbook.md`](docs/runbook.md) —— 操作手册 + 故障排查（OAuth / 磁盘 / orphan 抢救）
- [`docs/architecture.md`](docs/architecture.md) —— 三层模型 + defenses-as-data 详解

## 开发

```bash
./build.sh check    # lint + 单元 + 集成 + literate + docs 一致性，本地 CI 全套
./build.sh test     # 仅 230+ 单元 + 集成测试
./build.sh literate # 仅跑 quickstart.md 的可执行块
./build.sh e2e      # pi 上端到端测试（需要 ssh alias 'pi' + AGENT_RUNNER_E2E_PI=1）
```

只支持 POSIX（Linux / macOS）。Python 3.11+。x86_64 与 aarch64 都跑过。

## 项目状态

Phase 2（运维界面）已发。Phase 3（LLM Critic）留了接口：`[llm]` 配置段 +
`agent_runner.critic` Protocol stubs，实现待定。

## License

[Apache License 2.0](LICENSE).
