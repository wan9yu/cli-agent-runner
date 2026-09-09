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


def test_replace_block_should_replace_content_between_markers_when_markers_exist() -> None:
    text = "intro line\n<!-- gen:foo -->\nOLD CONTENT\n<!-- /gen:foo -->\ntrailing line\n"

    got = replace_block(text, "foo", "NEW CONTENT")

    assert "OLD CONTENT" not in got
    assert "NEW CONTENT" in got
    assert "<!-- gen:foo -->" in got
    assert "<!-- /gen:foo -->" in got
    assert got.startswith("intro line")
    assert got.endswith("trailing line\n")


def test_replace_block_should_return_text_unchanged_when_markers_missing() -> None:
    text = "some markdown without markers\n"

    assert replace_block(text, "missing", "X") == text


def test_replace_block_should_raise_valueerror_when_block_unclosed() -> None:
    text = "<!-- gen:foo -->\nstuff\n(never closes)\n"

    with pytest.raises(ValueError, match="foo"):
        replace_block(text, "foo", "X")


def test_render_defenses_table_should_list_one_row_per_catalog_entry() -> None:
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


def test_render_defenses_table_should_render_paths_as_repo_relative() -> None:
    md = render_defenses_table()

    # No absolute paths should leak — guarded_by is rendered as repo-relative.
    assert "/Users/" not in md
    assert "tests/unit/test_agent_runtime.py" in md  # one known guarded_by


