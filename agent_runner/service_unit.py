"""systemd user-unit content generators for serve and monitor.

Two units per project:
  agent-runner@<project>.service          - runs `agent-runner serve`
  agent-runner-monitor@<project>.service  - runs `agent-runner monitor`

Install command writes these to ~/.config/systemd/user/. The graceful-stop
contract relies on KillSignal=SIGTERM + TimeoutStopSec=round_timeout_s+60.
"""

from __future__ import annotations

from pathlib import Path

from agent_runner.config import Config

_GRACE_S = 60


def serve_unit_filename(project: str) -> str:
    return f"agent-runner@{project}.service"


def monitor_unit_filename(project: str) -> str:
    return f"agent-runner-monitor@{project}.service"


def _config_path(cfg: Config) -> Path:
    """Where the config TOML lives (always relative to work_dir for now)."""
    return cfg.runtime.work_dir / "agent-runner.toml"


def render_serve_unit(cfg: Config, *, venv_bin: Path) -> str:
    """Generate the serve systemd unit body."""
    timeout_total = cfg.runtime.round_timeout_s + _GRACE_S
    return (
        f"[Unit]\n"
        f"Description=Agent Runner Supervisor ({cfg.runtime.work_dir.name})\n"
        f"After=network.target\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"WorkingDirectory={cfg.runtime.work_dir}\n"
        f"ExecStart={venv_bin}/agent-runner serve "
        f"--config {_config_path(cfg)}\n"
        f"Restart=always\n"
        f"RestartSec=3\n"
        f"KillSignal=SIGTERM\n"
        f"TimeoutStopSec={timeout_total}\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


def render_monitor_unit(cfg: Config, *, venv_bin: Path) -> str:
    """Generate the monitor sidekick systemd unit body."""
    return (
        f"[Unit]\n"
        f"Description=Agent Runner Monitor ({cfg.runtime.work_dir.name})\n"
        f"After=network.target "
        f"agent-runner@{cfg.runtime.work_dir.name}.service\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"WorkingDirectory={cfg.runtime.work_dir}\n"
        f"ExecStart={venv_bin}/agent-runner monitor "
        f"--config {_config_path(cfg)}\n"
        f"Restart=always\n"
        f"RestartSec=10\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )
