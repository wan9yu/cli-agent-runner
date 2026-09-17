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
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{pi_workdir}"\n'
        f'log_dir = "{log_dir}"\n'
        "round_budget_s = 10\n"
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


@pytest.fixture
def pi_venv_python(_pi_install_pkg_dir: str) -> str:
    """Path to the venv python3 that ``_pi_install_pkg_dir`` set up to install
    agent-runner. Reused for two things test_e2e_pre_oom.py needs: the
    synthetic growth-child's interpreter, and a one-shot host-pressure probe
    (``agent_runner.metrics.sample()`` is already installed there) -- both
    avoid guessing at a system python3 path, since a root systemd unit has no
    PATH inherited from an interactive shell."""
    return f"{_pi_install_pkg_dir}/.venv/bin/python3"


@pytest.fixture
def pi_growth_script(pi_workdir: str) -> str:
    """Install the paced synthetic memory-growth agent on pi inside
    ``pi_workdir``, for test_e2e_pre_oom.py's real-cgroup pre-OOM property.

    Written as a real script installed via base64 (not an inline ``python -c``
    with escaped newlines). Growth is PACED -- appends+touches ~8 MB every
    ~2.5s -- deliberately: an unpaced allocate loop exhausts memory in ~2s,
    inside the first ~10s mid-round sample tick agent-runner's serve loop
    uses (``_MEM_CHECK_INTERVAL_S``), so the loop would never get a chance to
    sample before the kernel acted first. Grows without bound so it crosses
    the unit's finite MemoryMax and spills into the host's unbounded swap --
    agent-runner terminating it IS the property under test, not a bug here."""
    script_path = f"{pi_workdir}/growth_child.py"
    body = (
        "#!/usr/bin/env python3\n"
        '"""Synthetic pre-OOM growth child for agent-runner\'s gated\n'
        "real-cgroup e2e test (tests/e2e/test_e2e_pre_oom.py). Paced growth is\n"
        "REQUIRED -- see that module's docstring for why an unpaced loop would\n"
        'prove nothing.\n"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import time\n"
        "\n"
        "_CHUNK_BYTES = 8 * 1024 * 1024\n"
        "_PAGE_BYTES = 4096\n"
        "_PACE_S = 2.5\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    chunks: list[bytearray] = []\n"
        "    while True:\n"
        "        block = bytearray(_CHUNK_BYTES)\n"
        "        for i in range(0, _CHUNK_BYTES, _PAGE_BYTES):\n"
        "            block[i] = 1  # touch every page -- an untouched bytearray is "
        "virtual-only\n"
        "        chunks.append(block)\n"
        "        time.sleep(_PACE_S)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    import base64

    encoded = base64.b64encode(body.encode()).decode()
    _ssh(f"echo '{encoded}' | base64 -d > {script_path}")
    return script_path


@pytest.fixture
def pi_pre_oom_config(pi_workdir: str, pi_growth_script: str, pi_venv_python: str) -> str:
    """Write agent-runner.toml on pi pointing ``[agent] command`` at the paced
    growth-child, with the memory.high soft-brake armed
    (``[monitor.host_health.brake] memory_high = true``) so BOTH halves of the
    property under test (the softer brake, or the hard terminate) have a
    write path. Every other ``[monitor.host_health.pressure]`` threshold is
    left at its shipped default -- this test proves the property against the
    REAL calibration, not a loosened one. ``round_budget_s`` is generous
    (900s): the growth child paces itself at ~8 MB/2.5s, so crossing the
    finite MemoryMax and then sustaining critical host swap/PSI pressure for
    3 consecutive ~10s ticks takes real wall-clock minutes."""
    cfg_path = f"{pi_workdir}/agent-runner.toml"
    prompt_path = f"{pi_workdir}/p.md"
    log_dir = f"{pi_workdir}/logs"
    body = (
        # schema_version boot gate (v0.3.0): serve rejects a config without it
        # ("config predates schema_version"). Must be the file's first line,
        # before any [table] header, per TOML top-level-key ordering.
        "schema_version = 1\n"
        "[agent]\n"
        f'command = ["{pi_venv_python}", "{pi_growth_script}"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{pi_workdir}"\n'
        f'log_dir = "{log_dir}"\n'
        "round_budget_s = 900\n"
        "[monitor.host_health.brake]\n"
        "memory_high = true\n"
        "[prompt]\n"
        f'file = "{prompt_path}"\n'
    )
    prompt_body = "Synthetic pre-OOM property e2e -- unused by growth_child.py.\n"
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


@pytest.fixture
def pi_pre_oom_unit(
    pi_workdir: str, pi_install_agent_runner: str, pi_pre_oom_config: str
) -> Iterator[dict]:
    """Start a ROOT system unit for the real-cgroup pre-OOM property test.

    A root system unit, not a ``--user`` unit like ``test_e2e_install_systemd.py``
    uses: the pi e2e host has passwordless sudo but no user-slice cgroup
    delegation, so a ``systemctl --user`` unit can't get a writable memory.high
    leaf there.

    THE SINGLE MOST IMPORTANT CORRECTNESS POINT of this fixture is the cgroup
    shape: ``MemoryMax=150M`` (FINITE) AND ``MemorySwapMax=infinity``
    (UNBOUNDED). Both finite would make
    ``agent_runner.cli._serve_cgroup._probe_and_emit_cgroup_defer`` return
    True at serve boot, and agent-runner would defer its mid-round floor to
    kernel cgroup-OOM instead of arming its own host-pressure terminate --
    the exact path this test exists to prove, so it would prove nothing.
    MemoryMax=150M is only a SAFETY ENVELOPE: it caps the growth child's own
    RAM so it can't coma the host outright; its pages instead spill into the
    host's genuinely unbounded swap, which is what raises HOST-wide
    swap_sout/PSI -- the signal agent-runner's own floor watches.
    ``Delegate=yes`` hands agent-runner write access to its own leaf's
    memory.high (the soft-brake).

    Root (not the pi user) owns this unit's process tree, but ``pi_workdir``'s
    git repo was created by the pi user (see ``pi_workdir`` above) -- every
    round's dirty-check runs ``git status`` against it
    (``runner.py``'s ``vcs_state.detect_dirty_files``), and a bare cross-user
    git invocation refuses with "dubious ownership" and crashes the round on
    an error unrelated to the property under test. A ``safe.directory``
    exception is pre-seeded into root's OWN gitconfig (``sudo -H`` to force
    ``HOME=/root`` for the write, matching this unit's own
    ``Environment=HOME=/root``) before the unit starts."""
    import base64

    unit = f"agent-runner-preoom-{uuid.uuid4().hex[:8]}.service"
    unit_path = f"/etc/systemd/system/{unit}"
    exec_start = f"{pi_install_agent_runner} serve --config {pi_pre_oom_config} --max-rounds 1"
    body = (
        "[Unit]\n"
        "Description=agent-runner pre-OOM property e2e (transient test unit)\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "Environment=HOME=/root\n"
        "MemoryMax=150M\n"
        "MemorySwapMax=infinity\n"
        "Delegate=yes\n"
        f"ExecStart={exec_start}\n"
        "Restart=no\n"
    )
    encoded = base64.b64encode(body.encode()).decode()
    _ssh(f"sudo -H git config --global --add safe.directory {pi_workdir}")
    # The try starts BEFORE the tee/daemon-reload/start write, not after: the
    # unit file can land on disk (tee succeeds) even when daemon-reload or
    # start then fails (Type=simple's start returns non-zero when ExecStart
    # can't launch) -- with the old ordering that partial-setup failure
    # raised past a `try` that hadn't started yet, so `finally` never ran and
    # the unit file leaked on the real host. Starting the try here guarantees
    # the same cleanup fires however far setup got.
    try:
        _ssh(
            f"echo '{encoded}' | base64 -d | sudo tee {unit_path} > /dev/null && "
            "sudo systemctl daemon-reload && "
            f"sudo systemctl start {unit}"
        )
        yield {
            "unit": unit,
            # Delegate=yes does not relocate the unit in the cgroup tree -- a
            # system-scope unit with no Slice= override lands under the
            # default system.slice. NOT independently verified against a
            # live host by this task (no ssh access here) -- a go/no-go
            # verify-item; see the task-4 report.
            "cgroup_path": f"/sys/fs/cgroup/system.slice/{unit}",
        }
    finally:
        # Tolerant of partial setup (check=False on every step): the unit may
        # never have been started, or never even written, depending on how
        # far the try block above got before raising.
        _ssh(f"sudo systemctl stop {unit}", check=False)
        _ssh(f"sudo rm -f {unit_path}", check=False)
        _ssh("sudo systemctl daemon-reload", check=False)
