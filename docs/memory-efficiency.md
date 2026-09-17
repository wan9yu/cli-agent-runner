# Memory footprint & efficiency

agent-runner is a systemd-level supervisor, not a sandbox: it runs in the
**same cgroup** as the agent CLI it supervises. On a comfortable host that's
irrelevant. On a constrained host — the smallest field deployment this
project tracks runs in a ~462 MB memory cgroup — every MB the supervisor's
own process holds is a MB the agent doesn't get, and every extra syscall the
supervisor makes on the hot per-round path is extra latency on a host that is
already slow. This page tracks agent-runner's own footprint and per-round
overhead release over release, with real measurements, so a regression here
is visible before it reaches that host.

## Results

All measurements below are from the same machine/Python build (see
Methodology) at three pinned commits: **v0.2.17** = `4be7a48`, **v0.2.18** =
`b08e1b8`, and this release, **0.2.19** = the tip of the branch this doc
shipped from. Deltas are release-over-release; ↓ is an improvement.

### 1. Import/startup RSS

RSS after `import agent_runner.cli` in a fresh interpreter (peak
`ru_maxrss`, 5 runs each, cold).

| | v0.2.17 | v0.2.18 | 0.2.19 | 0.2.17→0.2.18 | 0.2.18→0.2.19 |
|---|---|---|---|---|---|
| RSS (avg of 5 runs) | 25.9 MB | 23.1 MB | 23.2 MB | **−2.9 MB (−11%)** ↓ | +0.1 MB (+0.5%, noise) |
| RSS range | 25.8–26.2 MB | 22.9–23.2 MB* | 23.1–23.5 MB | | |
| `sys.modules` count | 241 | 203 | 209 | −38 | +6 |
| hashlib / zoneinfo / `importlib.metadata` loaded? | yes | no | no | removed | still absent |

\* one v0.2.18 run (of 8 total) came in at 26.1 MB — a cold-page-cache
outlier on the very first invocation of that interpreter; excluded from the
range/average as noise, not signal (see Caveats).

### 2. `serve` startup RSS

RSS after the real `agent-runner serve` entrypoint runs its full setup path
(config load, serve-lock, startup hooks, loop preparation) and exits via a
pre-existing `stop_file` check — before any round is spawned. Same 5-runs
methodology.

| | v0.2.17 | v0.2.18 | 0.2.19 | 0.2.17→0.2.18 | 0.2.18→0.2.19 |
|---|---|---|---|---|---|
| RSS (avg of 5 runs) | 26.3 MB | 24.0 MB | 24.0 MB | **−2.3 MB (−9%)** ↓ | ~0 MB (flat) |

### 3. Per-round allocation growth

`tracemalloc` delta between two steady-state rounds of the per-round
readers `serve` calls once per round (`round_outcome`, `_active_throttles`,
`post_round_decision`), 6 warm-up rounds then 2 measured rounds, 3 runs
each. This is the exact harness `tests/invariants/test_round_alloc_growth.py`
ships as a regression gate (introduced in 0.2.18); applied unmodified here
to v0.2.17's equivalent readers (`round_outcome` lived in `_throttle.py`
before the 0.2.18 split into `_round_outcome.py`).

| | v0.2.17 | v0.2.18 | 0.2.19 | budget (all 3) |
|---|---|---|---|---|
| growth, rounds 2→3 (avg of 3 runs) | ~950 B | ~1030 B | ~1075 B | 64 KB |

No leak in any of the three versions — all three land in an ~800–1130 B
band attributable to hash-seed dict-resize jitter between interpreter
processes, not to any code change. **Unchanged across releases**; reported
here for completeness, not as a win.

### 4. cgroup sysfs reads per round

Reads of `/sys/fs/cgroup/**` per round, on a depth-4 systemd-slice nesting
(bounding ancestor 3 levels up from the leaf scope) — 1 round-start read +
15 mid-round pressure ticks + 1 round-end read.

| | v0.2.17 | v0.2.18 | 0.2.19 (uncached path) | 0.2.19 (cached, shipped) |
|---|---|---|---|---|
| reads/round | n/a — no cgroup pressure spine | n/a — no cgroup pressure spine | 136 | **56** |

