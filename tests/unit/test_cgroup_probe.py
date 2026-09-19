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
import os
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
        memory_current: str | None = None,
    ) -> None:
        d = self._dir_for(cgroup_path)
        if memory_max is not None:
            (d / "memory.max").write_text(memory_max)
        if memory_swap_max is not None:
            (d / "memory.swap.max").write_text(memory_swap_max)
        if memory_high is not None:
            (d / "memory.high").write_text(memory_high)
        if memory_current is not None:
            (d / "memory.current").write_text(memory_current)

    def __call__(
        self,
        *,
        memory_max: str | None = None,
        memory_swap_max: str | None = None,
        memory_high: str | None = None,
        memory_current: str | None = None,
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
            memory_current=memory_current,
        )


@pytest.fixture
def fake_cgroup(tmp_path: Path) -> _FakeCgroup:
    return _FakeCgroup(tmp_path / "cgroup")


def test_cgroup_memory_limits_should_return_both_limits_when_both_finite(
    fake_cgroup: _FakeCgroup,
) -> None:
    """Exactly the field host: MemoryMax=320M + MemorySwapMax=160M -- both
    finite, so the (mem+swap) budget is bounded end to end."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160")  # 320M / 160M

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim == {
        "memory_max": 335544320,
        "memory_swap_max": 167772160,
        "cgroup_path": _LEAF,
        "bounding_cgroup_path": _LEAF,
    }


def test_cgroup_memory_limits_should_return_none_swap_max_when_swap_unlimited(
    fake_cgroup: _FakeCgroup,
) -> None:
    """`"max"` means unlimited -- systemd's MemoryMax-without-MemorySwapMax
    default shape, where the floor must stay armed."""
    fake_cgroup(memory_max="335544320", memory_swap_max="max")

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim["memory_max"] == 335544320
    assert lim["memory_swap_max"] is None


def test_cgroup_memory_limits_should_use_ancestor_limit_when_leaf_unlimited(
    fake_cgroup: _FakeCgroup,
) -> None:
    """A bounding systemd slice's memory.max constrains every scope nested
    beneath it -- the tightest ancestor wins, not just the leaf's own
    (unlimited) value."""
    fake_cgroup(memory_max="max", memory_swap_max="max")  # leaf: unlimited
    fake_cgroup.set_limit("/system.slice", memory_max="335544320", memory_swap_max="167772160")

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim["memory_max"] == 335544320
    assert lim["memory_swap_max"] == 167772160


def test_cgroup_memory_limits_should_pick_lower_of_leaf_and_ancestor_when_both_finite(
    fake_cgroup: _FakeCgroup,
) -> None:
    """When BOTH leaf and ancestor are finite, the MIN (tighter) value wins,
    regardless of which level set it."""
    fake_cgroup(memory_max="536870912")  # leaf: 512M
    fake_cgroup.set_limit("/system.slice", memory_max="335544320")  # parent: 320M (tighter)

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim["memory_max"] == 335544320


def test_cgroup_memory_limits_should_report_bounding_path_as_the_min_max_owner_when_resolved(
    fake_cgroup: _FakeCgroup,
) -> None:
    """The own leaf sets memory.max itself, so it's also the bounding
    ancestor -- ``bounding_cgroup_path`` equals ``cgroup_path``."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160")

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim["bounding_cgroup_path"] == lim["cgroup_path"]


def test_cgroup_memory_limits_should_report_bounding_path_as_the_ancestor_when_leaf_unlimited(
    fake_cgroup: _FakeCgroup,
) -> None:
    """The leaf itself is unlimited; a parent slice owns the real budget --
    ``bounding_cgroup_path`` names that ancestor, NOT the (looser) leaf, so a
    caller can tell an inherited bound from an own-scope one."""
    fake_cgroup(memory_max="max", memory_swap_max="max")
    fake_cgroup.set_limit("/system.slice", memory_max="335544320", memory_swap_max="167772160")

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim["bounding_cgroup_path"] == "/system.slice"
    assert lim["bounding_cgroup_path"] != lim["cgroup_path"]


