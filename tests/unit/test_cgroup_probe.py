"""0.2.16 Task 3: the cgroup v2 memory-budget probe.

serve's mid-round hard floor (test_spawn_round_mem_floor.py) is a crude
host-wide round-kill -- redundant, even strictly worse, when the kernel's
own cgroup-OOM already guarantees containment. That's only true when this
cgroup's (memory + swap) budget is bounded END TO END: BOTH `memory.max`
and `memory.swap.max` finite. Only-`memory.max`-finite (systemd's
MemoryMax-without-MemorySwapMax default) leaves swap unbounded, so the
agent just swaps forever and cgroup-OOM never fires -- the probe below is
what tells serve which world it's in.

Exercised against a fake `/sys/fs/cgroup` tree (tmp_path) rather than the
real filesystem -- `root`/`proc_self_cgroup`/`self_cgroup` are all
injectable for exactly this reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runner import metrics

_LEAF = "/system.slice/example.service"


class _FakeCgroup:
    """Builds a fake `/sys/fs/cgroup` tree under `tmp_path`.

    Calling the fixture itself writes `cgroup.controllers` (v2 marker,
    unless `v2=False`) and the leaf's `memory.max` / `memory.swap.max`.
    `set_limit` writes those files at an arbitrary ancestor path, for
    exercising the ancestor-min walk independently of the leaf.
    """

    def __init__(self, root: Path):
        self.root = root
        self.self_cgroup = _LEAF

    def _dir_for(self, cgroup_path: str) -> Path:
        d = self.root if cgroup_path == "/" else self.root / cgroup_path.lstrip("/")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def set_limit(
        self,
        cgroup_path: str,
        *,
        memory_max: str | None = None,
        memory_swap_max: str | None = None,
        memory_high: str | None = None,
    ) -> None:
        d = self._dir_for(cgroup_path)
        if memory_max is not None:
            (d / "memory.max").write_text(memory_max)
        if memory_swap_max is not None:
            (d / "memory.swap.max").write_text(memory_swap_max)
        if memory_high is not None:
            (d / "memory.high").write_text(memory_high)

    def __call__(
        self,
        *,
        memory_max: str | None = None,
        memory_swap_max: str | None = None,
        memory_high: str | None = None,
        v2: bool = True,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if v2:
            (self.root / "cgroup.controllers").write_text("memory\n")
        self.set_limit(
            self.self_cgroup,
            memory_max=memory_max,
            memory_swap_max=memory_swap_max,
            memory_high=memory_high,
        )


@pytest.fixture
def fake_cgroup(tmp_path: Path) -> _FakeCgroup:
    return _FakeCgroup(tmp_path / "cgroup")


def test_probe_both_finite(fake_cgroup: _FakeCgroup) -> None:
    """Exactly the field host: MemoryMax=320M + MemorySwapMax=160M -- both
    finite, so the (mem+swap) budget is bounded end to end."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160")  # 320M / 160M

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim == {
        "memory_max": 335544320,
        "memory_swap_max": 167772160,
        "cgroup_path": _LEAF,
    }


def test_probe_swap_unlimited(fake_cgroup: _FakeCgroup) -> None:
    """`"max"` means unlimited -- systemd's MemoryMax-without-MemorySwapMax
    default shape, where the floor must stay armed."""
    fake_cgroup(memory_max="335544320", memory_swap_max="max")

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim["memory_max"] == 335544320
    assert lim["memory_swap_max"] is None


def test_probe_ancestor_min(fake_cgroup: _FakeCgroup) -> None:
    """A bounding systemd slice's memory.max constrains every scope nested
    beneath it -- the tightest ancestor wins, not just the leaf's own
    (unlimited) value."""
    fake_cgroup(memory_max="max", memory_swap_max="max")  # leaf: unlimited
    fake_cgroup.set_limit("/system.slice", memory_max="335544320", memory_swap_max="167772160")

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim["memory_max"] == 335544320
    assert lim["memory_swap_max"] == 167772160


def test_probe_ancestor_min_picks_lower_of_two_finite_values(fake_cgroup: _FakeCgroup) -> None:
    """When BOTH leaf and ancestor are finite, the MIN (tighter) value wins,
    regardless of which level set it."""
    fake_cgroup(memory_max="536870912")  # leaf: 512M
    fake_cgroup.set_limit("/system.slice", memory_max="335544320")  # parent: 320M (tighter)

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim["memory_max"] == 335544320


def test_probe_v1_or_missing_is_unlimited(fake_cgroup: _FakeCgroup) -> None:
    """No `cgroup.controllers` (cgroup v1, or no unified hierarchy at all)
    means there's no reliable fixed-path budget file to read -- probe
    returns all-None rather than guessing."""
    fake_cgroup(v2=False)

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim == {"memory_max": None, "memory_swap_max": None, "cgroup_path": None}