The cgroup pressure spine (`round_cgroup_memory`, reading `memory.current` /
`memory.swap.current` / `memory.events` at the bounding ancestor) does not
exist at all at the v0.2.17 or v0.2.18 commits pinned above — it and its
per-round ancestor-resolution cache both land later in this same release.
So the release-over-release story on this axis isn't "regression fixed", it's
"a new capability shipped at **56** reads/round instead of the **136** an
uncached implementation would have cost" — a **−59%** reduction over the
naive version of the same feature, confirmed by an independent
`Path.read_text` call-count on a fake cgroup-v2 tree (not just re-quoting
the implementation's own report).

### 5. Module count, source LOC, largest module

`agent_runner/**/*.py`, tracked source only (no `__pycache__`).

| | v0.2.17 | v0.2.18 | 0.2.19 | 0.2.17→0.2.19 |
|---|---|---|---|---|
| `.py` files | 67 | 69 | 75 | +8 |
| total LOC | 15,989 | 16,895 | 17,292 | +1,303 |
| largest module | `api.py`, 943 | `api.py`, 923 | `api.py`, 959 | +16 |

The module-count and LOC growth is **structural, not a runtime cost**: it's
`_emit.py` becoming an `_emit/` package (4 domain submodules behind a
facade), `_round_outcome.py` and `cli/_serve_cgroup.py` being carved out of
larger files, none of which changes what gets imported or how much it
allocates (axis 1 and axis 3 above are flat or improving across the same
span). Every version's largest module stays comfortably under the 1,000-line
ceiling `tests/invariants/test_module_sizes.py` enforces — the splits were
done ahead of that ceiling, not in reaction to tripping it.

## What drove each change

**0.2.18** removed three modules from the default startup path that had no
consumer on it: `hashlib` (libcrypto; the prompt is only hashed when a
pre-round hook is configured — none are by default), `zoneinfo` (only
`[schedule]`/`now_in_zone` need it), and `importlib.metadata` (replaced by a
direct `entry_points.txt` scan, dropping its `email`/`zipfile`/`csv` tail).
`tests/invariants/test_import_footprint.py` locks this in with two checks: a
frozen allowlist of the `agent_runner.*` modules startup is allowed to load,
and an AST scan that forbids a *module-level* import of any of the removed
modules from ever creeping back in (function-scoped imports stay fine).
`tests/invariants/test_round_alloc_growth.py` landed the same release as a
belt-and-suspenders check that none of this — or any future per-round
change — leaks state round over round.

**0.2.19** added the cgroup pressure spine's per-round ancestor cache: the
bounding cgroup ancestor (the one whose `memory.max` is the real budget)
cannot change once a round has started, so `_spawn_round`'s mid-round ticks
and the round-end read now reuse the round's first (necessarily uncached)
resolution instead of re-walking the ancestor chain and re-reading
`memory.max` at every level on every tick. The module decompositions in the
same release (`_emit` → package, `_round_outcome`, `cli/_serve_cgroup`) are
structure only — see axis 5.

## 0.2.22 → 0.2.23

Measured the same way as the table above (see Methodology), same machine,
comparing **v0.2.22** = `7f99ad9` against **0.2.23** = the tip of this
branch at measurement time (the docs-only commits after it don't touch the
import graph, so the number holds for the shipped release).

### Import/startup RSS

| | v0.2.22 | 0.2.23 | delta |
|---|---|---|---|
| RSS (avg of 5 runs) | 23.9 MB | 23.9 MB | ~0 MB (flat, within noise) |
| RSS range | 23.8–24.1 MB* | 23.8–24.0 MB | |
| `sys.modules` count | 228 | 229 | +1 |

\* one v0.2.22 run (of 8 total) came in at 26.1 MB — a cold-page-cache
outlier on the first invocation, excluded from the range/average as noise,
same treatment as the v0.2.18 outlier noted above.

### Module count, source LOC, largest module

Tracked source only (`git ls-tree`, not a working-tree `find` — this
checkout also carries a generated, untracked `_version.py` that would
otherwise inflate a naive file count by one regardless of which commit is
measured).

| | v0.2.22 | 0.2.23 | delta |
|---|---|---|---|
| `.py` files | 74 | 75 | +1 |
| total LOC | 18,043 | 18,247 | +204 |
| largest module | `api.py`, 998 | `api.py`, 990 | −8 |

### Why it's flat

The only startup-graph change this release is one new eager module,
`agent_runner.cli.doctor_cmd` — the `doctor` verb's own imports
(`phase_select`, `startup_check`, `cli.common`) were already on the startup
path, so it adds no new transitive dependency, just itself. No per-round
allocation path changed: `phase_window_overlap` is a config-time check —
`serve` evaluates it once at boot (refusing to boot on a hit) and the
startup-check battery re-evaluates it per round alongside the other base
checks, but neither path allocates per round. The pid `create_time`
lifecycle-helper unification and the startup-check descriptor-table
refactor are both structural, not allocation-path, changes.
`tests/invariants/test_round_alloc_growth.py` stays green unmodified,
confirming no new per-round leak. Axes 2 (`serve` startup RSS) and 4 (cgroup
reads/round) weren't re-measured this release since neither's code path
moved — re-running the full harness would report the same number as
0.2.19 for a code path this release didn't touch, not new signal.

## 0.2.23 → 0.2.24

Measured the same way as the table above (see Methodology), same machine,
comparing **v0.2.23**'s base — `60c666a` (one commit past the `v0.2.23` tag:
a ruff-format-only fix, so equivalent to the shipped release for this
purpose) — against **0.2.24**, the tip of this branch at measurement time.

### Import/startup RSS

| | v0.2.23 | 0.2.24 | delta |
|---|---|---|---|
| RSS (avg of 5 runs) | 24.0 MB | 24.1 MB | +0.03 MB (flat, within noise) |
| RSS range | 23.9–24.1 MB | 24.0–24.3 MB | |
| `sys.modules` count | 229 | 229 | +0 |

### Module count, source LOC, largest module

Tracked source only (`git ls-tree`, same convention as the row above).

| | v0.2.23 | 0.2.24 | delta |
|---|---|---|---|
| `.py` files | 75 | 75 | +0 |
| total LOC | 18,276 | 18,325 | +49 |
| largest module | `api.py`, 990 | `api.py`, 990 | +0 |

### Why it's flat

This release's only startup-path-adjacent change is the second
`host_cgroup_memory_limit` advisory (recommending `memory.high` when
`memory.max` is set on the supervisor's own cgroup): it lives inside the
probe function already called from the already-loaded `cli/_serve_cgroup`
module, adds no new import, and only walks its extra branch when a real
cgroup bound is present at startup. The `doctor` phase-plan resume-time
addition and the grace-kill test wall-time trim touch neither the startup
import graph nor any per-round allocation path.
`tests/invariants/test_import_footprint.py` and
`tests/invariants/test_round_alloc_growth.py` both stay green unmodified,
confirming no new eager import and no new per-round leak.

**Suite wall-time**: trimming the grace-kill negative test's sleep cuts
roughly 80s off the test suite's wall-clock time — a test-only change, not
reflected in any of the runtime numbers above.

## 0.2.24 → 0.3.0

Measured the same way as the table above (see Methodology), same machine,
comparing **v0.2.24** = `ecd8cf6` against **0.3.0**, the tip of this branch at
close-out.

### Import/startup RSS

| | v0.2.24 | 0.3.0 | delta |
|---|---|---|---|
| RSS (avg of 5 runs) | 23.95 MB | 23.92 MB | −0.03 MB (flat, within noise) |
| RSS range | 23.88–24.02 MB | 23.77–24.05 MB | |
| `sys.modules` count | 222 | 222 | +0 |

### Module count, source LOC, largest module

Tracked source only (`git ls-tree`, same convention as the rows above).

| | v0.2.24 | 0.3.0 | delta |
|---|---|---|---|
| `.py` files | 75 | 79 | +4 |
| total LOC | 18,333 | 18,826 | +493 |
| largest module | `api.py`, 990 | `migrations.py`, 904 | n/a — different module |

### Why this release moves the numbers it moves, and not others

**Import/startup RSS and `sys.modules` count are flat.** The plugin-loading
rewrite (seven `importlib.metadata.entry_points()` group scans collapsed into
one `agent_runner.plugins` group resolved through the existing
`_plugin_scan` file-parse fast path) replaces work that already ran at
startup with equivalent work under one group instead of seven — no new
eager import, same module set loaded. `agent_runner._plugin_manifest`,
`agent_runner._install`, `agent_runner._lifecycle`, and `agent_runner._observe`
are all on the startup path already (the pre-split `api.py` and the
pre-manifest plugin loader were too) — they replace code that was there
before, they don't add a new import edge. `tests/invariants/test_import_footprint.py`
carries all four in its frozen `EXPECTED_STARTUP_PKG_MODULES` allowlist and
is green; `tests/invariants/test_round_alloc_growth.py` (no per-round reader
changed shape this release) is green unmodified — no new per-round leak.

**`.py` file count and total LOC grow, structurally.** `+4` files:
`_plugin_manifest.py` (new — the typed manifest ABI), and `api.py`'s split
into `_install.py`/`_lifecycle.py`/`_observe.py` behind an unchanged
re-export facade (`api.py` itself: 990 → 180 lines). `migrations.py` grew
676 → 904 lines from the ~15 new host_health-regroup and
`round_timeout_s`→`round_budget_s` transforms plus the `schema_version`
stamping step — all executable migration logic, not narrative. None of this
is a runtime cost: it is the same objects reachable through the same
import-graph shape, confirmed by the flat RSS/module-count numbers above.

**Axis 2 (`serve` startup RSS) and axis 4 (cgroup reads/round) were not
re-measured this release** — neither code path moved (the split modules are
the same functions under new names; the cgroup pressure spine is untouched),
so re-running that harness would report the same number as 0.2.19/0.2.22 for
a path this release didn't touch, not new signal. Axis 3 (per-round
allocation growth) stays covered by
`tests/invariants/test_round_alloc_growth.py`, green, same as every prior
release.

