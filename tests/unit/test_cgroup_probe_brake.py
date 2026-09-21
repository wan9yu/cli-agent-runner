"""Leaf memory.high soft-brake read/stash/write/restore (0.3.7 Task 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import metrics
from tests.unit.test_cgroup_probe import _LEAF, _FakeCgroup


@pytest.fixture
def fake_cgroup(tmp_path: Path) -> _FakeCgroup:
    return _FakeCgroup(tmp_path / "cgroup")


# --- 0.3.7 Task 3: the leaf memory.high soft-brake read/stash/write/restore ---
#
# The brake writes ONLY serve's OWN resolved leaf cgroup -- never an
# ancestor. _brake_high_value is the pure arithmetic (floor/clamp/min-current
# guard); engage_leaf_memory_high/restore_leaf_memory_high do the actual
# reversible file I/O, fail-OPEN on any OSError.


def test_brake_high_value_should_step_below_current_when_current_large() -> None:

    current = 300 * 1024 * 1024

    actual = metrics._brake_high_value(current, 10)

    assert actual == int(current * 0.9)


def test_brake_high_value_should_cap_at_current_when_step_pct_zero() -> None:

    current = 300 * 1024 * 1024

    actual = metrics._brake_high_value(current, 0)

    assert actual == current


def test_brake_high_value_should_floor_at_64mib_when_step_would_go_lower() -> None:
    current = 130 * 1024 * 1024  # just over the 128MiB engage floor; 10% off = ~117MiB, fine

    assert metrics._brake_high_value(60 * 1024 * 1024 + 1, 10) is None  # below engage floor

    assert metrics._brake_high_value(current, 90) == 64 * 1024 * 1024  # clamp up to the 64MiB floor


def test_brake_high_value_should_return_none_when_current_below_engage_floor() -> None:

    actual = metrics._brake_high_value(127 * 1024 * 1024, 10)

    expected = None

    assert actual is expected


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


def test_engage_leaf_memory_high_should_brake_from_max_high_when_invoked(
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


def test_restore_leaf_memory_high_should_write_back_stashed_max_token_when_invoked(
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


def test_engage_leaf_memory_high_should_ever_write_the_resolved_leaf_never_ancestor_when_invoked(
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
