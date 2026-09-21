from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import ConfigError, load_config


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent-runner.toml"
    p.write_text("schema_version = 1\n" + body)
    return p


_MINIMAL_TOML_NO_PLUGINS = """\
[agent]
command = ["true"]
prompt_arg_template = ["{{prompt}}"]
[runtime]
work_dir = "{tmp_path}"
log_dir = "{tmp_path}/logs"
[prompt]
file = "{tmp_path}/prompt.md"
"""


# --- 0.2.13 strictness completion: base-table unknown keys, [phases] scalar
# keys, per-phase [phases.<name>.prompt] unknown keys, argv prompt_arg_template
# missing {prompt} (with the per-phase files=[] carve-out) ---


def _min_config_lines(
    tmp_path: Path, *, agent_extra: str = "", runtime_extra: str = ""
) -> list[str]:
    """The minimal valid [agent]/[runtime]/[prompt] lines this module's other
    tests inline repeatedly, with room to inject an extra raw line into
    [agent]/[runtime]. Callers append their own trailing [table] blocks."""
    return [
        "schema_version = 1\n",
        "[agent]\n",
        'command = ["true"]\n',
        'prompt_arg_template = ["{prompt}"]\n',
        agent_extra,
        "[runtime]\n",
        f'work_dir = "{tmp_path}"\n',
        f'log_dir = "{tmp_path}/logs"\n',
        runtime_extra,
        "[prompt]\n",
        f'file = "{tmp_path}/prompt.md"\n',
    ]


@pytest.mark.parametrize(
    "table, extra",
    [
        ("agent", "agent_extra"),
        ("runtime", "runtime_extra"),
        ("vcs", None),
        ("monitor", None),
    ],
)
def test_unknown_base_table_key_should_raise_config_error_when_loaded(
    tmp_path: Path, table: str, extra: str | None
) -> None:
    """Unknown keys under base [agent]/[runtime]/[vcs]/[monitor] used to load
    silently; 0.2.13 rejects them like the existing [prompt]/[schedule] checks."""

    (tmp_path / "prompt.md").write_text("p")
    kwargs = {extra: 'bogus_field = "x"\n'} if extra else {}
    lines = _min_config_lines(tmp_path, **kwargs)
    if extra is None:
        lines.append(f'[{table}]\nbogus_field = "x"\n')
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    with pytest.raises(ConfigError, match=rf"unknown \[{table}\].*bogus_field"):
        load_config(tmp_path / "agent-runner.toml")


def test_scalar_key_under_phases_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    """A stray non-table key under [phases] (typo'd out of a [phases.<name>]
    sub-table) used to be silently skipped; 0.2.13 rejects it."""

    (tmp_path / "prompt.md").write_text("p")
    lines = _min_config_lines(tmp_path)
    lines.append('[phases]\nlist = ["dev"]\nbogus_field = "x"\n[phases.dev]\n')
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    with pytest.raises(ConfigError, match=r"\[phases\].*bogus_field"):
        load_config(tmp_path / "agent-runner.toml")


def test_unknown_per_phase_prompt_key_should_raise_config_error_when_loaded(
    tmp_path: Path,
) -> None:
    """[phases.<name>.prompt] only ever reads 'files'; migrations/0.2.md already
    promised rejection of anything else — 0.2.13 makes the code match."""

    (tmp_path / "prompt.md").write_text("p")
    lines = _min_config_lines(tmp_path)
    lines.append(
        '[phases]\nlist = ["dev"]\n'
        "[phases.dev.prompt]\n"
        'files = ["dev.md"]\n'
        "inject_context = false\n"
    )
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    with pytest.raises(ConfigError, match=r"phases\.dev\.prompt.*inject_context"):
        load_config(tmp_path / "agent-runner.toml")


def test_argv_prompt_arg_template_should_raise_config_error_when_placeholder_missing(
    tmp_path: Path,
) -> None:
    """argv delivery with no {prompt} token in the template never delivers the
    prompt to the agent — a silent footgun 0.2.13 now rejects."""
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path, agent_extra='prompt_arg_template = ["-p"]\n')

    with pytest.raises(ConfigError, match=r"prompt_arg_template.*\{prompt\}"):
        load_config(cfg_path)