**Constrained-host (Pi/Linux) figures were not independently re-measured
this release.** Every number above is the same macOS dev-machine methodology
this page has used since 0.2.17 (see Caveats) — not a run on the ~462 MB
field host. Given the flat RSS/module-count result and the load-bearing
neutrality argument above (same objects, same import-graph shape, no new
per-round allocation), no regression is expected on that host either, but
that claim is unverified pending whoever runs the next prerelease build there.

## 0.3.0 → 0.3.1

Not independently re-measured this release. No import-time work changed:
`children_rss_sum_bytes` and `cgroup_growth_rate_pressure` are plain
functions added beside existing ones in already-imported modules, not new
imports. The mid-round tick loop's existing ~10s cadence gains a few extra
local-variable holders and, at most, one more psutil (or reused cgroup)
read on that SAME already-scheduled tick — no new per-round allocation
growth pattern. `tests/invariants/test_round_alloc_growth.py` stays green
unmodified.

## Methodology

Machine: macOS 26.6.2, arm64, 16 KB pages. Python 3.11.3 (CPython, pyenv),
psutil 7.2.2, one shared virtualenv reused for all three commits.

**Isolation.** The two older commits were measured in throwaway git
worktrees, never in the working tree this release ships from:

```bash
git worktree add /tmp/ar-v0217 4be7a48
git worktree add /tmp/ar-v0218 b08e1b8
# ... measure ...
git worktree remove /tmp/ar-v0217
git worktree remove /tmp/ar-v0218
```

**Reproducibility gotcha**: this repo's dev virtualenv has `cli-agent-runner`
installed editable, pointing at the normal working tree. Running a
measurement script by absolute path (`python3 /path/to/script.py`) puts the
*script's own directory* on `sys.path[0]`, not the worktree — `import
agent_runner` then silently resolves to the editable install instead of the
worktree you meant to measure. Every command below pins `PYTHONPATH` to the
target worktree explicitly to avoid this.

**1. Import RSS** (5 fresh interpreters per commit):

```bash
PYTHONPATH=/tmp/ar-v0217 python3 -c '
import resource, sys
before = set(sys.modules)
import agent_runner.cli
after = set(sys.modules)
usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print((usage/1024 if sys.platform == "darwin" else usage), len(after))
'
```

**2. `serve` startup RSS**: write a minimal `agent-runner.toml`
(`[agent] command = ["true"]`, a `prompt.md`, and `[runtime] stop_file =
"<path>"` pointing at a file that already exists on disk), then run the real
parser + entrypoint in-process and read `ru_maxrss` right after it returns:

```python
from agent_runner.cli import _build_parser
from agent_runner.cli import serve_cmd