def test_probe_self_cgroup_parsed_from_proc_file(tmp_path: Path) -> None:
    """`self_cgroup` is a test-only shortcut -- the real path is parsed from
    the cgroup v2 unified-hierarchy line (`0::<path>`) in `/proc/self/cgroup`."""
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text("memory\n")
    leaf = root / "system.slice" / "example.service"
    leaf.mkdir(parents=True)
    (leaf / "memory.max").write_text("335544320")
    (leaf / "memory.swap.max").write_text("167772160")

    proc_self_cgroup = tmp_path / "proc_self_cgroup"
    proc_self_cgroup.write_text(
        "12:pids:/system.slice/example.service\n0::/system.slice/example.service\n"
    )

    lim = metrics.cgroup_memory_limits(root=root, proc_self_cgroup=proc_self_cgroup)

    assert lim == {
        "memory_max": 335544320,
        "memory_swap_max": 167772160,
        "cgroup_path": "/system.slice/example.service",
    }


def test_probe_proc_self_cgroup_missing_is_unlimited(tmp_path: Path) -> None:
    """No `0::` line (a pure v1 /proc/self/cgroup, or the file is missing
    entirely) -- can't resolve the v2 path, so all-None."""
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text("memory\n")

    lim = metrics.cgroup_memory_limits(root=root, proc_self_cgroup=tmp_path / "does-not-exist")

    assert lim == {"memory_max": None, "memory_swap_max": None, "cgroup_path": None}


# --- 0.2.18 T1c fix round 1: cgroup_memory_high, a REAL memory.high read ---
#
# memory_high shipped in fix round 1 as a hardcoded None on every
# host_cgroup_memory_limit emit -- a permanently-dead field. These exercise
# the actual bounding-ancestor read against a fake cgroup tree, the same way
# the memory_max/memory_swap_max tests above do.


def test_cgroup_memory_high_reads_finite_value(fake_cgroup: _FakeCgroup) -> None:
    """A finite memory.high (MemoryHigh= set in the unit) reads as the real
    int value -- this is the field team's whole ask: can they see it's set."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160", memory_high="268435456")

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high == 268435456


def test_cgroup_memory_high_unset_is_none(fake_cgroup: _FakeCgroup) -> None:
    """The literal `"max"` (MemoryHigh unset, systemd's default) means
    unset -- None, never the raw "max" token."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160", memory_high="max")

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high is None


def test_cgroup_memory_high_missing_file_is_none(fake_cgroup: _FakeCgroup) -> None:
    """No memory.high file at all (older kernel, or just never written by
    this fixture) -- also None, not an error."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160")

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high is None


def test_cgroup_memory_high_ancestor_min(fake_cgroup: _FakeCgroup) -> None:
    """Same bounding-ancestor MIN-FINITE walk as memory.max: a tighter
    memory.high on an ancestor slice wins over the leaf's own looser value."""
    fake_cgroup(memory_high="536870912")  # leaf: 512M
    fake_cgroup.set_limit("/system.slice", memory_high="268435456")  # parent: 256M (tighter)

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high == 268435456


def test_cgroup_memory_high_v1_or_missing_is_none(fake_cgroup: _FakeCgroup) -> None:
    """No cgroup v2 at all -- None, same as the other probes."""
    fake_cgroup(v2=False)

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high is None


def test_mem_total_bytes_matches_psutil() -> None:
    import psutil

    assert metrics.mem_total_bytes() == psutil.virtual_memory().total


# --- 0.2.16 fix-wave IMPORTANT #1: the cgroup auto-defer plausibility guard ---
#
# A finite `memory.max` alone is not enough to defer the host-wide mid-round
# floor to kernel cgroup-OOM: a stale/copy-pasted unit (e.g. `MemoryMax=1G`
# on a 462MB host) reports a finite-but-IMPLAUSIBLE limit that can never
# actually bind before the host itself runs out of memory -- deferring in
# that shape would leave nothing armed to prevent coma. Both limits finite
# AND memory_max < host MemTotal is now required.


def _patch_probe(
    monkeypatch,
    *,
    memory_max: int | None,
    memory_swap_max: int,
    mem_total: int,
    swap_total: int,
    memory_high: int | None = None,
):
    monkeypatch.setattr(
        metrics,
        "cgroup_memory_limits",
        lambda: {"memory_max": memory_max, "memory_swap_max": memory_swap_max, "cgroup_path": "/x"},
    )
    monkeypatch.setattr(metrics, "mem_total_bytes", lambda: mem_total)
    monkeypatch.setattr(metrics, "swap_total_bytes", lambda: swap_total)
    monkeypatch.setattr(metrics, "cgroup_memory_high", lambda **_k: memory_high)


