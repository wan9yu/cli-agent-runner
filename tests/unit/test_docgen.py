from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import defenses
from agent_runner._docgen import render_defenses_table, replace_block
from agent_runner.config import (
    AgentConfig,
    Config,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)


def _default_cfg() -> Config:
    return Config(
        agent=AgentConfig(command=["agent"], prompt_arg_template=[]),
        runtime=RuntimeConfig(work_dir=Path("."), log_dir=Path("./logs")),
        prompt=PromptConfig(file=Path("./prompt.md")),
        vcs=VcsConfig(),
    )


def test_given_text_with_block_when_replaced_then_returns_new_content_between_markers() -> None:
    text = "intro line\n<!-- gen:foo -->\nOLD CONTENT\n<!-- /gen:foo -->\ntrailing line\n"
    got = replace_block(text, "foo", "NEW CONTENT")
    assert "OLD CONTENT" not in got
    assert "NEW CONTENT" in got
    assert "<!-- gen:foo -->" in got
    assert "<!-- /gen:foo -->" in got
    assert got.startswith("intro line")
    assert got.endswith("trailing line\n")


def test_given_text_without_block_when_replaced_then_returns_unchanged() -> None:
    text = "some markdown without markers\n"
    assert replace_block(text, "missing", "X") == text


def test_given_unclosed_block_when_replaced_then_raises_valueerror() -> None:
    text = "<!-- gen:foo -->\nstuff\n(never closes)\n"
    with pytest.raises(ValueError, match="foo"):
        replace_block(text, "foo", "X")


def test_given_default_cfg_when_render_defenses_table_then_returns_markdown_table() -> None:
    md = render_defenses_table()
    # Header
    assert "| Defense | Codifies | Guarded by |" in md
    assert "|---|---|---|" in md
    # Row count matches catalog (guards "docgen renders every entry", no hardcoded 11)
    rows = [
        line
        for line in md.splitlines()
        if line.startswith("| ") and "Defense" not in line and "---" not in line[:5]
    ]
    assert len(rows) == len(defenses.catalog(_default_cfg()))
    # Spot-check one well-known defense
    assert "round_timeout_s" in md


def test_given_render_defenses_table_when_called_then_paths_render_relative() -> None:
    md = render_defenses_table()
    # No absolute paths should leak — guarded_by is rendered as repo-relative.
    assert "/Users/" not in md
    assert "tests/unit/test_agent_runtime.py" in md  # one known guarded_by


def test_given_docs_dir_with_marker_when_render_then_writes_table(
    tmp_path: Path,
) -> None:
    from agent_runner._docgen import render

    arch = tmp_path / "architecture.md"
    arch.write_text(
        "intro\n<!-- gen:defenses-table -->\nPLACEHOLDER\n<!-- /gen:defenses-table -->\noutro\n"
    )
    out = render(docs_dir=tmp_path, write=True)
    assert arch in out
    rewritten = arch.read_text()
    assert "PLACEHOLDER" not in rewritten
    assert "round_timeout_s" in rewritten
    assert "<!-- gen:defenses-table -->" in rewritten
    assert "<!-- /gen:defenses-table -->" in rewritten


def test_given_render_with_write_false_when_called_then_does_not_touch_disk(
    tmp_path: Path,
) -> None:
    from agent_runner._docgen import render

    arch = tmp_path / "architecture.md"
    original = "<!-- gen:defenses-table -->\nPLACEHOLDER\n<!-- /gen:defenses-table -->\n"
    arch.write_text(original)
    out = render(docs_dir=tmp_path, write=False)
    assert arch.read_text() == original  # disk unchanged
    assert "round_timeout_s" in out[arch]  # but rendered text returned


def test_given_unknown_gen_name_when_render_then_raises(tmp_path: Path) -> None:
    from agent_runner._docgen import render

    arch = tmp_path / "x.md"
    arch.write_text("<!-- gen:does-not-exist -->\nfoo\n<!-- /gen:does-not-exist -->\n")
    with pytest.raises(ValueError, match="does-not-exist"):
        render(docs_dir=tmp_path, write=False)


def test_given_unclosed_marker_in_file_when_render_then_error_names_the_file(
    tmp_path: Path,
) -> None:
    """ValueError from replace_block must include the failing file's name."""
    from agent_runner._docgen import render

    bad = tmp_path / "bad.md"
    bad.write_text("<!-- gen:defenses-table -->\nstuff but no close\n")
    with pytest.raises(ValueError, match="bad.md"):
        render(docs_dir=tmp_path, write=False)


def test_given_render_detector_list_when_called_then_marks_auto_stop_kinds() -> None:
    from agent_runner._docgen import render_detector_list

    md = render_detector_list()
    # Flag the two auto-stop detectors with **auto-stop**
    assert "oauth_fail" in md
    assert "disk_critical" in md
    assert "**auto-stop**" in md
    # Notify-only kinds get no flag
    assert "timeout_rate" in md


