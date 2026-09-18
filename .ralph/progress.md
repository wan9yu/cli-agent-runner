## 2026-09-18 sample() IO-PSI + psi_full_total

- item: metrics.sample() always includes psi_full_total, io_psi_some_avg10, and io_psi_full_avg10 (None when unread). _read_psi returns (some_avg10, full_avg10, full_total). host_health.memory_pressure verdict and context stay identical when those extra keys are injected.
- rationale: plan prefers ladder-blindness before defer-field / e2e fixture work. First unfinished item.
- files: agent_runner/metrics.py, tests/unit/test_metrics.py, tests/unit/test_host_health.py
- verification: `.venv/bin/python -m pytest tests/unit/test_metrics.py tests/unit/test_host_health.py tests/unit/test_monitor_detectors.py -q` → 76 passed; `.venv/bin/ruff check . && .venv/bin/ruff format --check .` clean
- notes: did not edit host_health.py or _NEW_MEM_SAMPLE_KEYS. Pre-existing pi-lens findings in cgroup_delegated / _brake_high_value left untouched (outside this item).

## 2026-09-18 host_cgroup_memory_limit.defer bool

- item: host_cgroup_memory_limit events carry defer as a bool equal to _probe_and_emit_cgroup_defer's return value (True when both caps are finite and plausible, including MemorySwapMax=0).
- rationale: plan prefers locking the defer-field contract before e2e fixture work. First remaining unfinished item after sample() IO-PSI.
- files: agent_runner/_emit/memory.py, agent_runner/cli/_serve_cgroup.py, tests/unit/test_cgroup_probe.py, tests/e2e/test_e2e_pre_oom.py
- verification: `.venv/bin/python -m pytest tests/unit/test_cgroup_probe.py -q` → 55 passed; `.venv/bin/ruff check . && .venv/bin/ruff format --check .` clean
- notes: defer is a required emit kwarg (every probe is yes/no). Did not touch runbook.md (docs item). Pre-existing pi-lens typing findings in _serve_cgroup left untouched.