def test_render_should_write_table_when_docs_dir_has_marker(
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


def test_render_should_not_touch_disk_when_write_false(
    tmp_path: Path,
) -> None:
    from agent_runner._docgen import render

    arch = tmp_path / "architecture.md"
    original = "<!-- gen:defenses-table -->\nPLACEHOLDER\n<!-- /gen:defenses-table -->\n"
    arch.write_text(original)

    out = render(docs_dir=tmp_path, write=False)

    assert arch.read_text() == original  # disk unchanged
    assert "round_timeout_s" in out[arch]  # but rendered text returned


def test_render_should_raise_when_gen_name_unknown(tmp_path: Path) -> None:
    from agent_runner._docgen import render

    arch = tmp_path / "x.md"
    arch.write_text("<!-- gen:does-not-exist -->\nfoo\n<!-- /gen:does-not-exist -->\n")

    with pytest.raises(ValueError, match="does-not-exist"):
        render(docs_dir=tmp_path, write=False)


def test_render_should_name_failing_file_in_error_when_marker_unclosed(
    tmp_path: Path,
) -> None:
    from agent_runner._docgen import render

    bad = tmp_path / "bad.md"
    bad.write_text("<!-- gen:defenses-table -->\nstuff but no close\n")

    with pytest.raises(ValueError, match="bad.md"):
        render(docs_dir=tmp_path, write=False)


def test_render_detector_list_should_mark_auto_stop_kinds() -> None:
    from agent_runner._docgen import render_detector_list

    md = render_detector_list()

    # Flag the two auto-stop detectors with **auto-stop**
    assert "oauth_fail" in md
    assert "disk_critical" in md
    assert "**auto-stop**" in md
    # Notify-only kinds get no flag
    assert "timeout_rate" in md


def test_render_event_kinds_list_should_return_bullet_list_of_known_events() -> None:
    from agent_runner._docgen import render_event_kinds_list

    md = render_event_kinds_list()

    bullets = [line for line in md.splitlines() if line.startswith("- ")]
    # >=12 known events (incl. 2 monitor events: alert_emitted, auto_stop_triggered)
    assert len(bullets) >= 12
    assert any("round_start" in line for line in bullets)
    assert any("monitor_alert_emitted" in line for line in bullets)


def test_render_verb_table_should_list_all_subcommands() -> None:
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


def test_render_config_schema_table_should_list_all_sections() -> None:
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


def test_replace_block_should_insert_regex_special_chars_verbatim_when_body_contains_them() -> None:
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


def test_render_config_schema_table_should_include_phases_and_plugins_sections() -> None:
    """_SECTIONS must cover all 7 Config fields — [plugins] disable was undocumented."""
    from agent_runner._docgen import render_config_schema_table

    md = render_config_schema_table()

    assert "### `[phases]`" in md
    assert "### `[plugins]`" in md
    assert "| `overrides` | `dict[str, PhaseOverride]` | {} |" in md
    assert "| `disable` | `list[str]` | [] |" in md
    assert "| `raw` | `dict[str, Any]` | {} |" in md


def test_render_config_schema_table_should_emit_concat_separator_row_as_one_line() -> None:
    """Pins the row shape through the _cell/_field_table refactor.

    render_config_schema_table() already emits this as one line today — the
    3-line shattering in docs/configuration.md happens at WRITE time, in
    replace_block. This test guards the renderer half of that pair.
    """
    from agent_runner._docgen import render_config_schema_table

    md = render_config_schema_table()

    assert r"| `concat_separator` | `str` | '\n\n' |" in md.splitlines()


def test_render_config_schema_table_should_list_host_health_subsection_fields() -> None:
    """[monitor.host_health] is a real TOML sub-table; the parent row is an opaque repr."""
    from agent_runner._docgen import render_config_schema_table

    md = render_config_schema_table()

    assert "#### `[monitor.host_health]`" in md
    assert "| `mem_avail_min_mb` | `int` | 200 |" in md
    assert "| `disk_warning_pct` | `float` | 90.0 |" in md
    assert "| `disk_critical_pct` | `float` | 95.0 |" in md


def test_render_giveup_exit_codes_table_should_list_five_verdicts_with_four_distinct_codes() -> (
    None
):
    from agent_runner import _serve_policy
    from agent_runner._docgen import render_giveup_exit_codes_table

    md = render_giveup_exit_codes_table()

    rows = [line for line in md.splitlines() if line.startswith("| `")]
    assert len(rows) == 5  # 5 verdicts
    codes = {
        "config_broken": _serve_policy.PERMANENT_CONFIG_EXIT,
        "crash_loop": _serve_policy.CRASH_LOOP_EXIT,
        "stalled_no_progress": _serve_policy.CRASH_LOOP_EXIT,
        "mem_loop_persistent": _serve_policy.MEM_LOOP_PERSISTENT_EXIT,
        "mem_loop": _serve_policy.MEM_LOOP_EXIT,
    }
    assert len(set(codes.values())) == 4  # only 4 distinct codes
    for verdict, code in codes.items():
        assert f"`{verdict}` | `{code}`" in md
    # stalled_no_progress shares crash_loop's code — called out inline
    assert "stalled_no_progress` | `75` (shares `crash_loop`'s exit code)" in md
    # mem_loop is the one restartable verdict; the other four stay stopped
    assert "`mem_loop` | `71` | no — restarts" in md
    for verdict in ("config_broken", "crash_loop", "mem_loop_persistent"):
        assert f"`{verdict}` | `{codes[verdict]}` | yes — stays stopped" in md
    assert "(shares `crash_loop`'s exit code) | yes — stays stopped" in md


def test_restart_prevent_exit_status_should_agree_across_unit_docs_and_table(
    tmp_path: Path,
) -> None:
    """``RestartPreventExitStatus`` membership is hand-mirrored in three
    places: ``service_unit.render_serve_unit``'s rendered line (what systemd
    actually enforces), ``_docgen.render_giveup_systemd_example``'s rendered
    line (the runbook's copy of that same line), and
    ``_docgen._giveup_verdict_rows``'s ``stays_stopped`` flags (the runbook's
    exit-code table). Each existing test above/elsewhere checks one site
    against the ``_serve_policy`` constants; none checks that the three sites
    agree with EACH OTHER as a set -- so a future exit code added to one but
    not the others would keep every existing test green while the runbook
    silently omits it."""
    from agent_runner._docgen import _giveup_verdict_rows, render_giveup_systemd_example
    from agent_runner.config import AgentConfig, Config, PromptConfig, RuntimeConfig, VcsConfig
    from agent_runner.service_unit import render_serve_unit

    def _restart_prevent_codes(unit_text: str) -> set[int]:
        line = next(
            ln for ln in unit_text.splitlines() if ln.startswith("RestartPreventExitStatus=")
        )
        return {int(v) for v in line.split("=", 1)[1].split("#", 1)[0].split()}

    cfg = Config(
        agent=AgentConfig(command=["agent"], prompt_arg_template=[]),
        runtime=RuntimeConfig(work_dir=tmp_path, log_dir=tmp_path / "logs"),
        prompt=PromptConfig(file=tmp_path / "p.md"),
        vcs=VcsConfig(),
    )
    unit = render_serve_unit(
        cfg, script_path=tmp_path / "ar", config_path=tmp_path / "agent-runner.toml"
    )

    unit_codes = _restart_prevent_codes(unit)
    doc_example_codes = _restart_prevent_codes(render_giveup_systemd_example())
    table_codes = {
        code for _verdict, code, stays_stopped in _giveup_verdict_rows() if stays_stopped
    }

    assert unit_codes, "sanity: the unit must render a non-empty RestartPreventExitStatus"
    assert unit_codes == doc_example_codes == table_codes


def test_render_giveup_systemd_example_should_match_serve_policy_exit_codes() -> None:
    from agent_runner import _serve_policy
    from agent_runner._docgen import render_giveup_systemd_example

    md = render_giveup_systemd_example()

    assert md.startswith("```ini\n")
    assert md.endswith("```")
    prevent_line = next(ln for ln in md.splitlines() if ln.startswith("RestartPreventExitStatus="))
    values = prevent_line.split("=", 1)[1].split("#", 1)[0].split()
    assert " ".join(values) == (
        f"{_serve_policy.PERMANENT_CONFIG_EXIT} {_serve_policy.CRASH_LOOP_EXIT} "
        f"{_serve_policy.MEM_LOOP_PERSISTENT_EXIT}"
    )
    # mem_loop's exit code is deliberately absent from the RestartPreventExitStatus line
    prevent_codes = {int(v) for v in values}
    assert _serve_policy.MEM_LOOP_EXIT not in prevent_codes
    assert str(_serve_policy.MEM_LOOP_EXIT) in md  # still mentioned, in the trailing comment


def test_render_config_schema_table_should_escape_pipes_in_generated_rows() -> None:
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
