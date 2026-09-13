"""No-green-skip guard: the Landlock+seccomp kill-tests must degrade to a STRICT
xfail off a confinement-capable host, never a plain skip. A plain skip would let
a runner that IS capable silently pass without ever asserting the denial -- the
exact failure mode this whole task exists to prevent. strict=True additionally
turns an unexpected pass on an 'incapable' host into a failure, so the xfail
condition can never quietly rot into always-true."""

from __future__ import annotations

from pathlib import Path

_KILL = Path(__file__).resolve().parent.parent / "linux" / "test_plugin_sandbox_kill.py"


def test_kill_test_module_should_declare_strict_xfail_not_a_skip() -> None:
    src = _KILL.read_text(encoding="utf-8")

    assert "strict=True" in src
    assert "xfail" in src
    assert "pytest.mark.skip" not in src
