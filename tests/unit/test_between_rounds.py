"""examples/between_rounds/between_rounds.py — atomic replace, prompt smoke, give-up, JSONL."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests._test_helpers import ROOT

_EX = ROOT / "examples" / "between_rounds" / "between_rounds.py"


def _mod():
    spec = importlib.util.spec_from_file_location("between_rounds", _EX)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_atomic_replace_should_overwrite_complete_file_when_invoked(tmp_path: Path) -> None:
    br = _mod()
    target = tmp_path / "prompt.md"
    target.write_text("old")

    br.atomic_replace(target, "new-body")

    assert target.read_text() == "new-body"
    assert not list(tmp_path.glob(".swap-*"))


def test_prompt_smoke_error_should_reject_short_else_dash_when_invoked(tmp_path: Path) -> None:
    br = _mod()

    assert br.prompt_smoke_error("") == "empty"
    assert br.prompt_smoke_error("-flag") == "bad first character"
    assert br.prompt_smoke_error("x" * 10) == "under 500 bytes"

    assert br.prompt_smoke_error("x" * 500) is None


def test_apply_queued_prompt_should_refuse_smoke_failure_when_invoked(tmp_path: Path) -> None:
    br = _mod()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("x" * 500)
    queued = tmp_path / "next.md"
    queued.write_text("-oops")

    with pytest.raises(ValueError):
        br.apply_queued_prompt(prompt, queued)

    assert prompt.read_text() == "x" * 500
    assert queued.is_file()


def test_apply_queued_prompt_should_swap_and_unlink_when_invoked(tmp_path: Path) -> None:
    br = _mod()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("x" * 500)
    queued = tmp_path / "next.md"
    body = "y" * 500
    queued.write_text(body)

    br.apply_queued_prompt(prompt, queued)

    assert prompt.read_text() == body
    assert not queued.exists()


def test_iter_complete_objects_should_leave_partial_last_line_when_invoked(tmp_path: Path) -> None:
    br = _mod()
    path = tmp_path / "events-2026-09.jsonl"
    path.write_bytes(b'{"event":"round_end"}\n{"event":"round_start"')
    batch, offset = br.iter_complete_objects(path, 0)
    assert [e["event"] for e in batch] == ["round_end"]

    with path.open("ab") as fh:
        fh.write(b',"config_digest":"abc"}\n')
    batch2, _ = br.iter_complete_objects(path, offset)

    assert batch2 == [{"event": "round_start", "config_digest": "abc"}]


def test_give_up_kind_should_detect_config_broken_when_invoked() -> None:
    br = _mod()

    assert br.give_up_kind([{"event": "round_end"}]) is None
    assert br.give_up_kind([{"event": "config_broken"}]) == "config_broken"

    assert "mem_loop" not in br.GIVE_UP_KINDS
