import pytest

from agent_runner.config import ConfigError, load_config


def _write(tmp_path, schedule_block):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("hi", encoding="utf-8")
    p = tmp_path / "agent-runner.toml"
    p.write_text(
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        f'[prompt]\nfile = "{prompt}"\n' + schedule_block,
        encoding="utf-8",
    )
    return p


def test_schedule_should_be_disabled_when_absent(tmp_path):
    cfg = load_config(_write(tmp_path, ""))

    assert cfg.schedule.enabled is False
    assert cfg.schedule.pause_windows == ()


def test_schedule_should_parse_pause_windows_when_configured(tmp_path):
    windows = 'pause_windows = ["09:00-12:00", "14:00-18:00"]'
    block = f'[schedule]\ntimezone = "Asia/Shanghai"\n{windows}\n'

    cfg = load_config(_write(tmp_path, block))

    assert cfg.schedule.enabled is True
    assert cfg.schedule.timezone == "Asia/Shanghai"
    assert [w.label for w in cfg.schedule.pause_windows] == ["09:00-12:00", "14:00-18:00"]


def test_schedule_should_accept_run_windows_when_configured(tmp_path):
    cfg = load_config(_write(tmp_path, '[schedule]\nrun_windows = ["00:00-06:00"]\n'))

    assert [w.label for w in cfg.schedule.run_windows] == ["00:00-06:00"]
    assert cfg.schedule.enabled is True


def test_schedule_should_reject_config_when_timezone_invalid(tmp_path):
    with pytest.raises(ConfigError, match="timezone"):
        load_config(
            _write(
                tmp_path, '[schedule]\ntimezone = "Mars/Olympus"\npause_windows = ["09:00-12:00"]\n'
            )
        )


def test_schedule_should_reject_config_when_window_malformed(tmp_path):
    with pytest.raises(ConfigError, match="pause_windows"):
        load_config(_write(tmp_path, '[schedule]\npause_windows = ["9-12"]\n'))


def test_schedule_should_parse_weekday_windows_when_days_specified(tmp_path):
    cfg = load_config(_write(tmp_path, '[schedule]\npause_windows = ["Mon-Fri 09:00-12:00"]\n'))

    assert cfg.schedule.pause_windows[0].days == frozenset({0, 1, 2, 3, 4})


def test_schedule_should_reject_config_when_pause_windows_is_scalar(tmp_path):
    with pytest.raises(ConfigError, match="must be a list"):
        load_config(_write(tmp_path, '[schedule]\npause_windows = "09:00-12:00"\n'))