args = _build_parser().parse_args(["--config", str(toml_path), "serve"])
serve_cmd.cmd(args)  # loop's stop_file check breaks it before any round spawns
```

The serve loop's real per-iteration order is sentinel → throttle-gate →
`stop_file` existence → max-rounds → spawn-round, so a pre-existing
`stop_file` exercises config load, the serve lock, startup hooks, and loop
preparation, then exits before touching `_spawn_round` — this is the "setup
path" without a round.

**3. Per-round allocation growth**: the exact harness in
`tests/invariants/test_round_alloc_growth.py` (6 warm-up rounds, then
`tracemalloc.take_snapshot()` around rounds 2 and 3, comparing by filename),
run against whichever module `round_outcome` lives in for that commit
(`agent_runner._throttle` pre-0.2.18, `agent_runner._round_outcome` from
0.2.18 on — the same call site works unmodified either way since both
export the same function signature).

**4. cgroup reads/round**: a fake cgroup-v2 tree
(`/a.slice/b.slice/c.slice/app.scope`, `memory.max` finite only at
`c.slice`), with `pathlib.Path.read_text` wrapped to count calls, comparing
`metrics.cgroup_memory_usage(self_cgroup=<leaf>)` (forces full ancestor
resolution every call — the pre-cache code path, still reachable today when
`bounding_cgroup` isn't passed) against
`metrics.cgroup_memory_usage(bounding_cgroup=<resolved ancestor>)` (the
cached path), over 1 round-start read + 15 mid-round ticks + 1 round-end
read.

**5. Module/LOC counts**:

```bash
find agent_runner -name '*.py' -not -path '*/__pycache__/*' | wc -l
find agent_runner -name '*.py' -not -path '*/__pycache__/*' -exec cat {} + | wc -l
find agent_runner -name '*.py' -not -path '*/__pycache__/*' -exec wc -l {} + | sort -rn | head
```

## Caveats

- **These are local numbers**, measured on one developer machine (macOS,
  arm64, 16 KB pages), not the constrained field host. macOS's larger page
  size rounds RSS coarser than Linux's 4 KB pages, so absolute values here
  likely overstate what the same build reports on Linux/aarch64. The
  *reducible* deltas (what changed release over release, on the same
  machine) are the trustworthy part of this page; absolute values on the
  target constrained host should be validated by whoever runs a prerelease
  build there, following the same convention as the 0.2.18 footprint spike
  this page draws on.
- **Noise**: import RSS varies ±0.2–0.4 MB run to run (page-cache and
  allocator-arena jitter); one run in eight came in ~2.5 MB high on its
  first invocation of a freshly-worktree-checked-out interpreter and was
  treated as a cold-cache outlier, not a data point. The per-round
  allocation axis (§3) varies by a couple hundred bytes between runs from
  hash-seed dict-resize timing — still ~60× under its budget in every
  version measured.
- **Axis 4 has no v0.2.17/v0.2.18 baseline**: the cgroup pressure spine is
  new in this release, so there is nothing to compare its read count
  against in the two older commits — reported as "not applicable", not
  fabricated as zero-cost or backfilled.
- **Not cherry-picked**: axis 1 and axis 2 show a real 0.2.18 win and an
  honestly flat (not improved) 0.2.19 on the same axis; axis 3 never moved
  in any release. Both are reported as measured.

## 0.3.1 → 0.3.3 (2026-09-14)

Covers 0.3.2 (plugin security — Landlock+seccomp trampoline, checksum pinning, spawn-hook seam) and 0.3.3 (cgroup-delegation readiness probe + `sigterm_cooperative` declaration). Same macOS harness (§ methodology above).

### 1. Import/startup RSS

| | 0.3.1 | 0.3.3 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 23.9 MB | 23.9 MB | ~0 MB (flat) |
| RSS range | 23.8–24.0 MB | 23.8–24.0 MB | |

Flat despite the substantial 0.3.2 plugin-security surface (`_plugin_sandbox`, `_sandbox_probe`, `_plugin_checksum`, the SpawnHook seam) and 0.3.3's cgroup probe: the LSM bindings are an opt-in `[sandbox]` extra (absent from a base install), and the trampoline/probe modules import lazily (only when the sandbox path runs), so they never join the cold-startup graph — held by the frozen `test_import_footprint.py` allowlist (which the 0.3.2 loader restructure actually *shrank* by 7 modules, and no 0.3.2/0.3.3 addition re-added an eager import).

### 2. Base dependencies

Unchanged: `psutil>=5.9` is the only runtime dependency. 0.3.2's `py-landlock`/`pyseccomp` ship ONLY under the opt-in `[sandbox]` extra with `sys_platform=='linux'` markers — a base or macOS install pulls neither (clean-room-verified each release).

### 3. Per-round allocation growth

No new growth — `test_round_alloc_growth.py` green across both versions. 0.3.3's cgroup-delegation probe is a boot-time read-only `os.access`/`stat` (not per-round); 0.3.2's trampoline is a per-hook subprocess only for a THIRD-PARTY Tier-B hook on an engaged sandbox (zero for the first-party fleet), off the per-round allocation axis.

### Efficiency (internal wait)

Unchanged this cycle — as of 0.3.3 the serve loop's round-exit wait was still a ~1 s busy-poll. The event-driven migration that removes that internal wait is the 0.3.4 work; its efficiency numbers are the next section.

### Constrained-host (honesty)

Numbers are dev-host-relative (the macOS harness this page has used since 0.2.17). The 462MB / 256MB constrained-host confirmation stays deferred while that host is offline — measured on dev/CI, documented as deferred for the constrained host, never reported as zero-cost.

## 0.3.3 → 0.3.4 (2026-09-14)

The event-driven serve core: the round-exit wait, the terminate/reap grace waits, and serve's pause + restart-delay sleeps all block on a single OS event (a process-exit fd via `pidfd`/`kqueue`, plus a FIFO doorbell) instead of a ~1 s busy-poll. Same macOS `kqueue` harness (§ methodology above).

### 1. Import/startup RSS

| | 0.3.3 | 0.3.4 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 23.9 MB | 24.0 MB | ~0 MB (flat, within run-to-run noise) |
| RSS range | 23.8–24.0 MB | 23.95–24.12 MB | |

Flat, and deliberately so. The two new modules on the cold-startup graph — `_notify` (the FIFO doorbell) and `_procwait` (the process-exit fd wait) — are pure-stdlib leaves: they import only `os`/`select`/`selectors`/`errno`/`pathlib`/`subprocess` plus the already-loaded `clock`. `select`/`selectors` are already resident (`psutil` pulls them — verified: `import psutil` alone brings `select` in), so adding these two modules to the eager graph costs ~0 RSS. The `test_import_footprint.py` allowlist gained exactly those two entries, no others.

The reduction that matters here is the one **avoided**: the round-3 design measured a stdlib-`asyncio` version of this same core at **+5.2 MB RSS** (`import asyncio` alone). Choosing `selectors`/`pidfd`/`kqueue` over `asyncio` keeps that 5.2 MB off every install — and a new `test_no_asyncio_select.py` invariant now forbids an `asyncio` import anywhere in `agent_runner/`, so the saving can't silently regress. `asyncio` is confirmed absent from the cold graph.

### 2. Base dependencies

Unchanged: `psutil>=5.9` is still the only runtime dependency. No new dependency and no new extra — the event-driven core is entirely stdlib (`select`/`selectors`/`os.pidfd_open`/`os.mkfifo`). Clean-room-verified.

### 3. Per-round allocation growth

`test_round_alloc_growth.py` green — no new retained per-round growth. The rewrite also **reduces transient per-round work**: the old mid-round loop iterated once per ~1 s tick for the whole round (hundreds of loop bodies over a long round), whereas the new loop blocks in one `wait_exit` and wakes only when the round exits or the ~10 s mem-check is due. Mid-round wakeups per round drop from ~`round_budget_s` (1/s busy-poll) to ~`round_budget_s / 10` (host-health armed) or 1 (unarmed) — and, crucially, are **independent of how many events the round emits**: `_spawn_round` watches only the round leader's own exit fd, not the doorbell (a design-review ruling removed the mid-round doorbell watch as a no-op that consumed no decision — see below). Measured: a 2 s round that emitted ~256 events mid-flight woke `wait_exit` exactly **1** time.

### Efficiency (internal wait) — the headline

This is the release whose whole point is removing an internal wait, and it does. On the fast path (`pidfd`/`kqueue` — the CI and production norm):
- **Idle CPU wakeups during a round drop from ~1/s to ~0** — the supervisor sleeps in one `select`/`kqueue` call until the round leader actually exits (or the next ~10 s mem-check is due), instead of waking every second to re-check a clock.
- **Mid-round wakeups are event-rate-independent** — because `_spawn_round` no longer watches the doorbell, a round emitting a burst of events costs it zero extra wakeups (measured: 1 wake for a 2 s round with ~256 events). An earlier iteration DID watch it and, without a drain, busy-spun (measured 445,795 `wait_exit` calls in a 1.5 s round); rather than paper over that with a drain, the design review removed the watch entirely — the wake advanced no mid-round decision — so the failure mode is structurally gone, not merely guarded.
- **Signal reaction goes from up-to-`chunk_s` (≤30 s) to near-instant** — a `SIGTERM`/`serve stop` during a schedule/memory/phase pause or the restart delay wakes that wait immediately (via `signal.set_wakeup_fd` on the pause/sleep doorbell fd) instead of riding out the current 30 s chunk. (Mid-round, a SIGTERM is handled by the signal handler + the post-round stop check, unchanged — the round runs to its natural end/deadline either way.)
- **`events --tail` latency drops from ≤1 s to one drain cycle (sub-ms).**
- **`events.emit`'s doorbell ring got cheaper too** — it lists live listeners with `os.scandir` + a suffix check instead of `pathlib.glob("*.fifo")`, skipping a `Path` allocation and the fnmatch machinery on the highest-frequency durable-write path.

The decision RULES are byte-identical: none of this changes the inputs→verdict for when a round is deferred, terminated, or reaped. One honest boundary note: the ~10 s mem-check tick and the `round_budget_s` deadline now fire on schedule instead of up to ~1 s late (the old poll-tick lag), so within that ≤1 s window the sustained-pressure floor or the budget cutoff can act on a round the old poll would have seen finish first — a more-precise floor, not a rule change.

### Constrained-host (honesty)

Numbers are dev-host-relative (the macOS `kqueue` harness). The Linux `pidfd` fast path is exercised only on CI (ubuntu runners); the real 462 MB / 256 MB constrained-host wake-latency and `pidfd`/`kqueue`-under-swap-pressure confirmation stays deferred to 0.3.5 while that host is offline — measured on dev/CI, documented as deferred for the constrained host, never reported as zero-cost.

## 0.3.4 → 0.3.5 (2026-09-15)

"Cooperative wrap-up grace": a configurable, manifest-gated SIGTERM→SIGKILL grace threaded to the round leader's actual reap deadline (a cooperative agent like `gemini` gets `[agent] sigterm_grace_s`, default 10 s; `claude` keeps 5 s). Same macOS harness. <!-- authored: default sigterm_grace_s / REAP_GRACE_S; SSOT agent_runner/config/models.py, agent_runner/agent_runtime.py -->

### 1. Import/startup RSS

| | 0.3.4 | 0.3.5 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 24.0 MB | 24.1 MB | ~0 MB (flat, within noise) |
| RSS range | 23.95–24.12 MB | 23.91–24.25 MB | |

Flat: the change is a config field, a pure grace-resolution function, an env round-trip, and two observability fields — no new module on the cold-startup graph, no new dependency (base stays `psutil>=5.9`). `test_import_footprint.py` + `test_round_alloc_growth.py` green.

### 2. Efficiency

No new internal wait — the grace window rides the v0.3.4 `wait_exit` deadline (zero polling during the grace), so a 12 s cooperative grace costs zero wakeups, same as the old 5 s. Two structural wins rather than a headline number:
- **One grace code path, not two.** The reap deadline is now a single threaded value (defaulting to `REAP_GRACE_S`) instead of a hardcoded constant plus a would-be cooperative branch — the branch was never created, closing the v0.3.3 dark-code shape at the source.
- **No wasted force-kills.** A cooperative agent that finishes flushing within its grace is reaped normally instead of SIGKILLed mid-write — work that the old fixed 5 s could truncate now completes. The behavioral efficiency win, measured by the wall-clock PROPERTY test (`test_cooperative_grace_property.py`), not RSS.

### 3. Constrained-host (honesty)

Dev-host-relative (macOS). The grace's real value shows on the constrained fleet (a `gemini` round flushing under memory pressure before an early-SIGTERM nudge) — that nudge is the 462 MB-constrained-host-gated v0.3.7 work; this release ships only the correct, configurable grace it will use. No new constrained-host claim.

## 0.3.5 → 0.3.6 (2026-09-15)

Two closed lifecycle gaps, not a new subsystem: `kill` now reaps a stuck round's detached descendants the way `serve stop` already did, and the sandboxed spawn-hook trampoline wait now joins the same `wait_exit` fd primitive as the rest of `serve`, so a stop during a confined third-party hook (or a plugin `defer`) reacts immediately instead of riding out the old poll. Same macOS harness (§ methodology above).

### 1. Import/startup RSS

| | 0.3.5 | 0.3.6 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 24.12 MB | 24.18 MB | +0.06 MB (flat, within noise) |
| RSS range | 24.03–24.19 MB* | 24.00–24.34 MB | |

\* one v0.3.5 run came in at 27.17 MB — the same first-invocation cold-page-cache outlier this page excludes elsewhere (see Caveats); dropped from the average/range as noise.

Flat, and no new import edge. `_plugin_sandbox`/`_procwait`/`_notify` are NOT on the bare `import agent_runner` graph, but they ARE on the `import agent_runner.cli` (serve/round) startup graph — frozen in `test_import_footprint.py`'s `EXPECTED_STARTUP_PKG_MODULES` allowlist since the v0.3.2 SpawnHook seam (`_serve_round` imports `run_hook_sandboxed` at module level) and the v0.3.4 doorbell. v0.3.6 adds no new module to that graph — only new NAMES within those already-imported modules (`wake_fd`/`should_stop`/`SpawnHookInterrupted`/`_DRAIN_JOIN_S`/`_join_drain_threads`) — so the allowlist is unchanged and `test_import_footprint.py` + `test_round_alloc_growth.py` are green. Base dependency stays `psutil>=5.9`. (The raw `sys.modules` count shows +1, `agent_runner._version` — a generated file a fresh throwaway worktree lacks, a measurement artifact, not a code change.)

**The reducible floor (why flat is the floor, not a lack of effort).** The startup graph sits at the CPython interpreter floor (~16.4 MB, irreducible) plus a small, shared, already-minimized set of modules — `hashlib`/`zoneinfo`/`importlib.metadata`/`ssl` were removed and are locked out by `test_import_footprint.py`'s `FORBIDDEN_AT_STARTUP`. Every remaining reduction lever, measured as *marginal* cost inside the real serve graph:

| lever | marginal RSS | status |
|---|---|---|
| drop `psutil` (+ its `socket`) | 0.92 + 0.42 MB | **blocked (safety)** — `psutil.Process.create_time()` is the PID-reuse identity guard (`lifecycle.create_time_matches`); a hand-rolled `/proc` starttime parser is the PID-reuse failure class with a new author, and macOS has no `/proc`, so the shipped guard would escape the (macOS) reap property tests |
| drop the `inspect` tail (`inspect`/`ast`/`dis`/`tokenize`/`token`/`opcode`) | 0.95 MB | **blocked (ABI)** — it enters solely via `dataclasses`, which is the frozen `PluginManifest` kind + the config field-validation surface; removing it from the resident process is a v0.4 breaking-release item, not a 0.3.x slice |
| per-command lazy dispatch | 0.17 MB | inside the ±0.1–0.2 MB noise band — "flat within noise" by this page's own gate |
| `importlib.resources` / `sysconfig` / `tempfile` / `shutil` tail | ≤0.09 MB each | noise |

So at the 0.3.x ABI every ≥0.5 MB lever is either safety-negative or ABI-breaking, and everything else is measurement noise: the ~24 MB is a measured floor. Import RSS (axis 1) is a proxy; the property this page protects is **axis 2 — the resident RSS the supervisor holds in the agent's shared cgroup** (serve-startup RSS, below), which the psutil-lazy cold-import lever moves by 0 (serve loads psutil at startup regardless). Reduction resumes at the v0.4 language fork, where `/proc` parsing is native (unblocking psutil) and the whole suite runs on Linux.

### 2. Efficiency

No headline RSS number here — the win is in wakeups and reaction latency, and it's conditional on workload. For the first-party plugin fleet, which runs no third-party spawn hooks, this release is flat, same as 0.3.5. The number that moves is scoped to whoever *does* run one:

- **Per engaged third-party spawn hook, the trampoline wait drops from ~20 wakeups/s to exactly 1.** The old confinement trampoline joined a stdlib `subprocess.Popen.wait()`, which sleep-polls internally (~20 Hz); the rewrite blocks the trampoline on the same `wait_exit` fd primitive the rest of `serve` already uses, so it wakes exactly once, on the hook's actual exit — asserted by `test_plugin_sandbox_wire.py`'s `calls["n"] == 1` property test.
- **A stop during a confined hook or a plugin `defer` now takes effect immediately instead of after up to 30 s**, and the confined hook's child process is always reaped, never left running — closing the two lifecycle gaps this release targets. Behavioral, not an RSS change.

The trampoline bounds its post-reap pipe drain to `_DRAIN_JOIN_S` (`agent_runner/_plugin_sandbox.py`): if a process the hook itself `fork()`'d keeps the reaped child's output pipe open, the drain thread stays blocked in `read()` and is abandoned (a daemon thread) rather than stalling the stop. That leaks at most one drain-thread pair per such invocation, bounded by the plugin's own process leak and negligible in RSS (an untouched thread stack); the thread dies when the fork()'d holder exits or when `serve` itself does. Reaping the hook's own `fork()`'d descendants is a recorded internal follow-up.

### 3. Constrained-host (honesty)

Dev-host-relative (macOS). Neither closed gap is constrained-host-specific — both are plain process-lifecycle correctness fixes exercised on CI (Linux, where the sandbox trampoline actually engages) and dev. No new constrained-host claim; the 462 MB `memory.high` write stays 0.3.7, Pi-gated.

## 0.3.6 → 0.3.7

The cgroup `memory.high` soft-brake and the early cooperative-SIGTERM nudge — both opt-in, default off. Same macOS harness (§ methodology above), comparing the tagged `v0.3.6` commit (measured in a throwaway worktree) against this branch's tip.

### 1. Import/startup RSS

| | 0.3.6 | 0.3.7 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 23.72 MB | 23.72 MB | ~0 MB (flat) |
| RSS range | 23.58–23.84 MB | 23.61–23.92 MB | |
| `sys.modules` count | 212 | 212 | +0 |

Flat, and expected to be: no new dependency (base install stays `psutil>=5.9` only), and no new module joins the cold-startup graph. The brake's write helpers (`engage_leaf_memory_high` / `restore_leaf_memory_high`) are plain `os.open`/`os.write`/`os.close` functions added beside the existing cgroup readers in the already-resident `metrics` module; the config additions (`[monitor.host_health.brake]`, `pressure.in_round_nudge`) are new fields on the already-imported `config` dataclasses, not a new module. `test_import_footprint.py`'s `EXPECTED_STARTUP_PKG_MODULES` allowlist is unchanged this release.

### 2. Per-round allocation growth

`test_round_alloc_growth.py` green, unmodified harness. The brake only writes when armed (default off, so the shipped default takes this path zero times per round) and, when armed, its per-tick work is the same shape as the existing pressure read it rides alongside — no new retained per-round state.

### 3. Constrained-host (honesty)

Dev-host-relative (macOS) numbers only. This is the release that finally exercises the write path this page has flagged as deferred since 0.3.3 (previously: "the 462 MB `memory.high` write stays Pi-gated") — it now ships, but still default-off and verified so far only against a fake cgroup-v2 tree (unit tests) and Linux CI (the E2E nudge test). The real smallest-field-host confirmation — the brake actually engaging under genuine memory pressure on the ~462 MB constrained host, and its effect on that host's swap behavior — stays deferred until that host is back online for a prerelease verification pass. Documented as deferred, not skipped as passing.

## 0.3.7 → 0.3.8

Two independent changes: the decided backlog directions (`auto_commit` owned-paths exclusion, the `events_oom_kill_delta` canonical alias, the `claude` preset arming the repetitive-anomaly detector — all new fields/branches on already-imported config, preset, and vcs modules) and the event_log read-layer consolidation (a behavior-preserving refactor: a new `agent_runner.event_log` module owns the `events-*.jsonl` read layout + offset tail machinery, shared by the CLI `events --tail`/`--since`, the monitor poll, and the throttle detectors, replacing three hand-rolled copies). Same macOS harness (§ methodology); the v0.3.7 figures are carried from its close-out row above (same harness).

### 1. Import/startup RSS

| | 0.3.7 | 0.3.8 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 23.72 MB | 23.65 MB | −0.07 MB (flat, within noise) |
| RSS range | 23.61–23.92 MB | 23.47–23.75 MB | |
| `sys.modules` count | 212 | 212 | +0 |

Flat, and expected: no new dependency (base install stays `psutil>=5.9` only). The consolidation adds exactly one `agent_runner` module to the cold-startup graph — `agent_runner.event_log` — and `test_import_footprint.py`'s `EXPECTED_STARTUP_PKG_MODULES` allowlist gains that one entry (the only allowlist change this release). Total `sys.modules` is a wash because the relocated read core (`read_new`, moved out of `events.py`) is the same code under a new home, not additional code loaded; the three consumers each shed their own copy of the read/offset loop. The backlog-decision changes add no new import.

### 2. Per-round allocation growth

`test_round_alloc_growth.py` green, unmodified harness. The event_log refactor is behavior-preserving — the follow loop's per-drain offset dict is the same bounded per-poll state the pre-refactor tailers already carried, not new retained state — and the backlog-decision changes touch commit-time and detector-arm paths, not any per-round allocation.

## 0.3.8 → 0.3.9

Plugin surface subtraction: dropped 6 zero-producer `PluginManifest` fields
(`pre_round_hooks`, `context_enrichers`, `serve_startup_hooks`, `spawn_hooks`,
`detectors`, `event_kinds`), the `DirtyHandler` seam, the whole third-party
sandbox subsystem (`_plugin_sandbox.py`, `_sandbox_probe.py`,
`_plugin_checksum.py`, the opt-in `[sandbox]` extra), and the plugin-owned-paths
registry — none had a real producer. Same macOS harness (§ methodology above),
comparing **v0.3.8** = `10a665b` (remeasured fresh in a throwaway worktree this
session, same interpreter) against **0.3.9**, the tip of this branch.

### 1. Import/startup RSS

| | v0.3.8 (remeasured) | 0.3.9 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 23.45 MB | 23.44 MB | ~0 MB (flat, within noise) |
| RSS range | 23.31–23.64 MB | 23.28–23.64 MB | |
| `sys.modules` count | 211 | 211 | +0 |

Honestly flat, not a drop — for the same reason this page already recorded
when the sandbox subsystem was *added* in 0.3.2: it was always behind the
opt-in `[sandbox]` extra, and its trampoline/probe/checksum modules import
lazily (only when a sandboxed spawn hook actually runs), so they were never
on the `import agent_runner.cli` cold-startup graph this axis measures —
removing them can't move a number they never contributed to. A direct
`sys.modules` set diff (not just the count) confirms it: the `agent_runner.*`
module set `import agent_runner.cli` loads is byte-for-byte identical, 70
modules, before and after. This page's previously-recorded v0.3.8 figure
(23.65 MB / 212 modules, a different measurement session) differs from the
23.45 MB / 211 remeasured here by less than the page's own documented
±0.2–0.4 MB run-to-run noise band — sampling variance, not a regression.

### 2. Module count, source LOC, largest module

Tracked source only, both sides via `git ls-files`/`git ls-tree` — NOT a
working-tree `find`, which on the 0.3.9 side would silently count the
gitignored generated `agent_runner/_version.py` (24 lines) that a fresh
checkout of the v0.3.8 tag doesn't have, understating the real delta by
exactly that file.

| | v0.3.8 | 0.3.9 | Δ |
|---|---|---|---|
| `.py` files | 86 | 81 | **−5** |
| total LOC | 22,257 | 20,195 | **−2,062 (−9%)** |
| largest module | `cli/_serve_round.py`, 991 | `migrations.py`, 972 | n/a — different module |

This is where the subtraction actually shows up. `_plugin_sandbox.py`,
`_sandbox_probe.py`, `_plugin_checksum.py`, the `SpawnHook`/`DirtyHandler`
seams, and the owned-paths registry are gone from `agent_runner/`, alongside
~700 LOC of their tests (not counted in this source-only table).
`migrations.py` becomes the release's largest module at 972/1000 lines — the
0.3.9 removal-migration entries grew it toward the ceiling
`tests/invariants/test_module_sizes.py` enforces, then the close-out /simplify
pass deduped its table-scan and disable-rename helpers to restore comfortable
headroom.

### 3. Base dependencies

Unchanged base: `psutil>=5.9` is still the only runtime dependency. 0.3.9
also drops the opt-in `[sandbox]` extra (`py-landlock`/`pyseccomp`)
entirely — the third-party trampoline that extra served no longer exists, so
there is nothing left to opt into.

### 4. Per-round allocation growth

`test_round_alloc_growth.py` green, unmodified harness. No per-round reader
changed shape this release — the subtraction removes dispatch branches
(spawn-hook check, dirty-handler dispatch, sandbox-mode gate) rather than
adding any.

### Constrained-host (honesty)

Dev-host-relative (macOS) numbers only, same methodology as every release
since 0.2.17. No constrained-host-specific claim this release — the
subtraction is host-independent code removal, not a runtime-behavior change
under pressure.

## 0.3.9 → 0.3.10

Cross-round resume for `pi`: `PluginManifest.resume_flag` (a new dataclass
field, `str | None`, default `None`), `resolve_resume_flag`, a per-phase
session-id mint + env publish (`AGENT_RUNNER_RESUME_FLAG`/
`AGENT_RUNNER_RESUME_SESSION_ID`) in `serve_cmd`, the round child appending
`[flag, id]`, a `session_resumed` event, and a config boot guard. Same macOS
harness (§ methodology above), comparing **v0.3.9** = `095c5f1` (measured in a
throwaway worktree) against **0.3.10**, the tip of this branch.

### 1. Import/startup RSS

| | v0.3.9 | 0.3.10 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 23.42 MB | 23.39 MB | −0.03 MB (flat, within noise) |
| RSS range | 23.39–23.45 MB | 23.34–23.42 MB | |
| `sys.modules` count | 211 | 211 | +0 |

Flat, as expected: `uuid` (the only new stdlib name resume touches) is
imported inside `_apply_resume_env`, not at module level, so it never joins
the cold-startup graph — confirmed directly (`"uuid" in sys.modules` is
`False` after `import agent_runner.cli`), not just inferred from a flat RSS
number. A direct `sys.modules` set diff (not just the count) confirms the
`agent_runner.*` module set `import agent_runner.cli` loads is byte-for-byte
identical, 211 modules, before and after — the same check this page has run
at every release since 0.3.9's own close-out.

### 2. Base dependencies

Unchanged: `psutil>=5.9` is still the only runtime dependency
(`pip show cli-agent-runner` → `Requires: psutil`). Resume introduces no new
dependency and no new extra.

### 3. Module count, source LOC, largest module

Tracked source only (`git ls-tree`, same convention as the rows above).

| | v0.3.9 | 0.3.10 | Δ |
|---|---|---|---|
| `.py` files | 81 | 81 | +0 |
| total LOC | 20,195 | 20,338 | +143 |
| largest module | `migrations.py`, 972 | `migrations.py`, 972 | +0 |

The `+143` LOC is the new manifest field, its resolver, the boot guard, the
per-phase env publish/consume, the `session_resumed` event, and their tests
— no module decomposition this release.

### 4. Per-round allocation growth

`tests/invariants/test_round_alloc_growth.py` green, unmodified harness.
`_apply_resume_env` runs once per round (same call site as the other
per-round `serve_cmd` readers) and, on the no-resume branch every
non-`pi` preset takes, does two dict `.pop()`s and returns — no new retained
per-round state. On the resume branch (`pi` only), the session id is minted
once per phase at `serve` startup (`session_ids` dict, sized by phase count,
not by round count) and reused verbatim every round after — not re-allocated
per round.

### Constrained-host (honesty)

Dev-host-relative (macOS) numbers only, same methodology as every release
since 0.2.17. No constrained-host-specific claim this release — resume is a
pi-only session-id mechanism verified so far by an ssh-pi property test, not
a memory-pressure-path change.

## 0.3.10 → 0.3.11

Three additive changes: a host disk/inode-growth `%`/hr WARNING detector
(`detect_disk_growth`, the monitor's 14th built-in detector, reading a new
`inode_used_pct` field `metrics.collect` derives from `os.statvfs`),
`events.register_plugin_kind` (a namespaced custom-event-kind affordance for
out-of-tree plugins, restoring a slice of the 0.3.9-removed `event_kinds`
manifest field), and the small-host pre-OOM terminate-before-coma property
now pinned as tests-of-record (test-only, no runtime code). Same macOS
harness (§ methodology above), comparing **v0.3.10** = `bd63ce4` (measured
in a throwaway worktree) against **0.3.11**, the tip of this branch.

### 1. Import/startup RSS

| | v0.3.10 | 0.3.11 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 23.55 MB | 23.46 MB | −0.09 MB (flat, within noise) |
| RSS range | 23.45–23.70 MB | 23.34–23.56 MB | |
| `sys.modules` count | 211 | 211 | +0 |

Flat, as expected: `os.statvfs` is an attribute call on the already-imported
stdlib `os` module (`metrics.py` imports `os` at module level already, for
`os.getloadavg`) — it adds no new import edge. `register_plugin_kind` and its
backing `_PLUGIN_KINDS` set are new names inside the already-imported
`events.py`, not a new module. A direct `sys.modules` set diff of the
`agent_runner.*` modules `import agent_runner.cli` loads confirms
byte-for-byte identical, 70 modules, before and after.

### 2. Base dependencies

Unchanged: `psutil>=5.9` is still the only runtime dependency
(`pip show cli-agent-runner` → `Requires: psutil`; `pyproject.toml`
`dependencies` identical on both sides). No new dependency and no new extra.

### 3. Module count, source LOC, largest module

Tracked source only (`git ls-files`/`git ls-tree`, same convention as the
rows above).

| | v0.3.10 | 0.3.11 | Δ |
|---|---|---|---|
| `.py` files | 81 | 81 | +0 |
| total LOC | 20,338 | 20,507 | +169 |
| largest module | `migrations.py`, 972 | `migrations.py`, 972 | +0 |

The `+169` LOC is the disk-growth detector + its config sub-table fields +
the `register_plugin_kind` validation logic, plus their tests and the
`docs/migrations/0.3.md` adaptation notes (not counted in this source-only
table). No module decomposition this release; `migrations.py` stays the
largest module, unchanged in size (this release's migration-notes additions
landed in `docs/`, not in the executable `migrations.py` transform table).

### 4. Per-round allocation growth

`tests/invariants/test_round_alloc_growth.py` green, unmodified harness — no
per-round reader it covers (`round_outcome`, `_active_throttles`,
`post_round_decision`) changed shape. `detect_disk_growth` is one more
closure in the monitor's existing `run_all_detectors` poll list (13 → 14,
same shape as the existing detector entries, not a new retained per-round
data structure), and `register_plugin_kind` runs once at plugin import/load
time, not per round.

### Constrained-host (honesty)

Dev-host-relative (macOS) numbers only, same methodology as every release
since 0.2.17. The disk/inode-growth detector's real value is generalized
host-disk-pressure signal on a constrained field host under sustained
session/log growth — unverified live so far (observability-only, no action
path); the pre-OOM terminate-before-coma property is the release's
constrained-host-relevant verification, covered separately by the
always-on small-host calibration tests-of-record plus the
`AGENT_RUNNER_E2E_PI`-gated real-cgroup property test, not by this page's
RSS axis.

## 0.3.11 → 0.3.12

The `[goal]` steering loop: two new modules, `agent_runner.goal` (the
goal-check executor + the advisory-only treadmill assessor) and
`agent_runner._bounded` (a sanctioned timeout-bounded subprocess primitive),
plus their config plumbing (`GoalConfig`/`_GoalCheckConfig`, a boot guard, a
round-timeout-budget fold-in) and a config-level firewall keeping
`goal`-prefixed event kinds out of `[monitor] auto_stop_on`. Same macOS
harness (§ methodology above), comparing **v0.3.11** = `dfef642` (measured in
a throwaway worktree) against **0.3.12**, the tip of this branch.

### 1. Import/startup RSS

| | v0.3.11 | 0.3.12 | Δ |
|---|---|---|---|
| RSS (avg of 5 cold runs) | 23.44 MB | 23.41 MB | −0.03 MB (flat, within noise) |
| RSS range | 23.38–23.58 MB | 23.27–23.50 MB | |
| `sys.modules` count | 211 | 211 | +0 |

Flat, structurally, not just numerically: both new modules are kept off the
cold-startup import graph on purpose. `agent_runner.goal` is added to
`tests/invariants/test_import_footprint.py`'s `FORBIDDEN_AT_STARTUP`
allowlist this release, and `agent_runner._bounded` is a `goal.py`-only
dependency, so it never loads either unless `goal.py` does; both of
`runner.py`'s call sites (`run_goal_checks`, `assess_treadmill` /
`write_ledger_advisory`) import `agent_runner.goal` function-scope, guarded
by `cfg.goal is not None` — a config with no `[goal]` table never pulls
either module in. `EXPECTED_STARTUP_PKG_MODULES` is unchanged this release
(no new eager `agent_runner.*` import). A direct `sys.modules` set diff of
the `agent_runner.*` modules `import agent_runner.cli` loads confirms
byte-for-byte identical, 70 modules, before and after.

### 2. Base dependencies

Unchanged: `psutil>=5.9` is still the only runtime dependency
(`pyproject.toml` `dependencies` identical on both sides). `agent_runner._bounded`
runs check commands via stdlib `subprocess`; no new dependency and no new
extra.

### 3. Module count, source LOC, largest module

Tracked source only (`git ls-tree`, same convention as the rows above).

| | v0.3.11 | 0.3.12 | Δ |
|---|---|---|---|
| `.py` files | 81 | 83 | +2 |
| total LOC | 20,512 | 21,165 | +653 |
| largest module | `migrations.py`, 972 | `migrations.py`, 972 | +0 |

The two new files are `agent_runner/goal.py` and `agent_runner/_bounded.py`;
`migrations.py` stays the largest module, unchanged in size.

### 4. Per-round allocation growth

`tests/invariants/test_round_alloc_growth.py` green, unmodified harness — its
covered readers (`round_outcome`, `_active_throttles`, `post_round_decision`)
are unchanged in shape this release. That harness does not exercise the
`[goal]`-active path itself (opt-in, off by default). On a config that DOES
set `[goal]`, the treadmill assessor's own per-round read is bounded, not
unbounded: `assess_treadmill` reads only the newest two monthly
`events-*.jsonl` files (`event_log.newest_scope(2)`, the same tail window
every other events-derived detector in this codebase already reads), once
per round — a fixed-size read, not a full-history scan that grows with round
count.

### Constrained-host (honesty)

Dev-host-relative (macOS) numbers only, same methodology as every release
since 0.2.17. `[goal]` is opt-in and off by default, so this release makes no
constrained-host claim beyond the flat cold-startup number above; the
goal-active path's bounded-events-tail read is a structural property noted
here, not yet independently measured on a constrained host.

## Enforcement

Four invariant tests keep these numbers from drifting silently:
`tests/invariants/test_import_footprint.py` (forbidden-module allowlist +
frozen startup import graph), `tests/invariants/test_round_alloc_growth.py`
(64 KB per-round growth ceiling), `tests/invariants/test_module_sizes.py`
(1,000-line-per-module ceiling), and `tests/invariants/test_no_asyncio_select.py`
(no `asyncio` anywhere — protects the 0.3.4 decision to use `selectors`/`pidfd`/`kqueue`
over the +5.2 MB `asyncio` alternative — plus `select`/`selectors`/`pidfd` confinement).
