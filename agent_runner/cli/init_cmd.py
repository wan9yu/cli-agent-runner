"""init subcommand — scaffold project."""

from __future__ import annotations

from agent_runner import api
from agent_runner.cli.common import emit, fail, work_dir_from_args


def add_parser(sub, parent) -> None:
    p = sub.add_parser("init", parents=[parent], help="Scaffold agent-runner project files")
    p.add_argument(
        "--preset",
        choices=["claude", "aider"],
        default="claude",
        help="Which agent CLI preset to scaffold (default: claude)",
    )
    p.add_argument("--force", action="store_true", help="Overwrite existing toml")
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--commit",
        dest="commit",
        action="store_true",
        default=True,
        help="git commit the new files (default)",
    )
    g.add_argument("--no-commit", dest="commit", action="store_false", help="Skip git commit")
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    work_dir = work_dir_from_args(args)
    try:
        result = api.init(work_dir, preset=args.preset, force=args.force, commit=args.commit)
    except (FileExistsError, RuntimeError, FileNotFoundError) as e:
        return fail(str(e))
    emit(result, json_mode=getattr(args, "json", False))
    return 0
