"""agent-runner migrate — rewrite removed/renamed config fields to current form."""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_runner import migrations
from agent_runner.cli.common import fail  # `fail(msg, *, code=1)` prints to stderr, returns code


def add_parser(sub, parent) -> None:
    p = sub.add_parser(
        "migrate",
        parents=[parent],
        help="Rewrite removed/renamed fields in agent-runner.toml to current form",
    )
    p.add_argument("--dry-run", action="store_true", help="Show changes without writing the file")
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    cfg_path = Path(getattr(args, "config", None) or "agent-runner.toml")
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError as e:
        return fail(f"cannot read {cfg_path}: {e}", code=2)
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return fail(f"{cfg_path} is not valid TOML: {e}", code=2)

    result = migrations.run_migrations(text, parsed)
    if not result.applied and not result.manual:
        print(f"{cfg_path}: nothing to migrate")
        return 0

    for line in result.applied:
        print(f"  rewrite: {line}")
    for line in result.manual:
        print(f"  MANUAL:  {line}")

    if args.dry_run:
        print("(dry-run — no changes written)")
        return 1 if result.manual else 0

    if result.applied:
        cfg_path.with_suffix(cfg_path.suffix + ".bak").write_text(text, encoding="utf-8")
        cfg_path.write_text(result.new_text, encoding="utf-8")
        print(f"wrote {cfg_path} (backup: {cfg_path.name}.bak)")
    return 1 if result.manual else 0