def test_cgroup_memory_limits_should_return_all_none_when_cgroup_v1_or_missing(
    fake_cgroup: _FakeCgroup,
) -> None:
    """No `cgroup.controllers` (cgroup v1, or no unified hierarchy at all)
    means there's no reliable fixed-path budget file to read -- probe
    returns all-None rather than guessing."""
    fake_cgroup(v2=False)

    lim = metrics.cgroup_memory_limits(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert lim == {
        "memory_max": None,
        "memory_swap_max": None,
        "cgroup_path": None,
        "bounding_cgroup_path": None,
    }


def test_cgroup_memory_limits_should_parse_self_cgroup_from_proc_file(tmp_path: Path) -> None:
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
        "bounding_cgroup_path": "/system.slice/example.service",
    }


def test_cgroup_memory_limits_should_return_all_none_when_proc_self_cgroup_missing(
    tmp_path: Path,
) -> None:
    """No `0::` line (a pure v1 /proc/self/cgroup, or the file is missing
    entirely) -- can't resolve the v2 path, so all-None."""
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.controllers").write_text("memory\n")

    lim = metrics.cgroup_memory_limits(root=root, proc_self_cgroup=tmp_path / "does-not-exist")

    assert lim == {
        "memory_max": None,
        "memory_swap_max": None,
        "cgroup_path": None,
        "bounding_cgroup_path": None,
    }


# --- 0.2.18 T1c fix round 1: cgroup_memory_high, a REAL memory.high read ---
#
# memory_high shipped in fix round 1 as a hardcoded None on every
# host_cgroup_memory_limit emit -- a permanently-dead field. These exercise
# the actual bounding-ancestor read against a fake cgroup tree, the same way
# the memory_max/memory_swap_max tests above do.


def test_cgroup_memory_high_should_return_finite_value_when_set(fake_cgroup: _FakeCgroup) -> None:
    """A finite memory.high (MemoryHigh= set in the unit) reads as the real
    int value -- this is the field team's whole ask: can they see it's set."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160", memory_high="268435456")

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high == 268435456


def test_cgroup_memory_high_should_return_none_when_unset(fake_cgroup: _FakeCgroup) -> None:
    """The literal `"max"` (MemoryHigh unset, systemd's default) means
    unset -- None, never the raw "max" token."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160", memory_high="max")

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high is None


def test_cgroup_memory_high_should_return_none_when_file_missing(
    fake_cgroup: _FakeCgroup,
) -> None:
    """No memory.high file at all (older kernel, or just never written by
    this fixture) -- also None, not an error."""
    fake_cgroup(memory_max="335544320", memory_swap_max="167772160")

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high is None


def test_cgroup_memory_high_should_use_tighter_ancestor_value_when_leaf_looser(
    fake_cgroup: _FakeCgroup,
) -> None:
    """Same bounding-ancestor MIN-FINITE walk as memory.max: a tighter
    memory.high on an ancestor slice wins over the leaf's own looser value."""
    fake_cgroup(memory_high="536870912")  # leaf: 512M
    fake_cgroup.set_limit("/system.slice", memory_high="268435456")  # parent: 256M (tighter)

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high == 268435456


