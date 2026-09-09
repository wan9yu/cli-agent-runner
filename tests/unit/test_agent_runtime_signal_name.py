from agent_runner.agent_runtime import signal_name


def test_signal_name_should_return_signal_when_given_negative_form():
    assert signal_name(-15) == "SIGTERM"
    assert signal_name(-9) == "SIGKILL"


def test_signal_name_should_return_signal_when_given_shell_128_plus_n_form():
    assert signal_name(143) == "SIGTERM"
    assert signal_name(137) == "SIGKILL"


def test_signal_name_should_return_none_when_given_non_signal_exit_code():
    assert signal_name(0) is None
    assert signal_name(1) is None


def test_signal_name_should_return_none_when_given_out_of_range_exit_code():
    # 200 -> 72 is not a valid signal number; must return None, not raise.
    assert signal_name(200) is None
