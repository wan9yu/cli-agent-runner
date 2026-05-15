"""systemd user-unit content generators for serve and monitor.

Two units per project:
  agent-runner@<project>.service          - runs `agent-runner serve`
  agent-runner-monitor@<project>.service  - runs `agent-runner monitor`

Install command writes these to ~/.config/systemd/user/. The graceful-stop
contract relies on KillSignal=SIGTERM + TimeoutStopSec=max(round_timeout_s, *per_phase)+60.
"""

from __future__ import annotations

from pathlib import Path

from agent_runner.config import Config

_GRACE_S = 60


def _unit_mode_lines(user: str | None) -> tuple[str, str]:
    """Return (user_lines, wanted_by) for a unit's [Service]/[Install] sections.

    user=None → user-mode unit (no User=, default.target).
    user="dietpi" → system-mode unit (User=dietpi, multi-user.target).
    """
    if user:
        return f"User={user}\nGroup={user}\n", "multi-user.target"
    return "", "default.target"


def serve_unit_filename(project: str) -> str:
    return f"agent-runner@{project}.service"


def monitor_unit_filename(project: str) -> str:
    return f"agent-runner-monitor@{project}.service"


def _config_path(cfg: Config) -> Path:
    """Where the config TOML lives (always relative to work_dir for now)."""
    return cfg.runtime.work_dir / "agent-runner.toml"


def render_serve_unit(cfg: Config, *, script_path: Path, user: str | None = None) -> str:
    """Generate the serve systemd unit body."""
    # TimeoutStopSec covers the maximum possible round budget so `systemctl stop`
    # doesn't SIGKILL a mid-flight round in any phase.
    max_timeout = cfg.runtime.round_timeout_s
    if cfg.phases is not None:
        for override in cfg.phases.overrides.values():
            if override.round_timeout_s is not None:
                max_timeout = max(max_timeout, override.round_timeout_s)
    timeout_total = max_timeout + _GRACE_S
    user_lines, wanted_by = _unit_mode_lines(user)
    return (
        f"[Unit]\n"
        f"Description=Agent Runner Supervisor ({cfg.runtime.work_dir.name})\n"
        f"After=network.target\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"{user_lines}"
        f"WorkingDirectory={cfg.runtime.work_dir}\n"
        f"ExecStart={script_path} serve "
        f"--config {_config_path(cfg)}\n"
        f"Restart=always\n"
        f"RestartSec=3\n"
        f"KillSignal=SIGTERM\n"
        f"TimeoutStopSec={timeout_total}\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy={wanted_by}\n"
    )


def render_monitor_unit(cfg: Config, *, script_path: Path, user: str | None = None) -> str:
    """Generate the monitor sidekick systemd unit body."""
    user_lines, wanted_by = _unit_mode_lines(user)
    return (
        f"[Unit]\n"
        f"Description=Agent Runner Monitor ({cfg.runtime.work_dir.name})\n"
        f"After=network.target "
        f"agent-runner@{cfg.runtime.work_dir.name}.service\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"{user_lines}"
        f"WorkingDirectory={cfg.runtime.work_dir}\n"
        f"ExecStart={script_path} monitor "
        f"--config {_config_path(cfg)}\n"
        f"Restart=always\n"
        f"RestartSec=10\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy={wanted_by}\n"
    )
