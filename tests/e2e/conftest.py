"""Pi e2e fixtures — opt-in via AGENT_RUNNER_E2E_PI=1.

Uses the `pi` ssh alias (Tailscale-routed). Each test gets an isolated
work_dir under /tmp on the pi, with the local agent-runner package tarball'd
and installed into a per-test venv.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest

PI_HOST = "pi"
E2E_FLAG = "AGENT_RUNNER_E2E_PI"


def _ssh(cmd: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", PI_HOST, cmd],
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _scp(src: str, dst: str) -> None:
    subprocess.run(
        ["scp", "-q", src, f"{PI_HOST}:{dst}"],
        check=True,
        timeout=120,
    )


@pytest.fixture(scope="session")
def pi_session() -> Iterator[None]:
    if not os.getenv(E2E_FLAG):
        pytest.skip(f"set {E2E_FLAG}=1 to run pi e2e tests")
    _ssh("uname -a")  # smoke: can we ssh?
    yield


@pytest.fixture
def pi_workdir(pi_session) -> Iterator[str]:
    workdir = f"/tmp/agent-runner-e2e-{uuid.uuid4().hex[:8]}"
    _ssh(
        f"mkdir -p {workdir} && cd {workdir} && "
        "git init -q -b main && "
        "git config user.email t@t.com && "
        "git config user.name t && "
        "git config commit.gpgsign false && "
        "echo init > README.md && "
        "git add . && git commit -q -m init"
    )
    try:
        yield workdir
    finally:
        _ssh(f"rm -rf {workdir}", check=False)


@pytest.fixture
def pi_fake_agent(pi_workdir: str) -> str:
    """Install fake-agent.sh on pi inside ``pi_workdir``."""
    script_path = f"{pi_workdir}/fake-agent.sh"
    body = (
        "#!/usr/bin/env bash\n"
        'echo "fake agent on pi" >&2\n'
        'case "${FAKE_AGENT_BEHAVIOR:-succeed}" in\n'
        "  succeed) exit 0 ;;\n"
        '  dirty)   echo x > "$WORK_DIR/dirty.txt"; exit 0 ;;\n'
        "  hang)    sleep 9999 ;;\n"
        "  crash)   exit 137 ;;\n"
        "esac\n"
    )
    # Use base64 to avoid shell quoting hell with the heredoc + bash $vars
    import base64

    encoded = base64.b64encode(body.encode()).decode()
    _ssh(f"echo '{encoded}' | base64 -d > {script_path} && chmod +x {script_path}")
    return script_path


@pytest.fixture(scope="session")
def _pi_install_pkg_dir(pi_session) -> Iterator[str]:
    """Session-scoped: install agent-runner once on pi, share across all tests.

    Per-test would re-tar + re-scp + re-pip-install for every test (~75s each on
    ARMv8 Pi); session scope cuts the suite to one install.

    The installed venv is independent of any test's work_dir — tests invoke the
    binary by absolute path while keeping their per-test sandbox isolated.
    """
    repo_root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tar = f"/tmp/agent-runner-e2e-{uuid.uuid4().hex[:8]}.tar.gz"
    subprocess.run(
        ["tar", "czf", tar, "-C", repo_root, "agent_runner", "pyproject.toml", "README.md"],
        check=True,
    )
    pi_pkg_dir = f"/tmp/agent-runner-e2e-pkg-{uuid.uuid4().hex[:8]}"
    _ssh(f"mkdir -p {pi_pkg_dir}")
    _scp(tar, f"{pi_pkg_dir}/")
    tar_basename = tar.rsplit("/", 1)[-1]
    # hatch-vcs reads version from git metadata; the tarball intentionally
    # excludes .git/ for size. Pretend a version via setuptools-scm's escape
    # hatch so editable install succeeds without VCS metadata on pi.
    _ssh(
        f"cd {pi_pkg_dir} && tar xzf {tar_basename} && "
        "python3 -m venv .venv && "
        "SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+e2e "
        ".venv/bin/pip install -q -e .",
        timeout=600,
    )
    try:
        yield pi_pkg_dir
    finally:
        _ssh(f"rm -rf {pi_pkg_dir}", check=False)


@pytest.fixture
def pi_install_agent_runner(_pi_install_pkg_dir: str) -> str:
    """Path to the agent-runner binary installed once per session on pi."""
    return f"{_pi_install_pkg_dir}/.venv/bin/agent-runner"


@pytest.fixture
def pi_config(pi_workdir: str, pi_fake_agent: str) -> str:
    """Write agent-runner.toml on pi pointing at the fake agent."""
    cfg_path = f"{pi_workdir}/agent-runner.toml"
    prompt_path = f"{pi_workdir}/p.md"
    log_dir = f"{pi_workdir}/logs"
    body = (
        "[agent]\n"
        f'command = ["{pi_fake_agent}"]\n'
        "prompt_arg_template = []\n"
        "[runtime]\n"
        f'work_dir = "{pi_workdir}"\n'
        f'log_dir = "{log_dir}"\n'
        "round_timeout_s = 10\n"
        "[prompt]\n"
        f'file = "{prompt_path}"\n'
    )
    prompt_body = "Test prompt body. " * 50
    import base64

    cfg_b64 = base64.b64encode(body.encode()).decode()
    prompt_b64 = base64.b64encode(prompt_body.encode()).decode()
    _ssh(
        f"echo '{prompt_b64}' | base64 -d > {prompt_path} && "
        f"echo '{cfg_b64}' | base64 -d > {cfg_path} && "
        f"echo 'logs/' > {pi_workdir}/.gitignore && "
        f"cd {pi_workdir} && git add . && git -c commit.gpgsign=false commit -q -m fixture"
    )
    return cfg_path
