"""Parity + fallback + override tests for the entry_points.txt scanner.

The scanner (agent_runner._plugin_scan) replaces a per-group
importlib.metadata.entry_points() call at package-import time with one cheap
parse of installed dist-info entry_points.txt files. Its discovery contract
must match importlib.metadata.entry_points() exactly -- these tests pin that,
plus the hard fallback and the env override that forces the old path.
"""

from __future__ import annotations

import sys
from importlib.metadata import entry_points

import pytest

from agent_runner import _HOOK_GROUPS, _plugin_scan


def _md(group: str) -> list[tuple[str, str]]:
    return sorted((ep.name, ep.value) for ep in entry_points(group=group))


def test_scanner_matches_importlib_metadata_per_group():
    """scanner(sys.path, group) == entry_points(group) for every group we load --
    run on the dev venv's real (possibly stale) dist-info + in CI."""
    groups = (*_HOOK_GROUPS, "agent_runner.event_kinds", "agent_runner.detectors")
    for group in groups:
        scanned = sorted(_plugin_scan.scan_entry_points(sys.path, group))
        assert scanned == _md(group), f"parity drift in {group}: {scanned} != {_md(group)}"


def test_scanner_hard_falls_back_to_metadata_on_parse_error(monkeypatch):
    def boom(*a, **k):
        raise ValueError("corrupt entry_points.txt")

    monkeypatch.setattr(_plugin_scan, "_parse_entry_points_files", boom)
    # must not raise -- falls back to importlib.metadata
    out = _plugin_scan.scan_entry_points(sys.path, "agent_runner.post_round_hooks")
    assert ("pi_error_detector", "agent_runner.builtin_plugins.pi:PiErrorDetector") in out


def test_env_override_forces_metadata_path_without_touching_the_scan(monkeypatch):
    """AGENT_RUNNER_PLUGIN_DISCOVERY=metadata must skip the file scan entirely,
    not merely fall back to it after a failed attempt."""
    monkeypatch.setenv("AGENT_RUNNER_PLUGIN_DISCOVERY", "metadata")

    def must_not_run(*a, **k):
        raise AssertionError("scan should not run when the env override is set")

    monkeypatch.setattr(_plugin_scan, "_parse_entry_points_files", must_not_run)
    out = _plugin_scan.scan_entry_points(sys.path, "agent_runner.post_round_hooks")
    assert ("pi_error_detector", "agent_runner.builtin_plugins.pi:PiErrorDetector") in out


def test_env_override_absent_uses_the_scan(monkeypatch):
    """Sanity check for the previous test: without the override, the real scan
    path IS exercised (and still agrees with importlib.metadata)."""
    monkeypatch.delenv("AGENT_RUNNER_PLUGIN_DISCOVERY", raising=False)
    scanned = sorted(_plugin_scan.scan_entry_points(sys.path, "agent_runner.post_round_hooks"))
    assert scanned == _md("agent_runner.post_round_hooks")


def _write_dup_dist_info(tmp_path, name="dup_name", group="agent_runner.post_round_hooks"):
    """Two dist-info dirs on separate sys.path entries declaring the same
    ``name`` in the same ``group`` with DIFFERENT targets. Returns the two
    sys.path entries in winner-first order."""
    site1 = tmp_path / "site1"
    site2 = tmp_path / "site2"
    for site, target in (
        (site1, "pkg_a.mod:First"),
        (site2, "pkg_b.mod:Second"),
    ):
        dist_info = site / "somepkg-1.0.dist-info"
        dist_info.mkdir(parents=True)
        (dist_info / "entry_points.txt").write_text(f"[{group}]\n{name} = {target}\n")
    return [str(site1), str(site2)]


def test_scanner_dedups_by_name_first_sys_path_entry_wins(tmp_path):
    """Two dist-info dirs on different sys.path entries declaring the same
    plugin name in the same group: the earlier sys.path entry wins, and the
    name is not returned twice."""
    sys_path = _write_dup_dist_info(tmp_path)
    with pytest.warns(UserWarning):  # the collision itself is asserted below
        out = _plugin_scan.scan_entry_points(sys_path, "agent_runner.post_round_hooks")
    matches = [pair for pair in out if pair[0] == "dup_name"]
    assert matches == [("dup_name", "pkg_a.mod:First")]


