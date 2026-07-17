> 中文 · **[English](README.md)**

# agent-runner

把任意 CLI agent（Claude Code、aider、任何接受 prompt 参数的命令）包装成能被
systemd / launchd 拉起、能被远程观测的服务。**每轮跑完进程退出**，外层
supervisor 重启 —— 这是核心模式。中间穿插一组防御，避开 production 上最容易
翻车的几条路：轮卡死、脏文件没 commit、OAuth 失效烧 quota、磁盘写满、
两个 supervisor 抢同一个仓库。

三层，每层都能独立运行，上层都是可选的：`round` 跑一次 agent 跑完即退；
`serve` 捕获信号循环拉起 round；`monitor` 检测器 + 告警，critical 时自动停服
（本机或 ssh 远程）。

## 安装

```bash
pip install cli-agent-runner
```

CLI 命令是 `agent-runner`（PyPI 包名加 `cli-` 前缀只是为了规避命名冲突，
导入名 `agent_runner` 和命令名都不变）。

## 完整文档

文档以英文为准。下面 `docs/` 各页末尾都附有中文摘要，完整细节见英文正文：

- [`docs/quickstart.md`](docs/quickstart.md) —— 5 步装好并跑通第一轮
- [`README.md`](README.md) —— 定位、动词表、内置防御、Monitor 检测器
- [`docs/architecture.md`](docs/architecture.md) —— 三层模型 + defenses-as-data
- [`docs/commands.md`](docs/commands.md) —— 动词参考
- [`docs/configuration.md`](docs/configuration.md) —— `agent-runner.toml` schema
- [`docs/runbook.md`](docs/runbook.md) —— 操作手册 + 故障排查

## License

[Apache License 2.0](LICENSE).
