"""Internal identity/location resolver (Group C, 0.2.13 "close the seams").

Project name, config path, unit filename and log_dir were each derived
independently at several call sites and could disagree when ``work_dir`` and
the TOML's own directory differ. This module is the single source for all
four; the divergent call sites route through it instead of re-deriving. It
also carries ``guard_against_clobber``, ``api.install``'s same-basename
sibling-unit guard — a natural fit since it works entirely off unit-file
identity (``WorkingDirectory=``) rather than any install-specific state.

INTERNAL ONLY -- not a public/documented contract. Exposing a resolver as a
public contract is deferred to 0.3.

Leaf module: imports only ``agent_runner.config`` and
``agent_runner.service_unit`` (both leaves themselves), so anything in the
package can import this without risking a cycle.

Lenient/strict split (spec-review correction): validating the project
name on the serve/round/init path would newly reject any ``work_dir`` whose
basename has a space or CJK characters -- common on macOS dev boxes -- an
unflagged BREAK, since only lifecycle/observe verbs validate today. So
``strict=True`` applies ONLY where the name is interpolated into a unit
filename / ssh / systemd identity (api.py's lifecycle verbs, install,
lifecycle.py, install_cmd.py); ``strict=False`` for descriptive uses
(``hook_ctx.project`` in runner.py, the scaffolded toml's ``{project}`` in
scaffold.py) that must keep accepting any basename.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_runner.config import load_config
from agent_runner.service_unit import serve_unit_filename

_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def project_name(work_dir: Path, *, strict: bool) -> str:
    """Derive the project name from ``work_dir``'s basename.

    strict=True rejects anything but ``[A-Za-z0-9._-]+`` -- required wherever
    the name is interpolated into a unit filename / ssh / systemd identity.
    strict=False accepts any non-empty basename (spaces, CJK, etc.) -- for
    descriptive uses where the name is never shelled out or used as a
    filesystem/systemd token by itself.
    """
    name = work_dir.resolve().name or "default"
    if strict and not _PROJECT_NAME_RE.match(name):
        raise ValueError(
            f"invalid project name {name!r}: must match [A-Za-z0-9._-]+. "
            "The project name is the basename of work_dir and is interpolated into "
            "ssh remote commands and systemd unit filenames; shell metacharacters "
            "and path separators are rejected."
        )
    return name


def config_path(args) -> Path:
    """Resolve the config TOML path CLI ``args`` refer to (``--config``,
    default ``./agent-runner.toml``).

    Single source for "given argparse args, which agent-runner.toml" so every
    CLI bootstrap step (which runs before a ``Config`` exists to read
    ``runtime.work_dir`` back from) agrees on the same file, instead of each
    reconstructing its own getattr/default dance.

    Uses ``.absolute()``, NOT ``.resolve()``: a relative ``[runtime] work_dir``
    in the loaded toml anchors to THIS path's parent, so resolving a symlinked
    ``--config`` here would silently re-anchor work_dir to the symlink's
    TARGET directory instead of the directory the caller actually named.
    ``.absolute()`` makes the path absolute (still comparable/joinable) without
    ever dereferencing a symlink.
    """
    cfg = getattr(args, "config", None)
    return Path(cfg).absolute() if cfg is not None else Path("agent-runner.toml").absolute()


def unit_filename(project: str) -> str:
    """Thin wrap of the existing ``service_unit.serve_unit_filename`` -- the
    single name every unit-filename call site should go through."""
    return serve_unit_filename(project)


def default_log_dir(name: str) -> Path:
    """Return the conventional ``~/.agent-runner/<name>/logs`` fallback for a
    bare project name with no locally-readable toml to read log_dir from.

    Single source for that fallback: shared by ``log_dir``'s own missing-toml
    branch below and ``api._resolve_target``'s bare-string branch (a project
    name that isn't the caller's own cwd, so there is no toml to load).
    """
    return Path.home() / ".agent-runner" / name / "logs"


def log_dir(work_dir: Path) -> Path:
    """Return the configured log_dir from ``<work_dir>/agent-runner.toml``.

    Falls back to the conventional ``~/.agent-runner/<project>/logs`` only
    when the toml is missing there, keeping lifecycle/observe verbs aligned
    with where ``serve_cmd.py`` actually writes ``serve.pid``.
    """
    cfg_path = work_dir / "agent-runner.toml"
    if cfg_path.exists():
        return load_config(cfg_path).runtime.log_dir
    return default_log_dir(project_name(work_dir, strict=True))


def _unit_owner(serve_path: Path) -> Path | None:
    """Parse an existing unit's ``WorkingDirectory=`` line, or None if the file
    doesn't exist / can't be read / has no such line."""
    try:
        text = serve_path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("WorkingDirectory="):
            return Path(line.removeprefix("WorkingDirectory="))
    return None


def guard_against_clobber(serve_path: Path, work_dir: Path, *, force: bool) -> None:
    """Refuse to overwrite a same-basename sibling project's unit.

    The unit filename is derived from the project name (work_dir's basename)
    alone, so two unrelated projects sharing a basename — or the same project
    moved to a new path — would silently clobber each other's install.
    force=True is the explicit override for the moved-repo case (``api.install``).
    """
    owner = _unit_owner(serve_path)
    if owner is None or owner == work_dir or force:
        return
    raise FileExistsError(
        f"{serve_path} already manages a different project at {owner} "
        f"(this is {work_dir}); pass force=True to overwrite "
        "(e.g. after moving/renaming that project's directory)."
    )