def test_probe_and_emit_cgroup_defer_implausible_limit_stays_armed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both memory.max and memory.swap.max finite (the normally-deferred
    shape) but memory_max >= host MemTotal (e.g. a copy-pasted MemoryMax=1G
    on a 462MB host) -- cgroup-OOM can never fire before host-wide coma, so
    the floor must NOT defer."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=1024 * 1024 * 1024,  # 1G "limit"
        memory_swap_max=512 * 1024 * 1024,
        mem_total=462 * 1024 * 1024,  # 462MB host
        swap_total=1024 * 1024 * 1024,  # plausible swap cap -- memory_max is the implausible one
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    assert _probe_and_emit_cgroup_defer(log_dir) is False


def test_probe_and_emit_cgroup_defer_plausible_limit_defers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both finite AND tighter than host MemTotal/swap (the field host's
    MemoryMax=320M + MemorySwapMax=160M shape on far more RAM/swap) --
    cgroup-OOM can plausibly fire before host exhaustion, so the floor
    defers. 0.2.18 T1c's swap-plausibility guard must NOT regress this: the
    field host's own shape is exactly "both finite, swap cap within host
    swap" -- the guard only disarms a swap cap ABOVE host swap (see
    test_probe_and_emit_cgroup_defer_huge_swap_cap_stays_armed below)."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=335544320,  # 320M
        memory_swap_max=167772160,  # 160M
        mem_total=2 * 1024 * 1024 * 1024,  # 2G host RAM
        swap_total=2 * 1024 * 1024 * 1024,  # 2G host swap -- 160M cap is well within it
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    assert _probe_and_emit_cgroup_defer(log_dir) is True


# --- 0.2.18 T1c: the swap-plausibility guard + startup swap-cap advisory ---
#
# Field-report ask #3: a huge MemorySwapMax (far above what the host actually
# has) can't bind before host-wide swap exhaustion either -- symmetric with
# the memory_max plausibility guard above -- so it must not disarm the floor.
# Separately, a memory.swap.max bounded but far BELOW host swap is the exact
# blind spot that caused the field host's OOMs: the floor may terminate a
# round the kernel would have contained on a wider cap. Both are surfaced as
# fields on the SAME host_cgroup_memory_limit event -- never a new kind --
# and NEVER auto-change the operator's cgroup/unit.


def test_probe_and_emit_cgroup_defer_huge_swap_cap_stays_armed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A huge MemorySwapMax (>> host swap) can't bind before host-swap
    exhaustion, so the floor must stay ARMED (defer=False), matching the
    memory.max guard."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=300_000_000,
        memory_swap_max=10**12,  # 1TB, absurd
        mem_total=462_000_000,
        swap_total=1_600_000_000,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    assert _probe_and_emit_cgroup_defer(log_dir) is False


def test_advisory_field_when_swap_cap_far_below_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """swap.max at 12.5% of host swap (well under the 25% advisory floor)
    rides as fields on the EXISTING host_cgroup_memory_limit event -- not a
    new kind -- plus one stderr line. Never changes the cgroup/unit."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=256_000_000,
        memory_swap_max=200_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    _probe_and_emit_cgroup_defer(log_dir)

    [ev] = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    assert ev["event"] == "host_cgroup_memory_limit"
    assert ev["swap_total_bytes"] == 1_600_000_000
    assert ev["swap_cap_pct"] == 12.5
    assert ev["memory_high"] is None
    assert ev["advisory"] is not None  # advisory rides as a FIELD, not a new kind

    captured = capsys.readouterr()
    assert "swap.max is far below host swap" in captured.err


def test_memory_high_field_carries_real_value_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """memory_high on the emitted event is the REAL memory.high read, not a
    hardcoded None -- this is the field team's whole ask (can they see
    whether MemoryHigh is set). Fails against a hardcoded `memory_high=None`
    in _probe_and_emit_cgroup_defer even though metrics.cgroup_memory_high
    itself reports a finite value."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=256_000_000,
        memory_swap_max=1_280_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=192_000_000,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    _probe_and_emit_cgroup_defer(log_dir)

    [ev] = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    assert ev["memory_high"] == 192_000_000


def test_advisory_absent_when_swap_cap_within_host_swap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """swap.max at 80% of host swap is a plausible operator choice -- no
    advisory, no stderr line."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=256_000_000,
        memory_swap_max=1_280_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    _probe_and_emit_cgroup_defer(log_dir)

    [ev] = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    assert ev["swap_cap_pct"] == 80.0
    assert ev["advisory"] is None

    captured = capsys.readouterr()
    assert captured.err == ""