def test_disabled_phase_agent_should_be_accepted_without_placeholder_when_loaded(
    tmp_path: Path,
) -> None:
    """Carve-out: a phase whose own prompt.files = [] sends no prompt at all,
    so its own [phases.<name>.agent] template needs no {prompt} token."""
    (tmp_path / "prompt.md").write_text("p")
    lines = _min_config_lines(tmp_path)
    lines.append(
        '[phases]\nlist = ["silent"]\n'
        "[phases.silent.prompt]\n"
        "files = []\n"
        "[phases.silent.agent]\n"
        'prompt_arg_template = ["--silent-mode"]\n'
    )
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    cfg = load_config(tmp_path / "agent-runner.toml")

    silent_agent = cfg.phases.overrides["silent"].agent
    assert silent_agent is not None
    assert silent_agent.prompt_arg_template == ["--silent-mode"]


def test_enabled_phase_agent_should_raise_config_error_without_placeholder_when_loaded(
    tmp_path: Path,
) -> None:
    """Without the files=[] carve-out, a phase inherits the base's (mandatory,
    non-empty) prompt, so its own argv template still needs {prompt}."""

    (tmp_path / "prompt.md").write_text("p")
    lines = _min_config_lines(tmp_path)
    lines.append(
        '[phases]\nlist = ["silent"]\n'
        "[phases.silent.agent]\n"
        'prompt_arg_template = ["--silent-mode"]\n'
    )
    (tmp_path / "agent-runner.toml").write_text("".join(lines))

    with pytest.raises(ConfigError, match=r"prompt_arg_template.*\{prompt\}"):
        load_config(tmp_path / "agent-runner.toml")


def test_load_config_should_parse_exec_prefix_when_set_on_base_agent(tmp_path: Path) -> None:
    """exec_prefix is opaque operator-supplied argv (e.g. a container-run prefix);
    the base [agent] table parses it into AgentConfig verbatim."""
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path, agent_extra='exec_prefix = ["docker", "run", "img"]\n')

    cfg = load_config(cfg_path)

    assert cfg.agent.exec_prefix == ["docker", "run", "img"]


def test_load_config_should_reject_exec_prefix_when_set_on_a_phase_agent(tmp_path: Path) -> None:
    """exec_prefix is base-only: setting it under [phases.<name>.agent] would let
    phases silently disagree on the container/runtime, defeating the SSOT."""
    from tests._test_helpers import make_toml_with_sections

    cfg_path = make_toml_with_sections(
        tmp_path,
        phases_block=(
            '[phases]\nlist = ["a"]\n[phases.a.agent]\nexec_prefix = ["docker","run","img"]\n'
        ),
    )

    with pytest.raises(ConfigError, match="exec_prefix.*base"):
        load_config(cfg_path)


def test_load_config_should_reject_bare_string_exec_prefix_on_base_agent_when_invoked(
    tmp_path: Path,
) -> None:
    """exec_prefix must be a list like command -- a bare string (a single-token
    TOML mistake, e.g. `exec_prefix = "docker"`) is rejected via _require_str_list."""
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path, agent_extra='exec_prefix = "docker"\n')

    with pytest.raises(ConfigError, match="must be a list"):
        load_config(cfg_path)


def test_agent_config_should_accept_terminal_marker_when_set_in_toml(tmp_path: Path) -> None:
    """terminal_marker is opt-in per-preset grace-kill detection token (e.g. pi's
    "type":"agent_end") -- the base [agent] table parses it into AgentConfig verbatim."""
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(
        tmp_path, agent_extra='terminal_marker = "\\"type\\":\\"agent_end\\""\n'
    )

    cfg = load_config(cfg_path)

    assert cfg.agent.terminal_marker == '"type":"agent_end"'


