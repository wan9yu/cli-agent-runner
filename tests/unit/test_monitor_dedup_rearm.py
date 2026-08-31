from __future__ import annotations

from unittest.mock import patch

from agent_runner import api
from agent_runner.api_types import Alert


class _StopError(Exception):
    """Breaks the monitor loop from the fake sleep. NOT StopIteration — raising
    StopIteration inside a generator becomes RuntimeError (PEP 479)."""


def _disk(v: float) -> Alert:
    return Alert("warning", "disk_warning", "m", {"value": v, "threshold": 90.0}, "t")


def test_recovered_then_recurred_alert_fires_twice(tmp_path, monkeypatch) -> None:
    # poll 1: firing; poll 2: cleared (empty); poll 3: firing again -> should re-yield.
    polls = iter([[_disk(91.0)], [], [_disk(92.0)]])
    monkeypatch.setattr(api, "_poll_once", lambda _wd, **_kwargs: next(polls))
    sleeps = {"n": 0}

    def _fake_sleep(_s) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 3:  # let all three polls run; break on the 3rd sleep
            raise _StopError

    monkeypatch.setattr(api.SYSTEM_CLOCK, "sleep", _fake_sleep)
    mon = type("M", (), {"auto_stop_on": ()})()
    cfg = type("C", (), {"runtime": type("R", (), {"log_dir": tmp_path})(), "monitor": mon})()
    monkeypatch.setattr(api, "load_config", lambda _p: cfg)
    tmp_path.mkdir(exist_ok=True)
    with patch.object(api.monitor, "on_alert"):
        gen = api._monitor_loop_iter(tmp_path)
        seen = []
        try:
            for a in gen:
                seen.append(a.context["value"])
        except _StopError:
            pass
    assert seen == [91.0, 92.0]  # not deduped away — the episode ended and re-armed
