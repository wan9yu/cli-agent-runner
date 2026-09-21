"""End-to-end: the ``[goal]`` treadmill assessor's advisory-only fold, under
a REAL ``serve`` loop with a synthetic (non-LLM) agent.

The assessor reads the events tail from inside the round subprocess ``serve``
spawns each round -- a shape a mock or a unit test around ``assess_treadmill``
in isolation cannot exercise, because the in-progress round's own pre-spawn
``round_substrate_before`` event is already in that tail by the time the
assessor runs. Only a real ``serve`` loop, with real round subprocesses,
proves the advisory actually reaches the FRESH round's own prompt (not a
stale unit-test event list).

The synthetic agent (a tiny ``sh -c`` script) exits 0, touches a per-round
marker file (the CLI-agnostic "activity" signal via git dirty-tree
detection), and echoes its own full prompt argument to stdout -- which
``serve`` captures verbatim into that round's ``logs/rounds/R{n}-*.log``.
Echoing the prompt is how this test proves the ledger's advisory bytes
actually reached the fresh child's argv, not just that the ledger file was
written.

No ssh, no LLM -- plain CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_runner._serve_policy import CRASH_LOOP_EXIT, CRASH_LOOP_THRESHOLD
from agent_runner.api import assemble_prompt
from agent_runner.config import load_config
from agent_runner.events import GOAL_ASSESSMENT, GOAL_CHECK, ROUND_SUPERVISOR_WEDGED
from tests._test_helpers import read_events_for_current_month

# Same size floor test_bounded_run.py's fixtures rely on: the startup battery's
# prompt_smoke_passes check requires >= 500 assembled bytes.
_VALID_PROMPT = "placeholder agent task prompt line. " * 20

# Exits 0, touches a per-round marker (the round's own git-dirty activity
# signal), and echoes the full prompt it was given -- serve captures that
# echo verbatim into the round's own agent log.
_TREADMILL_SCRIPT = "printf '%s' \"$1\"; touch tick-$AGENT_RUNNER_ROUND_NUM; exit 0"

# Same shape (including the same per-round marker, so gate 1's activity
# signal is agent-driven in BOTH variants), but also touches `done` once the
# round number reaches 3 -- the goal-check `test -f done` starts passing from
# round 3 onward and then stays satisfied, so this differs from
# _TREADMILL_SCRIPT in exactly one variable: whether/when `done` gets touched.
_CONVERGING_SCRIPT = (
    "printf '%s' \"$1\"; touch tick-$AGENT_RUNNER_ROUND_NUM; "
    '[ "$AGENT_RUNNER_ROUND_NUM" -ge 3 ] && touch done; exit 0'
)

# The armed-breaker differential's agent: exits 1 immediately, well under
# _serve_policy.CRASH_LOOP_SHORT_EXIT_S (60s) -- an "unknown short crash" on
# every round, so post_round_decision's crash-loop breaker arms and trips at
# exactly CRASH_LOOP_THRESHOLD consecutive rounds.
_CRASH_SCRIPT = "exit 1"


def _init_git(work_dir: Path, *, gitignore_toml: bool = False) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=work_dir, check=True)
    # logs/ gitignored so event-log churn (and the ledger, which now lives under
    # log_dir) never contributes to the round-to-round "dirty" signal. The
    # agent's own per-round marker file (tick-N) provides genuine per-round
    # activity in every variant.
    #
    # gitignore_toml (the stash variant): under dirty_action="stash" the
    # untracked agent-runner.toml would itself be swept into the orphan stash
    # every round -- removing serve's own config from the tree -- so the stash
    # variant gitignores it, leaving tick-N as the sole per-round dirty path.
    ignore = "logs/\nagent-runner.toml\n" if gitignore_toml else "logs/\n"
    (work_dir / ".gitignore").write_text(ignore)
    (work_dir / "prompt.md").write_text(_VALID_PROMPT)
    subprocess.run(["git", "add", ".gitignore", "prompt.md"], cwd=work_dir, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=work_dir,
        check=True,
    )


def _write_config(
    tmp_path: Path, *, agent_script: str, with_goal: bool = True, dirty_action: str = "ignore"
) -> Path:
    """A real agent-runner.toml wiring [goal] + a synthetic sh agent.

    ``[prompt] files`` lists the lessons ledger at index 1 (the boot guard
    requires index >= 1 -- the ledger doesn't exist at cold start). The ledger
    lives UNDER ``log_dir`` (``logs/ledger.md``): the boot guard rejects a
    ledger inside work_dir but outside log_dir, and a ledger under log_dir is
    the one work-tree path the ``dirty_action="stash"`` pathspec excludes, so
    the steer survives a stash.

    ``dirty_action`` (default ``"ignore"``) selects the [vcs] mode. Under
    ``"ignore"`` the round's marker file is left in the tree, so a persistently
    dirty tree reads as activity every round (a STATE signal). Under ``"stash"``
    each round's marker is stashed away, so ``dirty_detected`` is a genuine
    per-round CHANGE signal.

    ``with_goal`` (default True): False omits the entire ``[goal]`` table AND
    drops the ledger from ``[prompt] files`` (leaving just ``["prompt.md"]``),
    producing a config that never mentions goal machinery at all -- the
    armed-breaker differential's "without" arm and the default-path arm.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    command = json.dumps(["sh", "-c", agent_script, "sh"])
    prompt_files = '["prompt.md", "logs/ledger.md"]' if with_goal else '["prompt.md"]'
    goal_block = (
        (
            "[goal]\n"
            'ledger = "logs/ledger.md"\n'
            "[[goal.checks]]\n"
            'name = "done_check"\n'
            'cmd = ["test", "-f", "done"]\n'
        )
        if with_goal
        else ""
    )
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
        "[agent]\n"
        f"command = {command}\n"
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        "restart_delay_s = 1\n"
        "[prompt]\n"
        f"files = {prompt_files}\n"
        "[vcs]\n"
        f'dirty_action = "{dirty_action}"\n'
        f"{goal_block}"
    )
    return toml


