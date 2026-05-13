"""systemd user-unit content generators for serve and monitor.

Two units per project:
  agent-runner@<project>.service          - runs `agent-runner serve`
  agent-runner-monitor@<project>.service  - runs `agent-runner monitor`

Install command writes these to ~/.config/systemd/user/. The graceful-stop
contract relies on KillSignal=SIGTERM + TimeoutStopSec=max(round_timeout_s,
round_timeout_per_phase.values())+60 — the LARGEST possible round budget
plus grace.
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
    # 0.1.9: TimeoutStopSec must cover the LARGEST possible round budget so
    # `systemctl stop` doesn't SIGKILL a legitimate per-phase long round.
    # Use list form so empty per_phase dict still works (max(int) fails).
    max_round_timeout = max(
        [cfg.runtime.round_timeout_s, *cfg.runtime.round_timeout_per_phase.values()]
    )
    timeout_total = max_round_timeout + _GRACE_S
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
