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

## 2026-09-18 round_cgroup_memory corroborating PSI fields

- item: round_cgroup_memory may carry optional flat io_psi_some_avg10, io_psi_full_avg10, and psi_full_total from metrics.sample() at emit time. They are omitted when unread. They are never copied into Pressure.context.
- rationale: plan prefers locking ladder-blindness and defer-field contracts before e2e fixture work. First remaining unfinished item after host_cgroup_memory_limit.defer.
- files: agent_runner/_emit/memory.py, agent_runner/cli/_serve_cgroup.py, tests/unit/test_serve_round_cgroup_spine.py
- verification: `.venv/bin/python -m pytest tests/unit/test_serve_round_cgroup_spine.py tests/unit/test_serve_round_oom_killed.py -q` → 19 passed; `.venv/bin/ruff check . && .venv/bin/ruff format --check .` clean
- notes: assumed sample() runs only on the successful emit path (skip-no-op paths do not sample); each None key is omitted independently. Did not edit host_health.py, events.md, or runbook.md. Pre-existing pi-lens typing findings in _serve_cgroup left untouched.

## 2026-09-18 extract growth_child.py

- item: The paced pre-OOM child lives in tests/e2e/growth_child.py (8 MiB every 2.5s, page-touch). pi_growth_script installs that file instead of an inlined string. Importing the module does not start the infinite loop.
- rationale: plan prefers extracting growth_child.py after ladder-blindness/defer-field contracts, before skip-closed control tests. First remaining unfinished item.
- files: tests/e2e/growth_child.py, tests/e2e/conftest.py, tests/unit/test_growth_child_constants.py
- verification: `.venv/bin/python -m pytest tests/unit/test_growth_child_constants.py -q` → 1 passed; `.venv/bin/ruff check . && .venv/bin/ruff format --check .` clean
- notes: assumed the constants test lives under tests/unit because CI and build.sh --ignore=tests/e2e. pi_growth_script still base64-over-ssh (same install path), reading tests/e2e/growth_child.py bytes instead of an inlined string. Importing the module does not enter main().

## 2026-09-18 skip-closed control-arm e2e

- item: A skip-closed control-arm e2e exists: same unit as treatment except MemorySwapMax=0, MemoryHigh=120M kept, no oom.group and no OOMPolicy=kill. Unset AGENT_RUNNER_E2E_CGROUP and AGENT_RUNNER_E2E_PI, the new test skips. Assertions in source require defer true, no round_mem_terminated, no memory_high_engaged, events_oom_kill_delta>=1 or INVALID, DEFER-FIRST or KERNEL-FIRST named, growth child gone, host still answers.
- rationale: plan prefers skip-closed control tests after extracting growth_child.py. First remaining unfinished item.
- files: tests/e2e/conftest.py, tests/e2e/test_e2e_pre_oom.py
- verification: `.venv/bin/python -m pytest tests/e2e/test_e2e_pre_oom.py -q` → 3 skipped; `.venv/bin/ruff check . && .venv/bin/ruff format --check .` clean
- notes: assumed PI control pytest timeout includes +600 install budget matching treatment; local uses +120 per plan. Two thin wrappers (PI + local CGROUP) share `_assert_control`. Skip-closed (unset env => skip) is the honest local proof; did not export e2e flags or run sudo/systemctl. Did not add OOMPolicy or memory.oom.group.

## 2026-09-18 SECURITY reframe + CHANGELOG stub

- item: Root SECURITY.md gains one paragraph: isolation is the operator's job (exec_prefix, dedicated user, no passwordless sudo, egress); agent-runner stays lifecycle-safety. CHANGELOG adds ## [0.3.13] - UNRELEASED. No docs/SECURITY.md. Optional results template under gitignored docs/internal/notes is allowed and must not be staged.
- rationale: plan says docs last. Last remaining unfinished item.
- files: SECURITY.md, CHANGELOG.md
- verification: `.venv/bin/python -m pytest tests/invariants/test_docs_index_complete.py tests/invariants/test_doc_claims_match_ssot.py -q` → 4 passed; `.venv/bin/ruff check . && .venv/bin/ruff format --check .` clean; `docs/SECURITY.md` absent
- notes: assumed not naming a competitor in SECURITY.md (Ralph item asks for the operator-vs-lifecycle split). Did not edit runbook.md (not in the Ralph item). Results template written under gitignored docs/internal/notes and not staged. Did not claim uncapped-before-coma or a live two-arm run.
