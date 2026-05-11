from __future__ import annotations

from .conftest import _ssh


def test_given_seeded_disk_critical_on_pi_when_local_monitor_polls_then_alert(
    pi_install_agent_runner: str,
    pi_workdir: str,
    pi_config: str,
) -> None:
    seed_metric = (
        '{"ts":"2026-05-12T10:00:00.000Z","event":"round_end",'
        '"mem_total_mb":463,"mem_available_mb":300,"disk_used_pct":98.0,"disk_free_gb":1.0}'
    )
    log_dir = f"{pi_workdir}/logs"
    _ssh(f"mkdir -p {log_dir} && echo '{seed_metric}' > {log_dir}/metrics-2026-05.jsonl")
    _ssh(f"echo '{{}}' > {log_dir}/status.json")
    monitor_cmd = (
        f"timeout 5 {pi_install_agent_runner} --config {pi_config} monitor --interval 1 --json"
    )
    r = _ssh(monitor_cmd, check=False)
    output = r.stdout
    assert "disk_critical" in output, f"no disk_critical alert: {output}"
