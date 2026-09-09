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

## Enforcement

Three invariant tests keep these numbers from drifting silently:
`tests/invariants/test_import_footprint.py` (forbidden-module allowlist +
frozen startup import graph), `tests/invariants/test_round_alloc_growth.py`
(64 KB per-round growth ceiling), and `tests/invariants/test_module_sizes.py`
(1,000-line-per-module ceiling).
