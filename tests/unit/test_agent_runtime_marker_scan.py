"""B5: the result marker is detected even when it is split across two incremental
read chunks (the byte-offset delta scan carries len(marker)-1 bytes)."""

from __future__ import annotations

from pathlib import Path

from agent_runner.agent_runtime import run


def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake.sh"
    p.write_text(f"#!/bin/bash\nset -e\n{body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_marker_split_across_chunks_is_detected(tmp_path):
    # First scan sees only '{"type":"res'; the completing 'ult"...' arrives after the
    # next scan tick. Without the carry, the delta read of 'ult"...' never re-forms the
    # marker and the grace kill never fires.
    script = _script(
        tmp_path,
        'printf \'{"type":"res\'\nsleep 1.5\nprintf \'ult","is_error":false}\\n\'\nexec sleep 30\n',
    )
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=20,
        log_path=tmp_path / "round.log",
        env_extra={},
        max_grace_after_result_s=1,
    )
    assert result.killed_for_grace is True
    assert result.duration_s < 12  # reaped via grace, not the 20s wall


def test_delta_scan_does_not_reread_prefix(tmp_path, monkeypatch):
    """Every rb read the marker scan performs advances the byte offset; across
    the whole scan lifetime each byte of the log is read exactly once. The old
    eager scan re-opened and re-read the WHOLE (ever-growing) file on every
    0.2s tick, so its total bytes-read balloons far past the file's final
    size (the O(n^2) cost over a long-idle round) -- that is the regression
    this test pins."""
    reads: list[int] = []
    real_open = Path.open

    def spy_open(self, *a, **k):
        fh = real_open(self, *a, **k)
        if a and a[0] == "rb":
            orig_read = fh.read

            def read(*ra, **rk):
                data = orig_read(*ra, **rk)
                reads.append(len(data))
                return data

            fh.read = read
        return fh

    monkeypatch.setattr(Path, "open", spy_open)
    # >4KB of pre-marker output (2000 lines * ~9 bytes ≈ 18KB) so a whole-file
    # re-read is a real, sizeable cost -- not a rounding artifact (red phase
    # is genuine: today's f.read() reads all ~18KB on every 0.2s tick).
    script = _script(tmp_path, "for i in $(seq 1 2000); do echo line$i; done\nexec sleep 5\n")
    log_path = tmp_path / "round.log"
    run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=8,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
    )
    assert reads, "marker scan never ran"
    final_size = log_path.stat().st_size
    assert final_size > 4096  # the payload is large enough for a re-read to matter
    # No byte is ever read twice: total bytes read across the round == the
    # log's final size, regardless of how many ticks elapsed.
    assert sum(reads) == final_size
