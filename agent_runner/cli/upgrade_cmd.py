"""upgrade subcommand — round-boundary smooth upgrade flow.

Orchestrates: capture from_version → graceful stop → pip install → smoke check
the new binary → start → emit `service_upgraded` event. Auto-rollback on smoke
failure (reinstall from_version, sanity smoke, start, emit
`service_upgrade_rolled_back`). Worst case (rollback fails too):
`service_upgrade_rollback_failed` event + exit 2.

On-host only. Assumes `pip` in PATH. Operator manually triggers via shell
(systemd-managed installs use `agent-runner upgrade` from the installation venv).
"""

from __future__ import annotations

import re
import subprocess  # noqa: TID251 — orchestration uses pip + smoke subprocess
import sys
import time
from pathlib import Path

from agent_runner import __version__, api, events
from agent_runner.cli.common import cfg_from_args, fail, info
from agent_runner.config import Config


def add_parser(sub, parent) -> None:
    p = sub.add_parser(
        "upgrade",
        parents=[parent],
        help=(
            "Round-boundary upgrade: stop → pip install → smoke → start"
            " (auto-rollback on smoke fail)"
        ),
    )
    p.add_argument(
        "--target",
        type=str,
        default=None,
        metavar="VERSION",
        help="Pin a specific version (e.g. 0.1.13). Default: latest from PyPI. "
        "Use to roll back: `--target <previous-version>`.",
    )
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    cfg = cfg_from_args(args)
    return _run_upgrade(cfg, target=args.target, cfg_path=args.config)


