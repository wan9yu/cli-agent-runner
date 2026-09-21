"""Public docs must not re-lie about cgroup defer vs admission pause.

architecture.md once said defer is True only when the own leaf has both
caps; runbook said "this process's cgroup has both set". Source of truth
is metrics.cgroup_memory_limits (min finite across leaf and ancestors)
plus _probe_and_emit_cgroup_defer's plausibility guards.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
_SKIP_DIRS = {"internal", "migrations", "marketing"}

# Phrases that shipped as overclaims. "own leaf" alone is still correct for
# the soft-brake write and for cgroup_path = leaf.
_FORBIDDEN = (
    "own leaf has both",
    "on_alert is 0.3",
    "plugin-configurable admission lever",
    "this process's cgroup has both `memory.max` and `memory.swap.max` set",
)


def _published_docs() -> list[Path]:
    out: list[Path] = []
    for path in DOCS.rglob("*.md"):
        if path.relative_to(DOCS).parts[0] in _SKIP_DIRS:
            continue
        out.append(path)
    return out


def test_published_docs_should_not_repeat_defer_overclaims() -> None:
    hits: list[str] = []
    for path in _published_docs():
        text = path.read_text(encoding="utf-8")
        for phrase in _FORBIDDEN:
            if phrase in text:
                hits.append(f"{path.relative_to(REPO).as_posix()}: {phrase!r}")
    assert not hits, "defer overclaim returned:\n" + "\n".join(hits)


def test_architecture_should_describe_effective_budget_and_three_names() -> None:
    text = (DOCS / "architecture.md").read_text(encoding="utf-8")
    assert "leaf **and ancestors**" in text
    assert "host_cgroup_memory_limit.defer" in text
    assert "`round_deferred`" in text
    assert "`mem_pressure_deferred_to_cgroup`" in text
    assert "bounding_cgroup_path" not in text


def test_runbook_should_describe_effective_budget_for_cgroup_defer() -> None:
    text = (DOCS / "runbook.md").read_text(encoding="utf-8")
    assert "host_cgroup_memory_limit.defer" in text
    assert "leaf **and ancestors**" in text
    assert "bounding_cgroup_path" in text
    assert "own leaf cgroup" in text


def test_config_digest_docs_should_hash_paths_and_bytes() -> None:
    cfg = (DOCS / "configuration.md").read_text(encoding="utf-8")
    recipe = (REPO / "examples" / "between_rounds" / "README.md").read_text(encoding="utf-8")
    events = (DOCS / "events.md").read_text(encoding="utf-8")
    assert "paths and bytes" in cfg
    assert "paths and bytes" in recipe
    assert "paths+bytes" in events
    assert "never a kill input" in events
