"""Pure per-phase scheduler: phase_select.select_phase (0.2.9).

Clock-injected, stateless: output depends only on (round_num, now, cfg).
Cfg built via load_config from a [phases] TOML with per-phase schedules.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from agent_runner import phase_select
from agent_runner.config import load_config
from tests._test_helpers import make_toml_with_sections

TZ = ZoneInfo("Asia/Shanghai")


def _clock(hour):
    def _now(_tz):
        return datetime(2026, 8, 22, hour, 0, tzinfo=TZ)

    return _now


def _cfg(tmp_path, phases_block):
    return load_config(make_toml_with_sections(tmp_path, phases_block=phases_block))


def test_select_phase_should_return_none_when_no_phases_configured(tmp_path):
    cfg = _cfg(tmp_path, "")

    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))

    assert sel.phase is None
    assert sel.paused is False


def test_select_phase_should_pause_when_global_pause_window_covers_all_day(tmp_path):
    cfg = _cfg(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\npause_windows = ["00:00-24:00"]\n',
    )

    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))

    assert sel.phase is None
    assert sel.paused is True
    assert sel.resume_at is None  # never opens → dropped None → resume_at None


def test_select_phase_should_return_rotation_phase_when_all_phases_open_regardless_of_policy(
    tmp_path,
):
    for policy in ("wait", "skip"):
        block = f'[phases]\nlist = ["a","b"]\nphase_policy = "{policy}"\n'
        cfg = _cfg(tmp_path, block)

        sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))

        assert sel.phase == "a"  # k=0 rotation phase
        assert sel.paused is False
        assert sel.skipped == []
        assert sel.active_window is None


def test_select_phase_should_pause_when_wait_policy_and_rotation_phase_closed(tmp_path):
    cfg = _cfg(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a","b"]\nphase_policy = "wait"\n'
        '[phases.a.schedule]\npause_windows = ["09:00-12:00"]\n',
    )

    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))

    assert sel.phase is None
    assert sel.paused is True
    assert sel.skipped == []
    assert sel.resume_at is not None
    assert sel.resume_at.hour == 12  # phase a's next_resume_at


def test_select_phase_should_return_next_open_phase_when_skip_policy_and_rotation_phase_closed(
    tmp_path,
):
    cfg = _cfg(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n'
        '[phases.a.schedule]\npause_windows = ["09:00-12:00"]\n',
    )

    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))

    assert sel.phase == "b"  # a closed, b open
    assert sel.paused is False
    assert sel.skipped == ["a"]
    assert sel.active_window  # the skipped phase's window label


def test_select_phase_should_pause_with_earliest_resume_when_skip_policy_and_all_phases_closed(
    tmp_path,
):
    cfg = _cfg(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n'
        '[phases.a.schedule]\npause_windows = ["09:00-14:00"]\n'
        '[phases.b.schedule]\npause_windows = ["09:00-12:00"]\n',
    )

    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))

    assert sel.phase is None
    assert sel.paused is True
    assert sel.skipped == []
    assert sel.resume_at.hour == 12  # min(14:00, 12:00)


def test_select_phase_should_drop_never_opening_phase_when_computing_min_resume(tmp_path):
    """A ["00:00-24:00"] phase never opens (next_resume_at None). min() must
    drop the None rather than crash, using the other phase's resume."""
    cfg = _cfg(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n'
        '[phases.a.schedule]\npause_windows = ["00:00-24:00"]\n'
        '[phases.b.schedule]\npause_windows = ["09:00-12:00"]\n',
    )

    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))

    assert sel.paused is True
    assert sel.resume_at.hour == 12  # b's; a's None dropped


def test_select_phase_should_return_same_result_when_called_twice_with_same_inputs(tmp_path):
    cfg = _cfg(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n'
        '[phases.a.schedule]\npause_windows = ["09:00-12:00"]\n',
    )

    a = phase_select.select_phase(cfg, 3, now_fn=_clock(10))
    b = phase_select.select_phase(cfg, 3, now_fn=_clock(10))

    assert (a.phase, a.paused, a.skipped, a.resume_at) == (
        b.phase,
        b.paused,
        b.skipped,
        b.resume_at,
    )


def test_select_phase_should_rotate_starting_phase_when_round_num_increases(tmp_path):
    cfg = _cfg(tmp_path, '[phases]\nlist = ["a","b","c"]\nphase_policy = "wait"\n')

    assert phase_select.select_phase(cfg, 1, now_fn=_clock(10)).phase == "a"
    assert phase_select.select_phase(cfg, 2, now_fn=_clock(10)).phase == "b"
    assert phase_select.select_phase(cfg, 3, now_fn=_clock(10)).phase == "c"
    assert phase_select.select_phase(cfg, 4, now_fn=_clock(10)).phase == "a"


def _skip_cfg(tmp_path, phases=("a", "b"), extra=""):
    lst = ",".join(f'"{p}"' for p in phases)
    return _cfg(tmp_path, f'[phases]\nlist = [{lst}]\nphase_policy = "skip"\n{extra}')


def test_select_phase_should_skip_throttled_phase_when_policy_is_skip(tmp_path):
    cfg = _skip_cfg(tmp_path)  # both windows always open

    sel = phase_select.select_phase(cfg, 1, throttled_phases=frozenset({"a"}), now_fn=_clock(10))

    assert sel.phase == "b"
    assert sel.skipped == ["a"]
    assert sel.paused is False


