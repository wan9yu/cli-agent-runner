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


def test_no_phases_runnable_returns_none(tmp_path):
    cfg = _cfg(tmp_path, "")
    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))
    assert sel.phase is None
    assert sel.paused is False


def test_no_phases_paused_by_global_schedule(tmp_path):
    cfg = _cfg(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\npause_windows = ["00:00-24:00"]\n',
    )
    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))
    assert sel.phase is None
    assert sel.paused is True
    assert sel.resume_at is None  # never opens → dropped None → resume_at None


def test_all_open_wait_and_skip_return_rotation_phase(tmp_path):
    for policy in ("wait", "skip"):
        block = f'[phases]\nlist = ["a","b"]\nphase_policy = "{policy}"\n'
        cfg = _cfg(tmp_path, block)
        sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))
        assert sel.phase == "a"  # k=0 rotation phase
        assert sel.paused is False
        assert sel.skipped == []
        assert sel.active_window is None


def test_wait_rotation_phase_closed_pauses(tmp_path):
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


def test_skip_rotation_closed_next_open_runs_next(tmp_path):
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


def test_skip_all_closed_pauses_with_min_resume(tmp_path):
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


def test_skip_all_closed_including_never_opening_drops_none(tmp_path):
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


def test_statelessness_same_inputs_same_output(tmp_path):
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


def test_round_num_rotates_starting_phase(tmp_path):
    cfg = _cfg(tmp_path, '[phases]\nlist = ["a","b","c"]\nphase_policy = "wait"\n')
    assert phase_select.select_phase(cfg, 1, now_fn=_clock(10)).phase == "a"
    assert phase_select.select_phase(cfg, 2, now_fn=_clock(10)).phase == "b"
    assert phase_select.select_phase(cfg, 3, now_fn=_clock(10)).phase == "c"
    assert phase_select.select_phase(cfg, 4, now_fn=_clock(10)).phase == "a"


def _skip_cfg(tmp_path, phases=("a", "b"), extra=""):
    lst = ",".join(f'"{p}"' for p in phases)
    return _cfg(tmp_path, f'[phases]\nlist = [{lst}]\nphase_policy = "skip"\n{extra}')


def test_skip_steps_over_throttled_phase(tmp_path):
    cfg = _skip_cfg(tmp_path)  # both windows always open
    sel = phase_select.select_phase(cfg, 1, throttled_phases=frozenset({"a"}), now_fn=_clock(10))
    assert sel.phase == "b"
    assert sel.skipped == ["a"]
    assert sel.paused is False


def test_all_phases_throttled_pauses_no_window_resume(tmp_path):
    cfg = _skip_cfg(tmp_path)
    sel = phase_select.select_phase(
        cfg, 1, throttled_phases=frozenset({"a", "b"}), now_fn=_clock(10)
    )
    assert sel.phase is None
    assert sel.paused is True
    assert sel.resume_at is None  # throttled phases own no window resume


def test_throttled_plus_window_closed_resume_is_open_window(tmp_path):
    # a throttled; b window-closed 09:00-12:00 -> paused, resume_at = b's 12:00 open.
    cfg = _skip_cfg(tmp_path, extra='[phases.b.schedule]\npause_windows = ["09:00-12:00"]\n')
    sel = phase_select.select_phase(cfg, 1, throttled_phases=frozenset({"a"}), now_fn=_clock(10))
    assert sel.phase is None
    assert sel.paused is True
    assert sel.resume_at is not None and sel.resume_at.hour == 12


def test_empty_throttled_is_0_2_9_behavior(tmp_path):
    cfg = _skip_cfg(tmp_path)
    sel = phase_select.select_phase(cfg, 1, now_fn=_clock(10))
    assert sel.phase == "a"
    assert sel.skipped == []


def test_throttled_determinism_same_inputs_same_output(tmp_path):
    cfg = _skip_cfg(tmp_path)
    t = frozenset({"a"})
    a = phase_select.select_phase(cfg, 1, throttled_phases=t, now_fn=_clock(10))
    b = phase_select.select_phase(cfg, 1, throttled_phases=t, now_fn=_clock(10))
    assert (a.phase, a.paused, a.skipped) == (b.phase, b.paused, b.skipped)


def test_rotation_index_is_zero_based_round_minus_one_mod_n() -> None:
    from agent_runner import phase_select

    assert [phase_select.rotation_index(r, 3) for r in (1, 2, 3, 4)] == [0, 1, 2, 0]


def test_runner_and_phase_select_rotation_agree(tmp_path) -> None:
    from agent_runner import phase_select
    from agent_runner.runner import _phase_for

    phases = ["a", "b", "c"]
    phases_block = '[phases]\nlist = ["a","b","c"]\nphase_policy = "wait"\n'
    cfg = _cfg(tmp_path, phases_block)
    for r in range(1, 8):
        _, idx = _phase_for(r, phases)
        assert idx == phase_select.rotation_index(r, len(phases))
        assert phases[idx] == phase_select.candidate_phases(cfg, r)[0]
