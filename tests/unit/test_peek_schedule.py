from agent_runner import monitor


def test_latest_schedule_state_should_return_paused_state_when_last_event_is_schedule_paused():
    events = [
        {
            "ts": "2026-08-22T10:00:00.000Z",
            "event": "schedule_paused",
            "resume_at": "2026-08-22T12:00:00+08:00",
            "active_window": "09:00-12:00",
        },
    ]

    st = monitor.latest_schedule_state(events)

    assert st == {
        "paused": True,
        "resume_at": "2026-08-22T12:00:00+08:00",
        "active_window": "09:00-12:00",
        "phase": "",  # no phase on a legacy (non-phase-aware) pause
    }


def test_latest_schedule_state_should_surface_phase_when_schedule_paused_event_has_phase():
    events = [
        {
            "ts": "2026-08-22T10:00:00.000Z",
            "event": "schedule_paused",
            "resume_at": "2026-08-22T12:00:00+08:00",
            "active_window": "09:00-12:00",
            "phase": "planning",
        },
    ]

    assert monitor.latest_schedule_state(events)["phase"] == "planning"


def test_latest_schedule_state_should_return_none_when_resumed_after_paused():
    events = [
        {
            "ts": "2026-08-22T10:00:00.000Z",
            "event": "schedule_paused",
            "resume_at": "x",
            "active_window": "y",
        },
        {"ts": "2026-08-22T12:00:00.000Z", "event": "schedule_resumed", "paused_for_s": 7200},
    ]

    assert monitor.latest_schedule_state(events) is None


def test_latest_schedule_state_should_return_none_when_events_empty():
    assert monitor.latest_schedule_state([]) is None
