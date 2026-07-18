"""Invariant: the auto-stop default exists once.

config._DEFAULT_AUTO_STOP_ON is the real runtime default (MonitorConfig.auto_stop_on
defaults to it, and load_config falls back to it). monitor.AUTO_STOP_ALERTS is a
second copy used as on_alert's legacy fallback — and _docgen reads *that* copy to
stamp "— **auto-stop**" into docs/architecture.md's generated detector list, which
the page presents as the default policy.

So a change to config.py's default silently leaves architecture.md documenting
monitor.py's copy as "the default", with `./build.sh docs` green. Bind them.
"""

from __future__ import annotations

from pathlib import Path

from tests._test_helpers import make_toml


def test_given_auto_stop_defaults_when_compared_then_single_source() -> None:
    from agent_runner.config import _DEFAULT_AUTO_STOP_ON
    from agent_runner.monitor import AUTO_STOP_ALERTS

    assert set(AUTO_STOP_ALERTS) == set(_DEFAULT_AUTO_STOP_ON), (
        "_docgen documents AUTO_STOP_ALERTS as the default auto-stop policy in "
        "docs/architecture.md, but config._DEFAULT_AUTO_STOP_ON is what "
        "MonitorConfig.auto_stop_on actually defaults to — they must not diverge"
    )


def test_given_default_config_when_loaded_then_auto_stop_on_matches_docgen_source(
    tmp_path: Path,
) -> None:
    """The end-to-end version: what a stock config gets == what the docs publish."""
    from agent_runner.config import load_config
    from agent_runner.monitor import AUTO_STOP_ALERTS

    cfg = load_config(make_toml(tmp_path))
    assert set(cfg.monitor.auto_stop_on) == set(AUTO_STOP_ALERTS)
