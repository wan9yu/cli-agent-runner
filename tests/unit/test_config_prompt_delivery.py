import pytest

from agent_runner.config import load_config
from tests._test_helpers import write_min_config


def _cfg(tmp_path, agent_extra):
    return write_min_config(tmp_path, agent_extra=agent_extra)


def test_prompt_delivery_should_default_to_argv(tmp_path):
    cfg = load_config(_cfg(tmp_path, agent_extra=""))

    assert cfg.agent.prompt_delivery == "argv"


def test_prompt_delivery_should_accept_stdin_when_template_has_no_prompt_token(tmp_path):
    cfg = load_config(_cfg(tmp_path, 'prompt_delivery = "stdin"\nprompt_arg_template = ["-p"]\n'))

    assert cfg.agent.prompt_delivery == "stdin"


def test_prompt_delivery_should_reject_stdin_when_template_has_prompt_token(tmp_path):
    with pytest.raises(ValueError, match="stdin"):
        load_config(
            _cfg(
                tmp_path,
                'prompt_delivery = "stdin"\nprompt_arg_template = ["-p", "{prompt}"]\n',
            )
        )


def test_prompt_delivery_should_reject_invalid_value(tmp_path):
    with pytest.raises(ValueError):
        load_config(_cfg(tmp_path, 'prompt_delivery = "file"\n'))
