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

from agent_runner import api  # noqa: F401 — used by Task 3 implementation


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
    """Stub — implementation lands in Task 3."""
    return 0
