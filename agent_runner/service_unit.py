"""systemd user-unit content generators for serve and monitor.

Two units per project:
  agent-runner@<project>.service          - runs `agent-runner serve`
  agent-runner-monitor@<project>.service  - runs `agent-runner monitor`

Install command writes these to ~/.config/systemd/user/. The graceful-stop
contract relies on KillMode=mixed + KillSignal=SIGTERM + TimeoutStopSec
(derived from max(round_timeout_s, *per_phase) via
``_serve_policy.timeout_budget`` — see that function for the margin
breakdown): SIGTERM must reach ONLY the serve process (which traps it and
drains the current round). systemd's default KillMode=control-group would
SIGTERM the whole cgroup — round and agent child included — making the drain
structurally ineffective (verified in production by a downstream integrator
via an interrupted round).
"""

from __future__ import annotations

from pathlib import Path

from agent_runner._serve_policy import timeout_budget
from agent_runner.config import Config, _reject_control_chars


def _unit_mode_lines(user: str | None) -> tuple[str, str]:
    """Return (user_lines, wanted_by) for a unit's [Service]/[Install] sections.

    user=None → user-mode unit (no User=, default.target).
    user="dietpi" → system-mode unit (User=dietpi, multi-user.target).
    """
    if user:
        return f"User={user}\nGroup={user}\n", "multi-user.target"
    return "", "default.target"


def _validate_unit_paths(cfg: Config, config_path: Path) -> None:
    """Fail closed on a control/non-printable character in either path that
    gets interpolated verbatim into a rendered unit (``WorkingDirectory=`` /
    ``--config``) -- defense in depth, since ``config.load_config`` already
    rejects these at load but a ``Config`` built directly bypasses that."""
    _reject_control_chars(str(cfg.runtime.work_dir), "runtime.work_dir")
    _reject_control_chars(str(config_path), "config path")


def serve_unit_filename(project: str) -> str:
    return f"agent-runner@{project}.service"


def monitor_unit_filename(project: str) -> str:
    return f"agent-runner-monitor@{project}.service"


def render_serve_unit(
    cfg: Config, *, script_path: Path, config_path: Path, user: str | None = None
) -> str:
    """Generate the serve systemd unit body.

    ``config_path`` must be the ACTUAL toml the caller loaded ``cfg`` from
    (``api.install``'s ``cfg_path``) — not re-derived from ``cfg.runtime.work_dir``.
    ``runtime.work_dir`` is an independently-configurable field (an absolute
    value is legal and can point anywhere; only a *relative* one anchors to
    the toml's own directory — see ``config.load_config``), so the two can
    legitimately diverge; embedding the wrong one silently breaks the unit's
    ``--config`` flag (Group C, seam 3).

    Rejects a control/non-printable character in ``work_dir`` or
    ``config_path`` (defense in depth — ``config.load_config`` already
    rejects these at load, but a ``Config`` built directly bypasses that, and
    this is the function that actually interpolates both into the unit).
    """
    _validate_unit_paths(cfg, config_path)
    # TimeoutStopSec covers the maximum possible round budget so `systemctl stop`
    # doesn't SIGKILL a mid-flight round in any phase.
    max_timeout = cfg.runtime.round_timeout_s
    if cfg.phases is not None:
        for override in cfg.phases.overrides.values():
            if override.round_timeout_s is not None:
                max_timeout = max(max_timeout, override.round_timeout_s)
    timeout_total, _ = timeout_budget(max_timeout)
    user_lines, wanted_by = _unit_mode_lines(user)
    return (
        f"[Unit]\n"
        f"Description=Agent Runner Supervisor ({cfg.runtime.work_dir.name})\n"
        f"After=network.target\n"
        # RestartSec=3 with no ceiling turns a persistent early-exit into an
        # invisible tight respawn; the StartLimit window stops the unit (failed)
        # after StartLimitBurst starts inside StartLimitIntervalSec.
        f"StartLimitIntervalSec=300\n"
        f"StartLimitBurst=5\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"{user_lines}"
        f"WorkingDirectory={cfg.runtime.work_dir}\n"
        f"ExecStart={script_path} serve "
        f"--config {config_path}\n"
        # on-failure (not always) so a deliberate give-up stop — config_broken (78)
        # or crash_loop (75), both in RestartPreventExitStatus — stays stopped and
        # visibly failed, while an unexpected supervisor crash (any other non-zero)
        # still recovers. Clean stops (max_rounds/stop_file/sentinel/SIGTERM → 0)
        # never restart.
        f"Restart=on-failure\n"
        # 78 = api.PERMANENT_CONFIG_EXIT, 75 = api.CRASH_LOOP_EXIT (literal here to
        # avoid an api→service_unit→api import cycle; pinned by test_service_unit).
        # NOTE: mem_loop (71) is deliberately absent here — it must restart
        # (break-then-restart, not a deliberate stop; see api.MEM_LOOP_EXIT).
        f"RestartPreventExitStatus=78 75\n"
        f"RestartSec=3\n"
        f"KillMode=mixed\n"
        f"KillSignal=SIGTERM\n"
        f"TimeoutStopSec={timeout_total}\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy={wanted_by}\n"
    )


def render_monitor_unit(
    cfg: Config, *, script_path: Path, config_path: Path, user: str | None = None
) -> str:
    """Generate the monitor sidekick systemd unit body.

    ``config_path``: see ``render_serve_unit`` — the actual toml the caller
    loaded ``cfg`` from, not re-derived from ``cfg.runtime.work_dir``.
    """
    _validate_unit_paths(cfg, config_path)
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
        f"--config {config_path}\n"
        f"Restart=always\n"
        f"RestartSec=10\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy={wanted_by}\n"
    )
