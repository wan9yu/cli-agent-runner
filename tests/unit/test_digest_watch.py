"""examples/digest_watch/digest_watch.py — round_start lamp, give-up, JSONL."""

from __future__ import annotations

import importlib.util

from tests._test_helpers import ROOT

_EX = ROOT / "examples" / "digest_watch" / "digest_watch.py"


def _mod():
    spec = importlib.util.spec_from_file_location("digest_watch", _EX)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_format_round_start_should_print_digest_prefix_and_changed_when_invoked() -> None:
    dw = _mod()

    line = dw.format_round_start(
        {
            "event": "round_start",
            "round_num": 4,
            "config_digest": "5eda8cf12c6cffff",
            "config_changed": True,
        }
    )
    assert line == "R4 5eda8cf12c6c changed=True"

    assert dw.format_round_start({"event": "round_end"}) is None


def test_give_up_kind_should_not_include_mem_loop_when_invoked() -> None:
    dw = _mod()

    assert dw.give_up_kind([{"event": "config_broken"}]) == "config_broken"

    assert "mem_loop" not in dw.GIVE_UP_KINDS