def _run_serve(cfg_path: Path, *, max_rounds: int, timeout_s: int) -> subprocess.CompletedProcess:
    # Real `serve`, run in the FOREGROUND (never backgrounded) with its own
    # bounded subprocess timeout -- the pattern tests/integration/
    # test_bounded_run.py uses for the same reason (widened for real python
    # interpreter startup x N rounds, not because anything is expected to hang).
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
            "--max-rounds",
            str(max_rounds),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


@pytest.mark.timeout(120)
def test_goal_steering_should_fold_one_advisory_into_the_fresh_round_when_treadmilling(
    tmp_path: Path,
) -> None:
    _init_git(tmp_path)
    cfg_path = _write_config(tmp_path, agent_script=_TREADMILL_SCRIPT)
    log_dir = tmp_path / "logs"

    # k (_TREADMILL_WINDOW_ROUNDS) = 3: the assessor needs 3 completed rounds
    # in its window before it can fire (round 4), plus one more round (5) to
    # prove the edge-trigger doesn't refire on a still-matching pattern.
    proc = _run_serve(cfg_path, max_rounds=5, timeout_s=100)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    events = read_events_for_current_month(log_dir)
    assessments = [e for e in events if e.get("event") == "goal_assessment"]
    assert len(assessments) == 1, f"expected exactly one goal_assessment event, got {assessments}"

    ledger_path = tmp_path / "logs" / "ledger.md"
    assert ledger_path.exists(), "the advisory fired but the ledger was never written"
    ledger_content = ledger_path.read_text(encoding="utf-8")
    assert ledger_content.count("### Goal assessment --") == 1, (
        f"expected exactly one advisory block in the ledger, got:\n{ledger_content!r}"
    )

    # The load-bearing assertion: round 4 (K+1) is the fresh child whose own
    # prompt must carry the just-written advisory -- proving the steer
    # reached the fresh subprocess, not merely that the ledger got written.
    r4_logs = sorted((log_dir / "rounds").glob("R4-*.log"))
    assert len(r4_logs) == 1, f"expected exactly one R4 round log, found {r4_logs}"
    r4_content = r4_logs[0].read_text(encoding="utf-8")
    assert ledger_content in r4_content, (
        "the ledger's advisory bytes did not reach round 4's fresh agent "
        f"subprocess prompt.\nledger:\n{ledger_content!r}\n\nR4 log:\n{r4_content!r}"
    )

    # Pin the firing to EXACTLY round 4, not "at or before" it: the ledger is
    # a [prompt] files entry, so once written it rides in EVERY subsequent
    # round's prompt too -- a window-size regression that fired as early as
    # round 3 would still put the marker in both R3 and R4 logs and still
    # satisfy the count==1 / substring checks above. goal_assessment carries
    # no round_num, so the round logs' own content is the only way to pin it.
    marker = "### Goal assessment --"
    for n in (1, 2, 3):
        early_logs = sorted((log_dir / "rounds").glob(f"R{n}-*.log"))
        assert len(early_logs) == 1, f"expected exactly one R{n} round log, found {early_logs}"
        early_content = early_logs[0].read_text(encoding="utf-8")
        assert marker not in early_content, (
            f"the advisory fired too early -- its marker already appears in "
            f"round {n}'s log:\n{early_content!r}"
        )


