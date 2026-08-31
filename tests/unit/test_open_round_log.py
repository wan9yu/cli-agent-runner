from __future__ import annotations

from pathlib import Path

from agent_runner.monitor import load_round_log_tails


def test_non_utf8_byte_in_round_log_does_not_raise(tmp_path: Path) -> None:
    rounds = tmp_path / "rounds"
    rounds.mkdir()
    # 0x80 is an invalid UTF-8 start byte; a strict decoder raises here.
    (rounds / "R1-2026-08-30.log").write_bytes(b"line one\n\x80\xff bad bytes\nline three\n")
    tails = load_round_log_tails(rounds)
    assert 1 in tails
    assert "line three" in tails[1]  # decoded with errors="replace", not crashed


def test_open_round_log_pins_errors_replace(tmp_path: Path) -> None:
    from agent_runner.round_log import open_round_log

    p = tmp_path / "R2-x.log"
    p.write_bytes(b"\x80abc")
    with open_round_log(p) as fh:
        assert "abc" in fh.read()  # replacement char + abc, no UnicodeDecodeError
