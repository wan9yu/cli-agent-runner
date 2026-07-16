from agent_runner.agent_runtime import signal_name


def test_negative_form_is_signal():
    assert signal_name(-15) == "SIGTERM"
    assert signal_name(-9) == "SIGKILL"


def test_shell_128_plus_n_form_is_signal():
    assert signal_name(143) == "SIGTERM"
    assert signal_name(137) == "SIGKILL"


def test_clean_and_plain_error_are_not_signals():
    assert signal_name(0) is None
    assert signal_name(1) is None


def test_high_nonsignal_exit_is_not_a_signal_and_does_not_crash():
    # 200 -> 72 is not a valid signal number; must return None, not raise.
    assert signal_name(200) is None