@pytest.mark.timeout(140)
def test_goal_steering_should_never_advise_when_the_goal_converges(tmp_path: Path) -> None:
    _init_git(tmp_path)
    cfg_path = _write_config(tmp_path, agent_script=_CONVERGING_SCRIPT)
    log_dir = tmp_path / "logs"

    # K+3 (not K+2): the goal is satisfied+stable from round 3 onward, so
    # rounds 3-5 alone would already be a constant-satisfied window at round
    # 6's assessment -- one round short of that (max_rounds=5) never actually
    # exercises the "every check satisfied and stable" gate this test exists
    # to pin; six rounds gives the assessor a full satisfied-and-stable
    # window (rounds 3,4,5) to (wrongly, pre-fix) call stuck at round 6.
    proc = _run_serve(cfg_path, max_rounds=6, timeout_s=120)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    events = read_events_for_current_month(log_dir)
    assessments = [e for e in events if e.get("event") == "goal_assessment"]
    assert assessments == [], f"a converging goal must never be advised, got {assessments}"

    ledger_path = tmp_path / "logs" / "ledger.md"
    assert not ledger_path.exists(), (
        f"no advisory should ever be written to the ledger, found: "
        f"{ledger_path.read_text(encoding='utf-8')!r}"
    )


@pytest.mark.timeout(160)
def test_goal_steering_ledger_should_survive_the_default_stash_and_persist_when_invoked(
    tmp_path: Path,
) -> None:
    """Ledger-survives-the-stash regression: under the DEFAULT [vcs]
    dirty_action="stash", the
    supervisor-owned ledger must PERSIST across rounds, not be swept into the
    round's orphan stash after the round it fired for.

    With the ledger under log_dir (excluded from the stash pathspec) the
    advisory written before round 4 must still ride in round 4's prompt AND in
    rounds 5 and 6 -- i.e. past K+1, all the way to K+2 and beyond -- and the
    ledger file must still exist on disk at the end. Mutation check: putting the
    ledger back at work_dir root (the pre-fix layout) lets `git stash push -u`
    sweep it after round 4, so rounds 5/6 carry no advisory and the ledger is
    absent at the end -- both assertions below then fail.

    The stash mode also makes each round's `dirty_detected` a genuine per-round
    CHANGE signal (the marker is stashed away every round), not the persistently
    dirty STATE signal the dirty_action="ignore" variant relies on.
    """
    _init_git(tmp_path, gitignore_toml=True)
    cfg_path = _write_config(tmp_path, agent_script=_TREADMILL_SCRIPT, dirty_action="stash")
    log_dir = tmp_path / "logs"

    proc = _run_serve(cfg_path, max_rounds=6, timeout_s=140)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    events = read_events_for_current_month(log_dir)

    # Non-vacuity: the stash mode was genuinely active (the round's dirty marker
    # was stashed as orphan work), not silently downgraded to ignore.
    orphan_stashed = [e for e in events if e.get("event") == "orphan_stashed"]
    assert orphan_stashed, "expected stash mode to stash the per-round marker as orphan work"

    assessments = [e for e in events if e.get("event") == "goal_assessment"]
    assert len(assessments) == 1, f"expected exactly one goal_assessment event, got {assessments}"

    ledger_path = tmp_path / "logs" / "ledger.md"
    assert ledger_path.exists(), (
        "the ledger was swept by the default stash -- it must survive under log_dir"
    )
    ledger_content = ledger_path.read_text(encoding="utf-8")
    assert ledger_content.count("### Goal assessment --") == 1, ledger_content

    # The load-bearing assertion: the advisory bytes reach not just round 4
    # (K+1) but rounds 5 and 6 -- proving the ledger was NOT stashed away after
    # the round it fired for.
    for n in (4, 5, 6):
        round_logs = sorted((log_dir / "rounds").glob(f"R{n}-*.log"))
        assert len(round_logs) == 1, f"expected exactly one R{n} round log, found {round_logs}"
        content = round_logs[0].read_text(encoding="utf-8")
        assert ledger_content in content, (
            f"round {n}'s prompt did not carry the persisted ledger advisory -- "
            f"the stash swept it.\nledger:\n{ledger_content!r}\n\nR{n} log:\n{content!r}"
        )


