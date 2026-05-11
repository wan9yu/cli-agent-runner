from __future__ import annotations

from .conftest import _ssh


def test_given_pi_install_uninstall_lifecycle(
    pi_install_agent_runner: str, pi_workdir: str, pi_config: str,
) -> None:
    project = pi_workdir.split("/")[-1]
    install = (
        f"cd {pi_workdir} && {pi_install_agent_runner} --config {pi_config} install"
    )
    r = _ssh(install, check=False)
    assert r.returncode in (0, 1), f"install rc={r.returncode}: {r.stderr}"
    unit = f"~/.config/systemd/user/agent-runner@{project}.service"
    ls = _ssh(f"ls {unit}", check=False)
    assert ls.returncode == 0, f"unit file missing after install: {ls.stderr}"
    _ssh(f"cd {pi_workdir} && {pi_install_agent_runner} --config {pi_config} uninstall",
         check=False)
    ls2 = _ssh(f"ls {unit}", check=False)
    assert ls2.returncode != 0, "unit file still present after uninstall"