def test_agent_config_should_default_terminal_marker_to_claude_token_when_absent(
    tmp_path: Path,
) -> None:
    """No [agent] terminal_marker key -> defaults to claude's own JSONL token, so
    existing configs (pre-0.2.23) are unaffected."""
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path)

    cfg = load_config(cfg_path)

    assert cfg.agent.terminal_marker == '"type":"result"'


def test_agent_config_should_default_sigterm_grace_s_to_10_when_absent(tmp_path: Path) -> None:
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path)

    cfg = load_config(cfg_path)

    assert cfg.agent.sigterm_grace_s == 10


def test_agent_config_should_accept_sigterm_grace_s_when_set_in_toml(tmp_path: Path) -> None:
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path, agent_extra="sigterm_grace_s = 12\n")

    cfg = load_config(cfg_path)

    assert cfg.agent.sigterm_grace_s == 12


@pytest.mark.parametrize("bad", ["0", "-1", '"x"', "true"])
def test_sigterm_grace_s_should_reject_non_positive_else_bool_values_when_invoked(
    tmp_path: Path, bad: str
) -> None:
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(tmp_path, agent_extra=f"sigterm_grace_s = {bad}\n")

    with pytest.raises(ValueError, match="sigterm_grace_s"):
        load_config(cfg_path)


def test_sigterm_grace_s_should_reject_values_above_round_term_grace_when_invoked(
    tmp_path: Path,
) -> None:
    from agent_runner.config.models import _MAX_SIGTERM_GRACE_S
    from tests._test_helpers import write_min_config

    cfg_path = write_min_config(
        tmp_path, agent_extra=f"sigterm_grace_s = {_MAX_SIGTERM_GRACE_S + 1}\n"
    )

    with pytest.raises(ValueError, match="sigterm_grace_s"):
        load_config(cfg_path)


def test_runtime_config_should_expose_round_budget_s_when_constructed(tmp_path):
    from agent_runner.config import RuntimeConfig

    cfg = RuntimeConfig(work_dir=tmp_path, log_dir=tmp_path)

    assert cfg.round_budget_s == 1800
    assert not hasattr(cfg, "round_timeout_s")


def test_load_config_should_parse_round_budget_s_in_all_three_forms_when_present(tmp_path):
    from agent_runner.config import load_config
    from tests._test_helpers import make_toml_with_sections

    toml = make_toml_with_sections(
        tmp_path,
        runtime_extra="round_budget_s = 900\n",
        phases_block=('[phases]\nlist = ["dev"]\n[phases.dev]\nround_budget_s = 1200\n'),
    )

    cfg = load_config(toml)

    assert cfg.runtime.round_budget_s == 900
    assert cfg.phases.overrides["dev"].round_budget_s == 1200


def test_load_config_should_reject_config_missing_schema_version_when_loaded(tmp_path):
    from agent_runner.config import load_config

    toml = tmp_path / "agent-runner.toml"
    toml.write_text('[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n')

    with pytest.raises(ConfigError, match="run 'agent-runner migrate'"):
        load_config(toml)


def test_load_config_should_accept_schema_version_one_when_loaded(tmp_path):
    from agent_runner.config import load_config
    from tests._test_helpers import make_toml

    toml = make_toml(tmp_path)

    cfg = load_config(toml)

    assert cfg is not None


def test_load_config_should_reject_newer_schema_version_when_loaded(tmp_path):
    from agent_runner.config import load_config

    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        'schema_version = 2\n[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
    )

    with pytest.raises(ConfigError, match="upgrade agent-runner"):
        load_config(toml)


def test_load_config_should_reject_older_schema_version_when_loaded(tmp_path):
    from agent_runner.config import load_config

    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        'schema_version = 0\n[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
    )

    with pytest.raises(ConfigError, match="run 'agent-runner migrate'"):
        load_config(toml)


def test_load_config_should_reject_non_integer_schema_version_when_loaded(tmp_path):
    from agent_runner.config import load_config

    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        'schema_version = "1"\n[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
    )

    with pytest.raises(ConfigError, match="schema_version must be an integer"):
        load_config(toml)