def test_cgroup_memory_high_should_return_none_when_cgroup_v1_or_missing(
    fake_cgroup: _FakeCgroup,
) -> None:
    """No cgroup v2 at all -- None, same as the other probes."""
    fake_cgroup(v2=False)

    high = metrics.cgroup_memory_high(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert high is None


def test_mem_total_bytes_should_match_psutil_virtual_memory_total() -> None:
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
    memory_swap_max: int | None,
    mem_total: int,
    swap_total: int,
    memory_high: int | None = None,
    cgroup_path: str | None = "/x",
    bounding_cgroup_path: str | None = "/x",
    delegated: bool | None = None,
):
    monkeypatch.setattr(
        metrics,
        "cgroup_memory_limits",
        lambda: {
            "memory_max": memory_max,
            "memory_swap_max": memory_swap_max,
            "cgroup_path": cgroup_path,
            "bounding_cgroup_path": bounding_cgroup_path,
        },
    )
    monkeypatch.setattr(metrics, "mem_total_bytes", lambda: mem_total)
    monkeypatch.setattr(metrics, "swap_total_bytes", lambda: swap_total)
    monkeypatch.setattr(metrics, "cgroup_memory_high", lambda **_k: memory_high)
    monkeypatch.setattr(metrics, "cgroup_delegated", lambda **_k: delegated)


@pytest.mark.parametrize(
    "memory_max, memory_swap_max, mem_total, swap_total, expected",
    [
        pytest.param(
            1024 * 1024 * 1024,  # 1G "limit"
            512 * 1024 * 1024,
            462 * 1024 * 1024,  # 462MB host
            1024 * 1024 * 1024,  # plausible swap cap -- memory_max is the implausible one
            False,
            id="memory_max_exceeds_host_total",
        ),
        pytest.param(
            335544320,  # 320M
            167772160,  # 160M
            2 * 1024 * 1024 * 1024,  # 2G host RAM
            2 * 1024 * 1024 * 1024,  # 2G host swap -- 160M cap is well within it
            True,
            id="both_limits_plausible",
        ),
        pytest.param(
            300_000_000,
            10**12,  # 1TB, absurd
            462_000_000,
            1_600_000_000,
            False,
            id="swap_cap_exceeds_host_swap",
        ),
    ],
)
def test_probe_and_emit_cgroup_defer_should_gate_on_limit_plausibility(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    memory_max: int,
    memory_swap_max: int,
    mem_total: int,
    swap_total: int,
    expected: bool,
) -> None:
    """The floor only defers to cgroup-OOM when BOTH memory.max and
    memory.swap.max are finite AND each is plausible against its own host
    total -- either one alone exceeding its host total (a copy-pasted
    MemoryMax=1G on a 462MB host, or an absurd 1TB MemorySwapMax) keeps the
    floor ARMED, the same way the field host's own both-finite-and-tighter
    MemoryMax=320M + MemorySwapMax=160M shape defers."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=memory_max,
        memory_swap_max=memory_swap_max,
        mem_total=mem_total,
        swap_total=swap_total,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    assert _probe_and_emit_cgroup_defer(log_dir) is expected


@pytest.mark.parametrize(
    "memory_swap_max, expected",
    [
        pytest.param(0, True, id="swap_max_zero"),
        pytest.param(None, False, id="swap_max_missing"),
    ],
)
def test_probe_and_emit_cgroup_defer_should_carry_defer_matching_return(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    memory_swap_max: int | None,
    expected: bool,
) -> None:
    """host_cgroup_memory_limit.defer equals the probe return: True when
    MemorySwapMax=0 (finite and plausible), False when swap.max is missing."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=150_000_000,
        memory_swap_max=memory_swap_max,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    assert _probe_and_emit_cgroup_defer(log_dir) is expected
    assert _only_event(tmp_path)["defer"] is expected


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