def test_select_phase_should_pause_with_no_resume_when_all_phases_throttled(tmp_path):
    cfg = _skip_cfg(tmp_path)

    sel = phase_select.select_phase(
        cfg, 1, throttled_phases=frozenset({"a", "b"}), now_fn=_clock(10)
    )

    assert sel.phase is None
    assert sel.paused is True
    assert sel.resume_at is None  # throttled phases own no window resume


def test_select_phase_should_resume_at_open_window_when_one_phase_throttled_and_other_closed(
    tmp_path,
):
    # a throttled; b window-closed 09:00-12:00 -> paused, resume_at = b's 12:00 open.
    cfg = _skip_cfg(tmp_path, extra='[phases.b.schedule]\npause_windows = ["09:00-12:00"]\n')

    sel = phase_select.select_phase(cfg, 1, throttled_phases=frozenset({"a"}), now_fn=_clock(10))

    assert sel.phase is None
    assert sel.paused is True
    assert sel.resume_at is not None and sel.resume_at.hour == 12


def test_select_phase_should_match_pre_throttle_behavior_when_throttled_set_is_empty(tmp_path):
    cfg = _skip_cfg(tmp_path)

    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))

    assert sel.phase == "a"
    assert sel.skipped == []


def test_select_phase_should_return_same_result_when_same_throttled_set_used_twice(tmp_path):
    cfg = _skip_cfg(tmp_path)
    t = frozenset({"a"})

    a = phase_select.select_phase(cfg, 1, throttled_phases=t, now_fn=_clock(10))
    b = phase_select.select_phase(cfg, 1, throttled_phases=t, now_fn=_clock(10))

    assert (a.phase, a.paused, a.skipped) == (b.phase, b.paused, b.skipped)


def test_rotation_index_should_be_zero_based_round_minus_one_mod_n() -> None:
    assert [phase_select.rotation_index(r, 3) for r in (1, 2, 3, 4)] == [0, 1, 2, 0]


def test_phase_select_should_agree_with_runner_rotation_index_across_rounds(tmp_path) -> None:
    from agent_runner.runner import _phase_for

    phases = ["a", "b", "c"]
    phases_block = '[phases]\nlist = ["a","b","c"]\nphase_policy = "wait"\n'
    cfg = _cfg(tmp_path, phases_block)

    for r in range(1, 8):
        _, idx = _phase_for(r, phases)
        assert idx == phase_select.rotation_index(r, len(phases))
        assert phases[idx] == phase_select.candidate_phases(cfg, r)[0]


def _run_windows_toml(windows: list[str]) -> str:
    if not windows:
        return ""
    items = ", ".join(f'"{w}"' for w in windows)
    return f"run_windows = [{items}]\n"


def _cfg_two_agent_phases(
    tmp_path,
    *,
    windows_a: list[str],
    windows_b: list[str],
    tz: str | None = None,
    tz_a: str | None = None,
    tz_b: str | None = None,
):
    tz_a = tz_a or tz
    tz_b = tz_b or tz
    block = (
        '[phases]\nlist = ["a", "b"]\n'
        '[phases.a.agent]\nname = "agent-a"\n'
        "[phases.a.schedule]\n"
        + (f'timezone = "{tz_a}"\n' if tz_a else "")
        + _run_windows_toml(windows_a)
        + '[phases.b.agent]\nname = "agent-b"\n'
        + "[phases.b.schedule]\n"
        + (f'timezone = "{tz_b}"\n' if tz_b else "")
        + _run_windows_toml(windows_b)
    )
    return _cfg(tmp_path, block)


def _cfg_one_agent_one_plain_phase(tmp_path, *, windows_a: list[str], windows_b: list[str]):
    block = (
        '[phases]\nlist = ["a", "b"]\n'
        '[phases.a.agent]\nname = "agent-a"\n'
        "[phases.a.schedule]\n"
        + _run_windows_toml(windows_a)
        + "[phases.b.schedule]\n"
        + _run_windows_toml(windows_b)
    )
    return _cfg(tmp_path, block)


def test_overlap_should_flag_pair_when_both_windowed_agent_phases_intersect_in_same_tz(tmp_path):
    cfg = _cfg_two_agent_phases(
        tmp_path, windows_a=["09:00-12:00"], windows_b=["11:00-14:00"], tz="UTC"
    )

    overlaps = phase_select.find_phase_window_overlaps(cfg)

    assert len(overlaps) == 1


def test_overlap_should_ignore_pair_when_one_phase_has_no_own_run_windows(tmp_path):
    cfg = _cfg_two_agent_phases(tmp_path, windows_a=["09:00-12:00"], windows_b=[], tz="UTC")

    assert phase_select.find_phase_window_overlaps(cfg) == []


def test_overlap_should_ignore_pair_when_agents_not_both_overridden(tmp_path):
    cfg = _cfg_one_agent_one_plain_phase(
        tmp_path, windows_a=["09:00-12:00"], windows_b=["10:00-11:00"]
    )

    assert phase_select.find_phase_window_overlaps(cfg) == []


def test_overlap_should_skip_pair_when_effective_timezones_differ(tmp_path):
    cfg = _cfg_two_agent_phases(
        tmp_path,
        windows_a=["09:00-12:00"],
        windows_b=["10:00-11:00"],
        tz_a="UTC",
        tz_b="Asia/Tokyo",
    )

    assert phase_select.find_phase_window_overlaps(cfg) == []


def test_overlap_should_ignore_pair_when_windows_do_not_intersect(tmp_path):
    cfg = _cfg_two_agent_phases(
        tmp_path, windows_a=["09:00-10:00"], windows_b=["11:00-12:00"], tz="UTC"
    )

    assert phase_select.find_phase_window_overlaps(cfg) == []
