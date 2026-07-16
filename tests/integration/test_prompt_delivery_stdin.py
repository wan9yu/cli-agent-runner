import sys
import textwrap
from pathlib import Path

from agent_runner import agent_runtime


def _fake_agent(tmp_path: Path) -> Path:
    # Echoes its own argv and whatever it reads from stdin, into stdout.
    p = tmp_path / "fake_agent.py"
    p.write_text(
        textwrap.dedent("""
        import sys
        sys.stdout.write("ARGV:" + "\\x1f".join(sys.argv) + "\\n")
        sys.stdout.write("STDIN:" + sys.stdin.read() + "\\n")
    """)
    )
    return p


def test_stdin_mode_keeps_prompt_out_of_argv(tmp_path):
    fake = _fake_agent(tmp_path)
    log_path = tmp_path / "round.log"
    marker = "PYTEST_MARKER_TOKEN_XYZ"
    res = agent_runtime.run(
        command=[sys.executable, str(fake)],
        prompt_arg_template=["-p"],
        prompt=f"do the thing {marker}",
        prompt_delivery="stdin",
        timeout_s=15,
        log_path=log_path,
        env_extra={},
    )
    out = log_path.read_text()
    assert res.exit_code == 0
    argv_line = next(line for line in out.splitlines() if line.startswith("ARGV:"))
    stdin_line = next(line for line in out.splitlines() if line.startswith("STDIN:"))
    assert marker not in argv_line  # prompt NOT in argv
    assert marker in stdin_line  # prompt received on stdin


def test_argv_mode_unchanged(tmp_path):
    fake = _fake_agent(tmp_path)
    log_path = tmp_path / "round.log"
    marker = "ARGV_MARKER_TOKEN"
    agent_runtime.run(
        command=[sys.executable, str(fake)],
        prompt_arg_template=["-p", "{prompt}"],
        prompt=f"hello {marker}",
        prompt_delivery="argv",
        timeout_s=15,
        log_path=log_path,
        env_extra={},
    )
    argv_line = next(line for line in log_path.read_text().splitlines() if line.startswith("ARGV:"))
    assert marker in argv_line  # default argv behavior intact