@pytest.mark.parametrize(
    "memory_swap_max, expected_swap_cap_pct, expect_advisory, expected_stderr_substring",
    [
        pytest.param(
            200_000_000, 12.5, True, "swap.max is far below host swap", id="far_below_host"
        ),
        pytest.param(
            1_280_000_000, 80.0, True, "memory.high", id="within_host_swap_but_memory_high_unset"
        ),
    ],
)
def test_probe_and_emit_cgroup_defer_should_gate_advisory_on_swap_cap_pct(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    memory_swap_max: int,
    expected_swap_cap_pct: float,
    expect_advisory: bool,
    expected_stderr_substring: str | None,
) -> None:
    """swap.max below the 25% advisory floor (here 12.5% of host swap) rides
    as fields on the EXISTING host_cgroup_memory_limit event -- not a new
    kind -- plus one stderr line; a plausible cap (80%) gets no swap-cap
    advisory, but BOTH cases still leave memory.high unset on the own leaf,
    so both also carry the memory.high hint (joined onto the swap-cap one
    for the far-below-host case). Never changes the cgroup/unit either way."""
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(
        monkeypatch,
        memory_max=256_000_000,
        memory_swap_max=memory_swap_max,
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
    assert ev["swap_cap_pct"] == expected_swap_cap_pct
    assert ev["memory_high"] is None
    assert (ev["advisory"] is not None) is expect_advisory
    assert "memory.high" in ev["advisory"]

    captured = capsys.readouterr()
    if expected_stderr_substring is None:
        assert captured.err == ""
    else:
        assert expected_stderr_substring in captured.err


def test_probe_and_emit_cgroup_defer_should_emit_real_memory_high_when_set(
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


# --- 0.2.24 T1: the memory.high advisory, own-scope-gated + defer-aware ---
#
# memory.max set (anywhere on the ancestor chain) without memory.high means
# the operator has a hard kill ceiling but no soft pre-OOM throttle -- worth
# flagging. But cgroup_memory_limits reports memory.max as the MIN across
# ALL ancestors, so "set" can also mean an inherited parent slice or a
# container root, where "add MemoryHigh" is noise the operator can't act on
# from their own unit. The hint fires ONLY when the bounding ancestor IS the
# operator's own leaf (bounding_cgroup_path == cgroup_path), and only
# appends the PSI-floor swap caveat when the mid-round floor isn't already
# deferring to kernel cgroup-OOM. Advisory only -- never writes a cgroup or
# unit file.


def _run_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs) -> None:
    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _patch_probe(monkeypatch, **kwargs)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _probe_and_emit_cgroup_defer(log_dir)


def _only_event(tmp_path: Path) -> dict:
    log_dir = tmp_path / "logs"
    [ev] = [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
    ]
    return ev


def test_advisory_should_not_hint_memory_high_when_bound_is_an_inherited_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # memory.max finite but owned by a PARENT slice, not the operator's leaf -> no hint

    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=256_000_000,
        memory_swap_max=1_280_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=None,
        bounding_cgroup_path="/system.slice",
    )

    ev = _only_event(tmp_path)
    assert ev["advisory"] is None


def test_advisory_should_omit_memory_high_hint_when_high_already_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # high already set -> no hint (and no swap-cap advisory here either)

    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=256_000_000,
        memory_swap_max=1_280_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=224_000_000,
    )

    ev = _only_event(tmp_path)
    assert ev["advisory"] is None


def test_advisory_memory_high_hint_should_omit_swap_caveat_when_already_deferring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # both-finite + plausible -> floor defers -> hint fires WITHOUT the "bound swap" caveat

    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=256_000_000,
        memory_swap_max=1_280_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=None,
    )

    ev = _only_event(tmp_path)
    assert "memory.high" in ev["advisory"]
    assert "swap" not in ev["advisory"].lower()


def test_advisory_memory_high_hint_should_include_swap_caveat_when_not_deferring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # memory.max bounded but memory.swap.max UNBOUNDED -> floor stays armed (no defer) -> caveat

    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=256_000_000,
        memory_swap_max=None,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=None,
    )

    ev = _only_event(tmp_path)
    assert "memory.high" in ev["advisory"]
    assert "swap" in ev["advisory"].lower()


def test_advisory_should_be_none_when_cgroup_v2_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # no cgroup v2 -> memory_max/cgroup_path/bounding_cgroup_path all None -> own_scope stays inert

    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=None,
        memory_swap_max=None,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=None,
        cgroup_path=None,
        bounding_cgroup_path=None,
    )

    ev = _only_event(tmp_path)
    assert ev["advisory"] is None


