"""Invariant: every user-facing config error is a ConfigError.

ConfigError's own docstring promises it for "a removed or invalid field", and
docs/configuration.md:179/181, CHANGELOG.md:354 and docs/migrations/0.2.1.md:48
all tell operators to expect it — but it was raised at 1 of 26 sites. Since
ConfigError subclasses ValueError and nothing catches ValueError from
load_config, promotion is a widening: no caller can observe the difference
except by asking for the more specific class, which the docs already do.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests._test_helpers import write_min_config

REPO = Path(__file__).resolve().parents[2]
CONFIG_PY = REPO / "agent_runner/config.py"

# Non-config failures that legitimately use a different class.
_ALLOWED_OTHER = {"FileNotFoundError"}


def _raised_class_names() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(CONFIG_PY.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                out.append((node.lineno, exc.func.id))
            elif isinstance(exc, ast.Name):
                out.append((node.lineno, exc.id))
    return out


def test_given_config_module_when_scanned_then_user_facing_raises_are_config_error() -> None:
    """No bare ValueError in config.py — every invalid-field path is a ConfigError."""
    offenders = [
        f"config.py:{lineno}: raise {name}"
        for lineno, name in sorted(_raised_class_names())
        if name not in _ALLOWED_OTHER and name != "ConfigError"
    ]
    assert not offenders, (
        "config.py raises a non-ConfigError for a config problem — "
        "ConfigError's docstring and docs/configuration.md:181 promise "
        "ConfigError:\n" + "\n".join(offenders)
    )


def test_given_config_error_when_inspected_then_subclasses_value_error() -> None:
    """The promotion is a widening only because of this relationship. Pin it:
    if ConfigError ever stops subclassing ValueError, every existing
    `pytest.raises(ValueError)` caller silently breaks."""
    from agent_runner.config import ConfigError

    assert issubclass(ConfigError, ValueError)


def test_given_stdin_delivery_with_prompt_token_when_loaded_then_config_error(
    tmp_path: Path,
) -> None:
    """docs/migrations/0.2.1.md:48 states ConfigError at startup for this config."""
    from agent_runner.config import ConfigError, load_config

    cfg_path = write_min_config(
        tmp_path,
        agent_extra='prompt_delivery = "stdin"\nprompt_arg_template = ["-p", "{prompt}"]\n',
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)