# --- P2 firewall differential + P5 default-path -----------------------


@pytest.mark.timeout(260)
def test_goal_steering_should_not_disarm_the_crash_loop_breaker_when_invoked(
    tmp_path: Path,
) -> None:
    """P2 armed-breaker differential -- the BEHAVIORAL complement to the AST
    firewall (no kill-path module even references a goal_* kind string) and
    tests/unit/test_goal_firewall_behavioral.py's events-derived P2 unit golden
    (round_outcome is blind to goal_* kinds). This proves the
    INTEGRATION: an agent that exits 1 FAST every round trips
    post_round_decision's crash-loop breaker at EXACTLY CRASH_LOOP_THRESHOLD
    consecutive rounds -- IDENTICALLY whether or not [goal] is configured. A
    real goal-check subprocess running after every crashed round must not
    inflate round_duration_s past CRASH_LOOP_SHORT_EXIT_S (which would
    silently DISARM the breaker). The round_supervisor_wedged assertion below
    is a cheap sanity check, not independent proof of "a check can't wedge
    the supervisor" -- that guarantee is BY CONSTRUCTION via
    timeout_budget's goal_checks_allowance_s fold into outer_ceiling_s (see
    _serve_policy.timeout_budget), not something this fast-crashing fixture
    can observe: a real wedge would surface as this test's own subprocess
    TimeoutExpired (the outer ceiling vastly exceeds the 90s _run_serve
    timeout here) before round_supervisor_wedged could ever be written.

    max_rounds == CRASH_LOOP_THRESHOLD bounds worst-case runtime regardless of
    pass/fail: if the breaker ever failed to fire, serve would cleanly hit
    max_rounds_reached (exit 0) instead of escalating restart delays without
    limit -- a fast, crisp failure instead of a timeout.
    """
    no_goal_dir = tmp_path / "no_goal"
    no_goal_dir.mkdir()
    _init_git(no_goal_dir)
    no_goal_cfg = _write_config(no_goal_dir, agent_script=_CRASH_SCRIPT, with_goal=False)
    no_goal_log_dir = no_goal_dir / "logs"
    proc_no_goal = _run_serve(no_goal_cfg, max_rounds=CRASH_LOOP_THRESHOLD, timeout_s=90)

    with_goal_dir = tmp_path / "with_goal"
    with_goal_dir.mkdir()
    _init_git(with_goal_dir)
    with_goal_cfg = _write_config(with_goal_dir, agent_script=_CRASH_SCRIPT, with_goal=True)
    with_goal_log_dir = with_goal_dir / "logs"
    proc_with_goal = _run_serve(with_goal_cfg, max_rounds=CRASH_LOOP_THRESHOLD, timeout_s=90)

    crash_loop_by_dir: dict[Path, dict] = {}
    for label, proc, log_dir, expect_goal_events in (
        ("without [goal]", proc_no_goal, no_goal_log_dir, False),
        ("with [goal]", proc_with_goal, with_goal_log_dir, True),
    ):
        assert proc.returncode == CRASH_LOOP_EXIT, (
            f"{label}: expected crash_loop exit {CRASH_LOOP_EXIT}, got "
            f"{proc.returncode}; stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        events = read_events_for_current_month(log_dir)
        crash_loop_events = [e for e in events if e.get("event") == "crash_loop"]
        assert len(crash_loop_events) == 1, f"{label}: {crash_loop_events}"
        assert crash_loop_events[0]["consecutive"] == CRASH_LOOP_THRESHOLD, (
            f"{label}: {crash_loop_events[0]}"
        )
        crash_loop_by_dir[log_dir] = crash_loop_events[0]
        round_logs = sorted((log_dir / "rounds").glob("R*-*.log"))
        assert len(round_logs) == CRASH_LOOP_THRESHOLD, (
            f"{label}: expected exactly {CRASH_LOOP_THRESHOLD} round logs (the breaker "
            f"must stop AT the threshold, not before or after), found {round_logs}"
        )
        # Sanity check only -- see docstring: the "a check can't wedge the
        # supervisor" guarantee is by construction (timeout_budget's
        # goal_checks_allowance_s fold), not proven by this observation.
        wedged = [e for e in events if e.get("event") == ROUND_SUPERVISOR_WEDGED]
        assert wedged == [], f"{label}: unexpected round_supervisor_wedged: {wedged}"
        goal_kinds = {e.get("event") for e in events} & {GOAL_CHECK, GOAL_ASSESSMENT}
        if expect_goal_events:
            # done_check never passes (the crash script never touches `done`) --
            # goal_check still fires every round even though it never
            # satisfies; proves goal-checking itself ran alongside the crashes.
            assert GOAL_CHECK in goal_kinds, f"{label}: expected goal_check events, got {events}"
        else:
            assert goal_kinds == set(), (
                f"{label}: goal_* events present with no [goal] configured: {goal_kinds}"
            )

    # The differential itself: identical stop round and identical verdict
    # whether or not [goal] is configured. Reuses the loop's already-read
    # crash_loop events above rather than re-reading each log_dir.
    assert proc_no_goal.returncode == proc_with_goal.returncode
    no_goal_crash = crash_loop_by_dir[no_goal_log_dir]
    with_goal_crash = crash_loop_by_dir[with_goal_log_dir]
    assert no_goal_crash["consecutive"] == with_goal_crash["consecutive"]
    assert no_goal_crash["exit_code"] == with_goal_crash["exit_code"]


def test_goal_steering_should_leave_prompt_assembly_byte_identical_when_goal_is_absent(
    tmp_path: Path,
) -> None:
    """P5 default-path zero-footprint property.

    Two DISTINCT checks, not one:

    1. An ABSOLUTE anchor: for this fixed, fully-deterministic ``ctx``, the
       naive (goal-naive) config's assembled prompt must be EXACTLY the
       round-context json header plus the raw ``prompt.md`` body, byte for
       byte -- nothing more, nothing less. This is the actual "goal path adds
       nothing when off" lock: a relative comparison against another arm
       cannot catch a leak that lands in BOTH arms (e.g. an unconditional
       addition to ``assemble_prompt``, or one keyed on ``cfg.goal is None``)
       -- an earlier version of this test did exactly that differential-only
       comparison and a mutation test (appending an unconditional literal to
       ``_round_support.assemble_prompt``) proved it STILL PASSED. The exact-
       bytes anchor below closes that gap: any extra byte anywhere fails it,
       regardless of which arm(s) it lands in.
    2. A differential: the with_goal=False `_write_config` fixture must match
       a SEPARATELY, independently hand-authored config that never mentions
       goal machinery at all (no [goal] table, no [[goal.checks]], no
       ledger.md [prompt] files entry) -- written independently of
       `_write_config` so a subtly wrong with_goal=False branch (e.g. an
       off-by-one dropping the wrong [prompt] files entry) can't escape
       detection by comparing itself to itself. This is fixture fidelity, not
       a production lock by itself -- check 1 is what makes this test load-
       bearing.

    Pure config-load + assemble_prompt -- no serve subprocess needed.
    """
    toggled_dir = tmp_path / "toggled"
    toggled_dir.mkdir()
    (toggled_dir / "prompt.md").write_text(_VALID_PROMPT)
    toggled_path = _write_config(toggled_dir, agent_script=_TREADMILL_SCRIPT, with_goal=False)

    naive_dir = tmp_path / "naive"
    naive_dir.mkdir()
    (naive_dir / "prompt.md").write_text(_VALID_PROMPT)
    naive_path = naive_dir / "agent-runner.toml"
    naive_path.write_text(
        "schema_version = 1\n"
        "[agent]\n"
        f"command = {json.dumps(['sh', '-c', _TREADMILL_SCRIPT, 'sh'])}\n"
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{naive_dir}"\n'
        f'log_dir = "{tmp_path / "naive_logs"}"\n'
        "restart_delay_s = 1\n"
        "[prompt]\n"
        'files = ["prompt.md"]\n'
        "[vcs]\n"
        'dirty_action = "ignore"\n'
    )

    cfg_toggled = load_config(toggled_path)
    cfg_naive = load_config(naive_path)
    assert cfg_toggled.goal is None, "with_goal=False must produce cfg.goal is None"
    assert cfg_naive.goal is None

    ctx = {"round_num": 1, "phase": None}
    prompt_toggled = assemble_prompt(cfg_toggled, phase=None, context=ctx)
    prompt_naive = assemble_prompt(cfg_naive, phase=None, context=ctx)

    # Check 1 (ABSOLUTE anchor -- see docstring): for this fixed ctx, the
    # round-context header (prompt_loader._format_context_block's documented
    # ```json round-context ...``` shape) is fully deterministic, so the
    # ENTIRE assembled prompt is pinned exact-bytes, not just "matches the
    # other arm". 785 bytes total (header + the 720-byte _VALID_PROMPT body).
    expected_naive_prompt = (
        '```json round-context\n{\n  "round_num": 1,\n  "phase": null\n}\n```\n\n' + _VALID_PROMPT
    )
    assert prompt_naive == expected_naive_prompt, (
        "ABSOLUTE anchor failed: the goal-naive config's assembled prompt must "
        "be EXACTLY the round-context header + the raw prompt.md body -- any "
        "extra bytes anywhere (even ones that would also land in the toggled "
        f"arm) must fail here.\nprompt_naive={prompt_naive!r}\n"
        f"expected={expected_naive_prompt!r}"
    )

    # Check 2 (differential -- fixture fidelity, see docstring): the
    # with_goal=False fixture matches the independently hand-authored naive
    # config byte for byte.
    assert prompt_toggled == prompt_naive, (
        "the with_goal=False fixture diverged from the independently "
        f"hand-authored goal-naive config -- toggled={prompt_toggled!r}\n"
        f"naive={prompt_naive!r}"
    )