# --- v0.3.3 T1: cgroup_delegated, a READ-ONLY delegation-readiness probe ---
#
# A future release needs to know, before it may ever write memory.high on a
# round-scoped nested cgroup, whether THIS process's own cgroup v2 leaf is
# actually delegated to it (systemd's Delegate=yes): uid-owned AND both
# cgroup.procs and memory.high writable. This probe only ever ANSWERS that
# question -- it never itself writes anything, tested via os.access/stat
# against the existing directory, never a trial write.


def test_cgroup_delegated_should_return_none_when_cgroup_v2_unavailable(
    fake_cgroup: _FakeCgroup,
) -> None:
    fake_cgroup(v2=False)

    result = metrics.cgroup_delegated(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert result is None


def test_cgroup_delegated_should_return_false_when_leaf_uid_differs_from_process_uid(
    fake_cgroup: _FakeCgroup, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cgroup(memory_max="335544320")
    leaf_dir = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf_dir / "cgroup.procs").touch()
    (leaf_dir / "memory.high").touch()
    # metrics.os IS the stdlib os module (same object this file imported), so
    # patching metrics.os.getuid replaces os.getuid globally -- capture the
    # real uid as a plain value first, or a lambda calling os.getuid() would
    # recurse into its own patched self.
    real_uid = os.getuid()
    monkeypatch.setattr(metrics.os, "getuid", lambda: real_uid + 1)

    result = metrics.cgroup_delegated(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert result is False


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file-permission checks")
def test_cgroup_delegated_should_return_false_when_leaf_files_not_writable(
    fake_cgroup: _FakeCgroup,
) -> None:
    fake_cgroup(memory_max="335544320")
    leaf_dir = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf_dir / "cgroup.procs").touch()
    (leaf_dir / "memory.high").touch()
    os.chmod(leaf_dir / "cgroup.procs", 0o444)
    os.chmod(leaf_dir / "memory.high", 0o444)

    result = metrics.cgroup_delegated(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert result is False


def test_cgroup_delegated_should_return_true_when_uid_owned_and_both_files_writable(
    fake_cgroup: _FakeCgroup,
) -> None:
    fake_cgroup(memory_max="335544320")
    leaf_dir = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf_dir / "cgroup.procs").touch()
    (leaf_dir / "memory.high").touch()

    result = metrics.cgroup_delegated(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert result is True


def test_cgroup_delegated_should_return_none_when_leaf_stat_fails(
    fake_cgroup: _FakeCgroup, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cgroup(memory_max="335544320")
    leaf_dir = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf_dir / "cgroup.procs").touch()
    (leaf_dir / "memory.high").touch()
    real_stat = Path.stat

    def _raise_for_leaf(self: Path, *args, **kwargs):
        if self == leaf_dir:
            raise OSError("permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _raise_for_leaf)

    result = metrics.cgroup_delegated(root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup)

    assert result is None


# --- v0.3.3 T1: cgroup_delegated wired onto host_cgroup_memory_limit + advisory ---
#
# The probe above is pure metrics; these exercise _probe_and_emit_cgroup_defer
# actually calling it, carrying the result on the SAME host_cgroup_memory_limit
# event (never a new kind), and firing the undelegated advisory only for the
# supervisor's OWN leaf when a memory bound already exists.


def test_probe_and_emit_cgroup_defer_should_carry_cgroup_delegated_on_every_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=None,
        memory_swap_max=None,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        cgroup_path=None,
        bounding_cgroup_path=None,
        delegated=None,
    )

    assert _only_event(tmp_path)["cgroup_delegated"] is None


def test_probe_and_emit_cgroup_defer_should_report_delegated_false_when_leaf_not_delegated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=256_000_000,
        memory_swap_max=1_280_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=192_000_000,
        delegated=False,
    )

    assert _only_event(tmp_path)["cgroup_delegated"] is False


def test_advisory_should_hint_undelegated_when_not_delegated_and_bound_already_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # memory_high already set -> own_scope's "add memory.high" hint stays silent,
    # so the ONLY advisory possible here is the new undelegated hint.

    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=256_000_000,
        memory_swap_max=1_280_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=192_000_000,
        delegated=False,
    )

    ev = _only_event(tmp_path)
    assert ev["advisory"] == (
        "memory.high on this cgroup is not writable by this process; the soft-brake "
        "is inert. Run serve as a user-mode unit (`systemctl --user` + `loginctl "
        "enable-linger`) or as a root system unit."
    )


