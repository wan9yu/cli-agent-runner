"""cgroup_memory_usage: per-round pressure read at the bounding ancestor
(the same ancestor cgroup_memory_limits picks for memory_max), returning
memory.current/memory.swap.current + memory.events as ABSOLUTE counters
(callers diff two reads for a per-round delta)."""

from pathlib import Path

from agent_runner import metrics


def _tree(root):
    (root / "cgroup.controllers").write_text("memory\n")
    slice_dir = root / "user.slice"
    slice_dir.mkdir()
    (slice_dir / "memory.max").write_text("320000000\n")  # bounding ancestor
    (slice_dir / "memory.current").write_text("300000000\n")
    (slice_dir / "memory.swap.current").write_text("100000000\n")
    (slice_dir / "memory.events").write_text("low 0\nhigh 12\nmax 3\noom 1\noom_kill 2\n")
    leaf = slice_dir / "app.scope"
    leaf.mkdir()
    (leaf / "memory.max").write_text("max\n")  # unbounded leaf -> ancestor wins


def test_cgroup_memory_usage_should_return_bounding_ancestor_usage_when_leaf_unbounded(tmp_path):
    _tree(tmp_path)

    usage = metrics.cgroup_memory_usage(root=tmp_path, self_cgroup="/user.slice/app.scope")

    assert usage["memory_current"] == 300000000
    assert usage["memory_swap_current"] == 100000000
    assert usage["memory_events"] == {"high": 12, "max": 3, "oom": 1, "oom_kill": 2}
    assert usage["cgroup_path"].endswith("user.slice")


def test_cgroup_memory_usage_should_be_empty_when_unbounded(tmp_path):
    (tmp_path / "cgroup.controllers").write_text("memory\n")

    assert metrics.cgroup_memory_usage(root=tmp_path, self_cgroup="/") == {}


def test_cgroup_memory_usage_should_be_empty_when_no_cgroup_v2(tmp_path):
    assert metrics.cgroup_memory_usage(root=tmp_path, self_cgroup="/") == {}


def test_cgroup_memory_usage_should_read_cached_bounding_cgroup_path_when_provided(tmp_path):
    """self_cgroup/proc_self_cgroup are irrelevant here and left at their
    defaults -- a caller's own earlier-resolved ancestor (e.g. a mid-round
    tick reusing _spawn_round's round-start read) skips the ancestor walk
    entirely and reads straight from the given path."""
    _tree(tmp_path)

    usage = metrics.cgroup_memory_usage(root=tmp_path, bounding_cgroup="/user.slice")

    assert usage["memory_current"] == 300000000
    assert usage["memory_swap_current"] == 100000000
    assert usage["memory_events"] == {"high": 12, "max": 3, "oom": 1, "oom_kill": 2}
    assert usage["cgroup_path"] == "/user.slice"


def test_cgroup_memory_usage_should_be_empty_when_cached_bounding_cgroup_vanished(tmp_path):
    """A cached ``bounding_cgroup`` whose directory no longer exists (renamed
    or removed mid-round) must report the same "can no longer tell" ``{}``
    the un-cached path returns when nothing bounds the process -- NOT a
    truthy all-zero dict, which would let ``_emit_round_cgroup_memory`` emit
    a misleading zero-pressure delta against a stale path (the bug this
    guard fixes: the fast path used to skip straight to reading
    memory.current/memory.swap.current/memory.events, all of which report
    0/{} for a MISSING path, without ever checking the directory itself
    still exists)."""
    (tmp_path / "cgroup.controllers").write_text("memory\n")

    # Deliberately no "vanished.slice" dir under tmp_path.
    assert metrics.cgroup_memory_usage(root=tmp_path, bounding_cgroup="/vanished.slice") == {}


def test_cgroup_memory_usage_should_fail_open_when_os_error_raised(tmp_path, monkeypatch):
    """A non-ENOENT stat error (EACCES/EIO on a flaky sysfs) must not
    propagate: this runs inside _spawn_round's mid-round tick loop, whose
    surrounding `except BaseException: _terminate_round(proc); raise` would
    otherwise terminate the round AND crash serve over an observability read.
    Path.exists() only swallows a narrow ENOENT/ENOTDIR/EBADF/ELOOP set and
    re-raises everything else, so this is a real, reachable failure mode."""

    def _raise(self):
        raise OSError("simulated EIO")

    monkeypatch.setattr(Path, "exists", _raise)

    assert metrics.cgroup_memory_usage(root=tmp_path, self_cgroup="/") == {}
