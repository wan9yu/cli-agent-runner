import json

from agent_runner import api, events


def test_emit_config_migrated(tmp_path):
    api.emit_config_migrated(
        tmp_path,
        applied=["runtime.rate_limit_action → runtime.transient_error_action"],
        manual=[],
        path="agent-runner.toml",
    )
    ev = [
        json.loads(x)
        for f in sorted(tmp_path.glob("events-*.jsonl"))
        for x in f.read_text().splitlines()
    ]
    assert ev[0]["event"] == events.CONFIG_MIGRATED
    assert ev[0]["applied"] == ["runtime.rate_limit_action → runtime.transient_error_action"]
    assert ev[0]["manual"] == [] and ev[0]["path"] == "agent-runner.toml"


def test_config_migrated_is_builtin():
    assert events.CONFIG_MIGRATED in events._BUILTIN_KINDS