def test_scanner_warns_when_dropping_a_duplicate_entry_point_name(tmp_path):
    """A dropped duplicate must stay operator-visible: the OLD importlib.metadata
    + ensure_unique path surfaced a UserWarning naming the plugin when two
    dist-infos declared the same entry_point name in the same group. The
    scanner's own first-sys.path-wins dedup must not silently swallow that
    signal -- it has to warn, naming the group, the duplicated name, and which
    value won."""
    sys_path = _write_dup_dist_info(tmp_path, name="dup_name", group="agent_runner.detectors")

    with pytest.warns(UserWarning) as caught:
        out = _plugin_scan.scan_entry_points(sys_path, "agent_runner.detectors")

    assert [pair for pair in out if pair[0] == "dup_name"] == [("dup_name", "pkg_a.mod:First")]
    messages = [str(w.message) for w in caught.list]
    assert any(
        "dup_name" in m and "agent_runner.detectors" in m and "pkg_b.mod:Second" in m
        for m in messages
    ), (
        f"expected a warning naming the group, the duplicate name, and the dropped "
        f"value; got {messages}"
    )


def test_scanner_discovers_third_party_style_dist_info(tmp_path):
    """A plugin registered via [project.entry-points] in an installed dist
    (simulated here as a bare dist-info dir on a synthetic sys.path entry)
    must still be discovered."""
    dist_info = tmp_path / "thirdparty_plugin-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "entry_points.txt").write_text(
        "[agent_runner.detectors]\nthird_party_detector = thirdparty_plugin.mod:Detector\n"
    )

    out = _plugin_scan.scan_entry_points([str(tmp_path)], "agent_runner.detectors")
    assert ("third_party_detector", "thirdparty_plugin.mod:Detector") in out


def test_scanner_discovers_legacy_egg_info(tmp_path):
    """A legacy setuptools egg-info install (pre-dist-info layout, same
    entry_points.txt format) must be found by the fast path too — not only
    via the importlib.metadata fallback."""
    egg_info = tmp_path / "thirdparty_plugin-0.1.0.egg-info"
    egg_info.mkdir()
    (egg_info / "entry_points.txt").write_text(
        "[agent_runner.detectors]\nlegacy_detector = thirdparty_plugin.mod:Detector\n"
    )

    out = _plugin_scan.scan_entry_points([str(tmp_path)], "agent_runner.detectors")
    assert ("legacy_detector", "thirdparty_plugin.mod:Detector") in out


def test_scanner_dist_info_wins_over_egg_info_same_sys_path_entry(tmp_path):
    """When a single sys.path entry has BOTH a dist-info and an egg-info
    declaring the same name (e.g. a stale egg-info left behind by an
    upgrade), the dist-info entry wins — dist-info is scanned first."""
    (tmp_path / "pkg-2.0.dist-info").mkdir()
    (tmp_path / "pkg-2.0.dist-info" / "entry_points.txt").write_text(
        "[agent_runner.detectors]\nsame_name = pkg.mod:New\n"
    )
    (tmp_path / "pkg-1.0.egg-info").mkdir()
    (tmp_path / "pkg-1.0.egg-info" / "entry_points.txt").write_text(
        "[agent_runner.detectors]\nsame_name = pkg.mod:Old\n"
    )

    with pytest.warns(UserWarning):
        out = _plugin_scan.scan_entry_points([str(tmp_path)], "agent_runner.detectors")
    assert [pair for pair in out if pair[0] == "same_name"] == [("same_name", "pkg.mod:New")]


def test_scanner_preserves_mixed_case_entry_point_names(tmp_path):
    """configparser's default optionxform lowercases option keys -- an
    entry-point NAME like 'MyPlugin' would silently come back as 'myplugin',
    diverging from importlib.metadata (which preserves case). The scanner
    must set optionxform = str so a mixed-case name round-trips intact."""
    dist_info = tmp_path / "thirdparty_plugin-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "entry_points.txt").write_text(
        "[agent_runner.detectors]\nMyPlugin = thirdparty_plugin.mod:Detector\n"
    )

    out = _plugin_scan.scan_entry_points([str(tmp_path)], "agent_runner.detectors")
    assert ("MyPlugin", "thirdparty_plugin.mod:Detector") in out
    assert not any(name == "myplugin" for name, _ in out)


def test_malformed_entry_points_txt_falls_back_without_dropping_plugins(tmp_path, monkeypatch):
    """A real (not mocked) malformed entry_points.txt on sys.path must not
    silently drop plugins -- the hard fallback to importlib.metadata kicks in
    and the real, installed entries still come back."""
    monkeypatch.delenv("AGENT_RUNNER_PLUGIN_DISCOVERY", raising=False)
    dist_info = tmp_path / "broken_plugin-0.1.0.dist-info"
    dist_info.mkdir()
    # Not valid INI: a bare line with no section header.
    (dist_info / "entry_points.txt").write_text("this is not ini content\nno section header\n")

    sys_path = [str(tmp_path), *sys.path]
    out = _plugin_scan.scan_entry_points(sys_path, "agent_runner.post_round_hooks")
    assert ("pi_error_detector", "agent_runner.builtin_plugins.pi:PiErrorDetector") in out