def test_given_render_event_kinds_list_when_called_then_returns_bullet_list() -> None:
    from agent_runner._docgen import render_event_kinds_list

    md = render_event_kinds_list()
    bullets = [line for line in md.splitlines() if line.startswith("- ")]
    # >=12 known events (incl. 2 monitor events: alert_emitted, auto_stop_triggered)
    assert len(bullets) >= 12
    assert any("round_start" in line for line in bullets)
    assert any("monitor_alert_emitted" in line for line in bullets)


def test_given_render_verb_table_when_called_then_lists_all_subcommands() -> None:
    from agent_runner._docgen import render_verb_table

    md = render_verb_table()
    # Each verb appears
    for verb in (
        "init",
        "install",
        "uninstall",
        "start",
        "stop",
        "kill",
        "restart",
        "status",
        "round",
        "serve",
        "peek",
        "watch",
        "monitor",
    ):
        assert f"`{verb}`" in md, f"verb {verb!r} missing"
    assert "| Verb | Description |" in md


def test_given_render_config_schema_table_when_called_then_lists_sections() -> None:
    from agent_runner._docgen import render_config_schema_table

    md = render_config_schema_table()
    # Each section name appears as a sub-heading
    assert "### `[agent]`" in md
    assert "### `[runtime]`" in md
    assert "### `[prompt]`" in md
    assert "### `[vcs]`" in md
    # Spot-check fields
    assert "command" in md
    assert "round_timeout_s" in md
    assert "stash_idempotency_s" in md
    # Defaults are shown for fields that have them
    assert "1800" in md
    assert "stash" in md  # vcs.dirty_action default


def test_given_content_with_regex_escapes_when_replaced_then_inserted_verbatim() -> None:
    """re.sub's string replacement is a TEMPLATE — a callable repl inserts literally.

    Round-trips the three expansion classes at once: \\n (escape), \\\\b (backslash
    survival), \\1 (group reference). The first two corrupt a generated doc SILENTLY
    — a deterministic corruption is a fixed point of render(), so the generator's
    output equals the corrupted file and `git diff --exit-code docs/` stays green on
    it. That is why this test, not the docs gate, is the guard. \\1 instead crashes
    outright (a backreference into a groupless pattern).
    """
    body = r"a\n\\b\1c"
    text = "intro\n<!-- gen:x -->\nOLD\n<!-- /gen:x -->\noutro\n"
    got = replace_block(text, "x", body)
    assert got == f"intro\n<!-- gen:x -->\n{body}\n<!-- /gen:x -->\noutro\n"


def test_given_config_when_schema_rendered_then_phases_and_plugins_sections_present() -> None:
    """_SECTIONS must cover all 7 Config fields — [plugins] disable was undocumented."""
    from agent_runner._docgen import render_config_schema_table

    md = render_config_schema_table()
    assert "### `[phases]`" in md
    assert "### `[plugins]`" in md
    assert "| `overrides` | `dict[str, PhaseOverride]` | {} |" in md
    assert "| `disable` | `list[str]` | [] |" in md
    assert "| `raw` | `dict[str, Any]` | {} |" in md


def test_given_prompt_section_when_rendered_then_concat_separator_row_is_one_line() -> None:
    """Pins the row shape through the _cell/_field_table refactor.

    render_config_schema_table() already emits this as one line today — the
    3-line shattering in docs/configuration.md happens at WRITE time, in
    replace_block. This test guards the renderer half of that pair.
    """
    from agent_runner._docgen import render_config_schema_table

    md = render_config_schema_table()
    assert r"| `concat_separator` | `str` | '\n\n' |" in md.splitlines()


def test_given_monitor_section_when_rendered_then_host_health_subsection_lists_fields() -> None:
    """[monitor.host_health] is a real TOML sub-table; the parent row is an opaque repr."""
    from agent_runner._docgen import render_config_schema_table

    md = render_config_schema_table()
    assert "#### `[monitor.host_health]`" in md
    assert "| `mem_avail_min_mb` | `int` | 200 |" in md
    assert "| `disk_warning_pct` | `float` | 90.0 |" in md
    assert "| `disk_critical_pct` | `float` | 95.0 |" in md


def test_given_generated_rows_when_rendered_then_pipes_are_escaped() -> None:
    """`X | None` types and the auth_fail_patterns default both contain `|`."""
    from agent_runner._docgen import render_config_schema_table

    md = render_config_schema_table()
    assert r"| `list` | `list[str] \| None` | None |" in md.splitlines()
    assert r"['\\b(oauth\|unauthorized\|401\|" in md
    for line in md.splitlines():
        if not line.startswith("| "):
            continue
        unescaped = line.replace(r"\|", "").count("|")
        assert unescaped == 4, f"row has {unescaped} unescaped pipes, want 4: {line}"
