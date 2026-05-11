from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner._docgen import render_defenses_table, replace_block


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
    # Eleven entries
    rows = [
        line
        for line in md.splitlines()
        if line.startswith("| ") and "Defense" not in line and "---" not in line[:5]
    ]
    assert len(rows) == 11
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


def test_given_render_alert_kinds_list_when_called_then_returns_bullet_list() -> None:
    from agent_runner._docgen import render_alert_kinds_list

    md = render_alert_kinds_list()
    # Bullet list, alphabetised, 9 entries
    bullets = [line for line in md.splitlines() if line.startswith("- ")]
    assert len(bullets) == 9
    assert any("oauth_fail" in line for line in bullets)
    assert any("disk_critical" in line for line in bullets)


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
    # 14 known events (including 2 Phase 2 monitor events)
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
        "cancel",
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
    assert "stash" in md  # vcs.orphan_action default
