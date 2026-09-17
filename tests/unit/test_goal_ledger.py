"""write_ledger_advisory: the supervisor-owned lessons-ledger writer. Every
call PREPENDS one bounded markdown block (newest first), TRUNCATES the whole
file to <=8192 bytes at a block boundary (oldest dropped first), writes via a
`.tmp` + `os.replace` (atomic, no stray tmp file survives), redacts every
field through `_redact.redact_secrets` before it ever touches disk, and emits
one `goal_assessment` event per call."""

from __future__ import annotations

from pathlib import Path

from agent_runner import events
from agent_runner.goal import Advisory, write_ledger_advisory


def _events(log_dir: Path) -> list[dict]:
    files = list(log_dir.glob("events-*.jsonl"))
    assert len(files) == 1
    return list(events.iter_event_dicts(files[0]))


def _advisory(n: int) -> Advisory:
    return Advisory(
        observation=f"marker-{n}: the tree keeps moving but the check stays unsatisfied",
        question=f"question-{n}: what specifically is blocking check X?",
        confidence="medium",
    )


def test_write_ledger_advisory_should_create_ledger_when_absent(
    tmp_path: Path, tmp_log_dir: Path
) -> None:
    ledger_path = tmp_path / "ledger.md"

    write_ledger_advisory(ledger_path, _advisory(1), log_dir=tmp_log_dir)

    assert ledger_path.exists()
    assert "marker-1" in ledger_path.read_text(encoding="utf-8")


def test_write_ledger_advisory_should_prepend_newest_advisory_when_ledger_exists(
    tmp_path: Path, tmp_log_dir: Path
) -> None:
    ledger_path = tmp_path / "ledger.md"
    write_ledger_advisory(ledger_path, _advisory(1), log_dir=tmp_log_dir)

    write_ledger_advisory(ledger_path, _advisory(2), log_dir=tmp_log_dir)

    content = ledger_path.read_text(encoding="utf-8")
    assert "marker-1" in content
    assert "marker-2" in content
    assert content.index("marker-2") < content.index("marker-1"), (
        "newest advisory must be prepended, not appended"
    )


def test_write_ledger_advisory_should_stay_under_8192_bytes_and_keep_newest_after_200_writes(
    tmp_path: Path, tmp_log_dir: Path
) -> None:
    ledger_path = tmp_path / "ledger.md"

    for n in range(200):
        write_ledger_advisory(ledger_path, _advisory(n), log_dir=tmp_log_dir)

    raw = ledger_path.read_bytes()
    assert len(raw) <= 8192
    content = raw.decode("utf-8")
    assert "marker-199" in content, "the newest advisory must survive truncation"
    assert "marker-0" not in content, "the oldest advisory must be dropped first"


def test_write_ledger_advisory_should_redact_secret_tokens_before_writing(
    tmp_path: Path, tmp_log_dir: Path
) -> None:
    ledger_path = tmp_path / "ledger.md"
    leaky = Advisory(
        observation="the check output was sk-ant-abcdefghijklmnopqrstuvwxyz012345",
        question="is this token still valid?",
        confidence="low",
    )

    write_ledger_advisory(ledger_path, leaky, log_dir=tmp_log_dir)

    content = ledger_path.read_text(encoding="utf-8")
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz012345" not in content
    assert "<redacted>" in content


def test_write_ledger_advisory_should_leave_no_tmp_file_behind(
    tmp_path: Path, tmp_log_dir: Path
) -> None:
    ledger_path = tmp_path / "ledger.md"

    write_ledger_advisory(ledger_path, _advisory(1), log_dir=tmp_log_dir)
    write_ledger_advisory(ledger_path, _advisory(2), log_dir=tmp_log_dir)

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_write_ledger_advisory_should_emit_goal_assessment_event(
    tmp_path: Path, tmp_log_dir: Path
) -> None:
    ledger_path = tmp_path / "ledger.md"
    advisory = _advisory(1)

    write_ledger_advisory(ledger_path, advisory, log_dir=tmp_log_dir)

    [ev] = _events(tmp_log_dir)
    assert ev["event"] == "goal_assessment"
    assert advisory.observation in ev["observation"]
    assert advisory.question in ev["question"]
    assert ev["confidence"] == "medium"
    # NO kill/severity/action/terminate field anywhere on the emitted event --
    # the advisory path is structurally unable to express a command.
    for forbidden in ("kill", "severity", "action", "terminate"):
        assert forbidden not in ev