def test_advisory_should_omit_undelegated_hint_when_already_delegated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=256_000_000,
        memory_swap_max=1_280_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=192_000_000,
        delegated=True,
    )

    ev = _only_event(tmp_path)
    assert ev["advisory"] is None


def test_advisory_should_omit_undelegated_hint_when_cgroup_v2_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=None,
        memory_swap_max=None,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        cgroup_path=None,
        bounding_cgroup_path=None,
        delegated=None,
    )

    ev = _only_event(tmp_path)
    assert ev["advisory"] is None


def test_advisory_should_join_swap_and_undelegated_hints_with_semicolon_when_both_fire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # swap_cap_pct = 200_000_000 / 1_600_000_000 * 100 = 12.5% -> below the 25%
    # advisory floor, so the swap-caveat clause ALSO fires alongside the new one.

    _run_probe(
        monkeypatch,
        tmp_path,
        memory_max=256_000_000,
        memory_swap_max=200_000_000,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=192_000_000,
        delegated=False,
    )

    ev = _only_event(tmp_path)
    assert "swap.max is far below host swap" in ev["advisory"]
    assert "not writable by this process" in ev["advisory"]
    assert "; " in ev["advisory"]


@pytest.mark.parametrize(
    "undelegated_state",
    [False, None],
    ids=["not_delegated", "delegation_unknown"],
)
def test_advisory_should_announce_brake_inert_when_brake_on_and_not_confirmed_delegated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, undelegated_state: bool | None
) -> None:
    """The brake-inert announce is triggered by brake_memory_high ALONE --
    no pre-existing memory.high or own_scope hint needed -- so this isolates
    it from the pre-existing (delegated is False AND memory_high-or-own_scope)
    trigger by leaving memory_high unset and memory_max unset (own_scope
    False). It fires for BOTH delegated=False (confirmed not delegated) and
    delegated=None (unknown, e.g. the leaf's own os.stat() failed) -- either
    way the brake can't confirm it can write memory.high."""
    _patch_probe(
        monkeypatch,
        memory_max=None,
        memory_swap_max=None,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=None,
        delegated=undelegated_state,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _probe_and_emit_cgroup_defer(log_dir, brake_memory_high=True)

    assert "not writable by this process" in _only_event(tmp_path)["advisory"]


# --- 0.3.7 Task 3: the leaf memory.high soft-brake read/stash/write/restore ---
#
# The brake writes ONLY serve's OWN resolved leaf cgroup -- never an
# ancestor. _brake_high_value is the pure arithmetic (floor/clamp/min-current
# guard); engage_leaf_memory_high/restore_leaf_memory_high do the actual
# reversible file I/O, fail-OPEN on any OSError.


def test_brake_high_value_should_step_below_current_when_current_large() -> None:
    current = 300 * 1024 * 1024

    assert metrics._brake_high_value(current, 10) == int(current * 0.9)


def test_brake_high_value_should_cap_at_current_when_step_pct_zero() -> None:
    current = 300 * 1024 * 1024

    # step_pct=0 = cap-at-current: write memory.high == memory.current, so the
    # kernel throttles further growth without a synchronous reclaim burst below
    # current (the gentle mode for SD-backed latency-sensitive hosts).
    assert metrics._brake_high_value(current, 0) == current


def test_brake_high_value_should_floor_at_64mib_when_step_would_go_lower() -> None:
    current = 130 * 1024 * 1024  # just over the 128MiB engage floor; 10% off = ~117MiB, fine

    assert metrics._brake_high_value(60 * 1024 * 1024 + 1, 10) is None  # below engage floor
    assert metrics._brake_high_value(current, 90) == 64 * 1024 * 1024  # clamp up to the 64MiB floor


def test_brake_high_value_should_return_none_when_current_below_engage_floor() -> None:
    assert metrics._brake_high_value(127 * 1024 * 1024, 10) is None


def test_engage_leaf_memory_high_should_write_leaf_and_stash_leaf_prior_when_delegated(
    fake_cgroup: _FakeCgroup,
) -> None:
    fake_cgroup(memory_high="536870912")  # leaf's own memory.high = 512M (the stash target)
    leaf = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf / "memory.current").write_text(str(300 * 1024 * 1024))
    fake_cgroup.set_limit("/system.slice", memory_high="805306368")  # ancestor: MUST NOT be touched

    result = metrics.engage_leaf_memory_high(
        step_pct=10, root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert result["engaged"] is True
    assert result["previous"] == "536870912"
    assert result["written"] == int(300 * 1024 * 1024 * 0.9)
    assert (leaf / "memory.high").read_text().strip() == str(result["written"])
    assert (fake_cgroup.root / "system.slice" / "memory.high").read_text().strip() == "805306368"


def test_engage_leaf_memory_high_should_not_engage_when_leaf_current_below_floor(
    fake_cgroup: _FakeCgroup,
) -> None:
    fake_cgroup(memory_high="max")
    leaf = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf / "memory.current").write_text(str(100 * 1024 * 1024))  # below 128MiB engage floor

    result = metrics.engage_leaf_memory_high(
        step_pct=10, root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert result == {}
    assert (leaf / "memory.high").read_text().strip() == "max"  # untouched


def test_engage_leaf_memory_high_should_skip_engage_when_existing_high_is_tighter_than_target(
    fake_cgroup: _FakeCgroup,
) -> None:
    """The operator already runs a tighter MemoryHigh=200M than our computed
    target (~270MiB from a 300MiB current at step_pct=10) -- engaging would
    LOOSEN the operator's own throttle, so the brake must skip entirely."""
    fake_cgroup(memory_high="209715200")  # 200MiB
    leaf = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf / "memory.current").write_text(str(300 * 1024 * 1024))

    result = metrics.engage_leaf_memory_high(
        step_pct=10, root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert result == {}
    assert (leaf / "memory.high").read_text().strip() == "209715200"  # untouched


def test_engage_leaf_memory_high_should_lower_high_when_existing_high_is_looser_than_target(
    fake_cgroup: _FakeCgroup,
) -> None:
    """The operator's existing MemoryHigh=400M is looser than our computed
    target (~270MiB) -- the brake still engages and tightens it."""
    fake_cgroup(memory_high="419430400")  # 400MiB
    leaf = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf / "memory.current").write_text(str(300 * 1024 * 1024))

    result = metrics.engage_leaf_memory_high(
        step_pct=10, root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert result["engaged"] is True
    assert result["previous"] == "419430400"
    assert result["written"] == int(300 * 1024 * 1024 * 0.9)
    assert result["written"] < 400 * 1024 * 1024
    assert (leaf / "memory.high").read_text().strip() == str(result["written"])


def test_engage_leaf_memory_high_should_brake_from_max_high(
    fake_cgroup: _FakeCgroup,
) -> None:
    """No pre-existing finite high (``"max"``, unset) -- the monotone-clamp
    skip never applies, so the brake engages exactly as before."""
    fake_cgroup(memory_high="max")
    leaf = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf / "memory.current").write_text(str(300 * 1024 * 1024))

    result = metrics.engage_leaf_memory_high(
        step_pct=10, root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert result["engaged"] is True
    assert result["written"] == int(300 * 1024 * 1024 * 0.9)
    assert (leaf / "memory.high").read_text().strip() == str(result["written"])


def test_restore_leaf_memory_high_should_write_back_stashed_max_token(
    fake_cgroup: _FakeCgroup,
) -> None:
    fake_cgroup(memory_high="123456789")  # engage overwrote it; restore must put "max" back
    leaf = fake_cgroup.root / _LEAF.lstrip("/")

    ok = metrics.restore_leaf_memory_high(
        "max", root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert ok is True
    assert (leaf / "memory.high").read_text().strip() == "max"


def test_engage_leaf_memory_high_should_fail_open_when_write_raises_oserror(
    fake_cgroup: _FakeCgroup, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cgroup(memory_high="536870912")
    leaf = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf / "memory.current").write_text(str(300 * 1024 * 1024))
    import errno as errno_mod

    def _raise(*_a, **_k):
        raise OSError(errno_mod.EACCES, "denied")

    monkeypatch.setattr(metrics.os, "open", _raise)

    result = metrics.engage_leaf_memory_high(
        step_pct=10, root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert result == {"engaged": False, "errno": errno_mod.EACCES}


def test_engage_leaf_memory_high_should_fail_open_and_close_fd_when_write_raises_oserror(
    fake_cgroup: _FakeCgroup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the try/finally around os.write: an OSError raised
    DURING the write (not at open) must still close the fd (no leak) via the
    inner try/finally, then fail open through the outer except -- distinct
    from the open-raises test above, which never gets a real fd at all."""
    fake_cgroup(memory_high="536870912")
    leaf = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf / "memory.current").write_text(str(300 * 1024 * 1024))
    import errno as errno_mod

    real_close = metrics.os.close
    closed_fds: list[int] = []

    def _raise_write(*_a, **_k):
        raise OSError(errno_mod.ENOSPC, "no space left on device")

    def _spy_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(metrics.os, "write", _raise_write)
    monkeypatch.setattr(metrics.os, "close", _spy_close)

    result = metrics.engage_leaf_memory_high(
        step_pct=10, root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert result == {"engaged": False, "errno": errno_mod.ENOSPC}
    assert len(closed_fds) == 1  # the fd os.open() returned was closed, not leaked


def test_engage_leaf_memory_high_should_only_ever_write_the_resolved_leaf_never_ancestor(
    fake_cgroup: _FakeCgroup,
) -> None:
    """The ONE hard safety invariant: engage writes the resolved leaf, never
    an ancestor -- even the cgroup ROOT itself, one level above /system.slice."""
    fake_cgroup(memory_high="536870912")
    leaf = fake_cgroup.root / _LEAF.lstrip("/")
    (leaf / "memory.current").write_text(str(300 * 1024 * 1024))
    fake_cgroup.set_limit("/", memory_high="999999999")
    fake_cgroup.set_limit("/system.slice", memory_high="888888888")

    metrics.engage_leaf_memory_high(
        step_pct=25, root=fake_cgroup.root, self_cgroup=fake_cgroup.self_cgroup
    )

    assert (fake_cgroup.root / "memory.high").read_text().strip() == "999999999"
    assert (fake_cgroup.root / "system.slice" / "memory.high").read_text().strip() == "888888888"
    assert (leaf / "memory.high").read_text().strip() == str(int(300 * 1024 * 1024 * 0.75))


def test_advisory_should_omit_brake_inert_announce_when_brake_off_and_undelegated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The counterpart of the isolating case above: same undelegated/unknown
    leaf, same absent memory_high/own_scope, but brake_memory_high defaults
    False (the operator never asked for the brake) -- no advisory at all."""
    _patch_probe(
        monkeypatch,
        memory_max=None,
        memory_swap_max=None,
        mem_total=462_000_000,
        swap_total=1_600_000_000,
        memory_high=None,
        delegated=False,
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    from agent_runner.cli._serve_cgroup import _probe_and_emit_cgroup_defer

    _probe_and_emit_cgroup_defer(log_dir)

    assert _only_event(tmp_path)["advisory"] is None
