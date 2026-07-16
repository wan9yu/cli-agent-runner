import pytest

from agent_runner.config import ConfigError, load_config
from tests._test_helpers import write_min_config


def _cfg(tmp_path, agent_extra):
    return write_min_config(tmp_path, agent_extra=agent_extra)


def test_default_is_argv(tmp_path):
    cfg = load_config(_cfg(tmp_path, agent_extra=""))
    assert cfg.agent.prompt_delivery == "argv"


def test_stdin_accepted_when_template_has_no_prompt(tmp_path):
    cfg = load_config(_cfg(tmp_path, 'prompt_delivery = "stdin"\nprompt_arg_template = ["-p"]\n'))
    assert cfg.agent.prompt_delivery == "stdin"


def test_stdin_with_prompt_token_rejected(tmp_path):
    with pytest.raises(ConfigError, match="stdin"):
        load_config(
            _cfg(
                tmp_path,
                'prompt_delivery = "stdin"\nprompt_arg_template = ["-p", "{prompt}"]\n',
            )
        )


def test_invalid_value_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_cfg(tmp_path, 'prompt_delivery = "file"\n'))