def _pip_install(spec: str, *, force_reinstall: bool = False) -> subprocess.CompletedProcess:
    """Invoke pip install with the given spec. Returns CompletedProcess (rc check by caller)."""
    cmd = ["pip", "install", "--upgrade", spec]
    if force_reinstall:
        cmd.insert(2, "--force-reinstall")
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _smoke_version() -> tuple[int, str]:
    """Spawn fresh Python to read NEW agent_runner.__version__. Returns (rc, version_string).

    Subprocess imports the on-disk agent_runner module, which is the freshly-installed
    code (vs the upgrade command's own process which has the OLD module loaded).
    """
    r = subprocess.run(
        [sys.executable, "-m", "agent_runner.cli", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return r.returncode, r.stderr.strip()[:200]
    m = re.match(r"agent-runner\s+(\S+)", r.stdout.strip())
    if not m:
        return 1, f"unparseable --version output: {r.stdout!r}"
    return 0, m.group(1)


def _smoke_peek(cfg_path: Path) -> tuple[int, str]:
    """Spawn fresh Python to run `peek --json --config <path>`. Returns (rc, error_excerpt)."""
    r = subprocess.run(
        [sys.executable, "-m", "agent_runner.cli", "--config", str(cfg_path), "peek", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return r.returncode, r.stderr.strip()[:200]
    return 0, ""


def _run_upgrade(cfg: Config, *, target: str | None, cfg_path: Path) -> int:
    """Orchestrate the full upgrade flow.

    Returns exit code (0 success, 1 user-recoverable, 2 critical).
    """
    # Fix #4: reject empty/whitespace-only --target before touching service
    if target is not None and not target.strip():
        return fail("--target must be a non-empty version string (e.g. 0.1.13)")

    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    from_version = __version__
    t0 = time.monotonic()

    # Step 2: stop
    info("stopping service...")
    t_stop = time.monotonic()
    try:
        api.stop(cfg.runtime.work_dir)
    except Exception as e:  # noqa: BLE001 — service state unknown; must not proceed
        return fail(
            f"api.stop raised {type(e).__name__}: {str(e)[:150]}; "
            f"service state unknown — investigate before retrying upgrade"
        )
    info(f"stopped ({time.monotonic() - t_stop:.1f}s)")

    # Step 3: pip install
    spec = "cli-agent-runner" if target is None else f"cli-agent-runner=={target}"
    info(f"installing {spec}...")
    t_pip = time.monotonic()
    pip_result = _pip_install(spec)
    if pip_result.returncode != 0:
        return fail(
            f"pip install failed (rc={pip_result.returncode}): "
            f"{pip_result.stderr.strip()[:200]}; "
            f"service is stopped, run 'agent-runner start' to resume previous version"
        )
    info(f"installed ({time.monotonic() - t_pip:.1f}s)")

    # Step 4: smoke (--version + peek)
    info("smoke check (--version + peek)...")
    rc_v, version_or_err = _smoke_version()
    if rc_v != 0:
        return _rollback(
            cfg,
            log_dir,
            from_version,
            attempted_version=target or "<unknown>",
            failure_reason=version_or_err,
            started_at=t0,
            cfg_path=cfg_path,
        )
    to_version = version_or_err

    rc_p, peek_err = _smoke_peek(cfg_path)
    if rc_p != 0:
        return _rollback(
            cfg,
            log_dir,
            from_version,
            attempted_version=to_version,
            failure_reason=peek_err,
            started_at=t0,
            cfg_path=cfg_path,
        )

    info(f"smoke OK (now at {to_version})")

    # Step 6: start
    info("starting service...")
    t_start = time.monotonic()
    try:
        api.start(cfg.runtime.work_dir)
    except Exception as e:  # noqa: BLE001 — new version installed but service stopped; no safe auto-rollback
        return _rollback_failed(
            log_dir,
            to_version,
            to_version,
            f"api.start raised after upgrade: {type(e).__name__}: {str(e)[:150]}",
        )
    info(f"started ({time.monotonic() - t_start:.1f}s)")

    # Step 7: emit success event
    elapsed = time.monotonic() - t0
    events.emit(
        log_dir,
        events.SERVICE_UPGRADED,
        from_version=from_version,
        to_version=to_version,
        duration_s=elapsed,
    )
    info(f"upgraded {from_version} → {to_version} ({elapsed:.1f}s total)")
    return 0


def _rollback(
    cfg: Config,
    log_dir: Path,
    from_version: str,
    *,
    attempted_version: str,
    failure_reason: str,
    started_at: float,
    cfg_path: Path,
) -> int:
    """Smoke failed at attempted_version; reinstall from_version and recover.

    Emits ``service_upgrade_rolled_back`` on success (exit 1).
    Emits ``service_upgrade_rollback_failed`` if the rollback itself fails (exit 2).
    """
    info(f"smoke failed at {attempted_version}; rolling back to {from_version}...")

    # Reinstall the from_version (force, since pip thinks the new version is installed)
    rollback_pip = _pip_install(f"cli-agent-runner=={from_version}", force_reinstall=True)
    if rollback_pip.returncode != 0:
        return _rollback_failed(
            log_dir,
            attempted_version,
            from_version,
            f"pip --force-reinstall failed (rc={rollback_pip.returncode}): "
            f"{rollback_pip.stderr.strip()[:200]}",
        )

    # Sanity smoke: --version on the rolled-back binary should match from_version
    rc_v, version_or_err = _smoke_version()
    if rc_v != 0:
        return _rollback_failed(
            log_dir,
            attempted_version,
            from_version,
            f"sanity smoke failed (rc={rc_v}): {version_or_err}",
        )
    if version_or_err != from_version:
        return _rollback_failed(
            log_dir,
            attempted_version,
            from_version,
            f"sanity smoke version mismatch: expected {from_version}, got {version_or_err}",
        )

    # Start service with rolled-back version
    try:
        api.start(cfg.runtime.work_dir)
    except Exception as e:  # noqa: BLE001 — best-effort recovery, must not raise further
        return _rollback_failed(
            log_dir,
            attempted_version,
            from_version,
            f"api.start raised after rollback: {type(e).__name__}: {str(e)[:150]}",
        )

    elapsed = time.monotonic() - started_at
    events.emit(
        log_dir,
        events.SERVICE_UPGRADE_ROLLED_BACK,
        attempted_version=attempted_version,
        restored_version=from_version,
        failure_reason=failure_reason,
        duration_s=elapsed,
    )
    info(
        f"upgrade {from_version} → {attempted_version} failed smoke; "
        f"restored {from_version} (service running)"
    )
    return 1


def _rollback_failed(
    log_dir: Path,
    attempted_version: str,
    restore_target_version: str,
    failure_reason: str,
) -> int:
    """Worst case: rollback itself failed. Service is stopped, no working code.

    Best-effort emit ``service_upgrade_rollback_failed`` event so operators can
    grep for it. Returns exit 2 (worst code) so caller can distinguish from
    user-recoverable exit 1.
    """
    try:
        events.emit(
            log_dir,
            events.SERVICE_UPGRADE_ROLLBACK_FAILED,
            attempted_version=attempted_version,
            restore_target_version=restore_target_version,
            failure_reason=failure_reason[:200],
        )
    except Exception:  # noqa: BLE001 — best-effort: log_dir itself may be unwritable
        pass
    info(
        f"CRITICAL: upgrade rollback failed; service is STOPPED. "
        f"Manual intervention required (try: pip install --force-reinstall "
        f"cli-agent-runner=={restore_target_version})."
    )
    return 2
