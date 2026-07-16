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


def test_stdin_mode_delivers_large_prompt_without_hanging(tmp_path):
    # Prompt exceeds the OS pipe buffer (~64KB); if the write were still
    # blocking on the main thread before the poll loop, an agent that reads
    # stdin only after some delay (or a full-pipe write) could hang run()
    # with no timeout_s protection. The write now happens on a daemon
    # thread, so run() reaches the poll loop immediately regardless.
    fake = _fake_agent(tmp_path)
    log_path = tmp_path / "round.log"
    marker = "LARGE_PROMPT_MARKER"
    big_prompt = "x" * 100_000 + marker
    res = agent_runtime.run(
        command=[sys.executable, str(fake)],
        prompt_arg_template=["-p"],
        prompt=big_prompt,
        prompt_delivery="stdin",
        timeout_s=15,
        log_path=log_path,
        env_extra={},
    )
    assert res.exit_code == 0
    assert not res.timed_out
    stdin_line = next(
        line for line in log_path.read_text().splitlines() if line.startswith("STDIN:")
    )
    assert marker in stdin_line  # fully delivered despite exceeding pipe buffer


def test_stdin_mode_guards_against_prompt_in_argv_template(tmp_path):
    # Config validation already rejects {prompt} in the template for stdin
    # mode, but run() itself must never substitute {prompt} into argv in
    # stdin mode, even if called directly with a mismatched template.
    fake = _fake_agent(tmp_path)
    log_path = tmp_path / "round.log"
    marker = "SECRET_MARKER"
    agent_runtime.run(
        command=[sys.executable, str(fake)],
        prompt_arg_template=["-p", "{prompt}"],
        prompt=marker,
        prompt_delivery="stdin",
        timeout_s=15,
        log_path=log_path,
        env_extra={},
    )
    argv_line = next(line for line in log_path.read_text().splitlines() if line.startswith("ARGV:"))
    assert marker not in argv_line  # runtime guard holds even with a bad template
