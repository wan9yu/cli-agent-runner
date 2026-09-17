"""The ``[goal]`` steering loop: objective checks plus an advisory-only
treadmill assessor, both opt-in behind ``cfg.goal is not None``.

Two halves:

- ``run_goal_checks`` (child-side): runs the operator's ``[goal].checks`` and
  emits one ``goal_check`` event per check -- a bounded, reap-safe wrapper
  around ``agent_runner._bounded.run_bounded`` so a hung or failing check
  never blocks the round beyond its declared budget.
- ``assess_treadmill`` + ``write_ledger_advisory`` (the ADVISORY half): reads
  the recent events tail, and when the agent is busy-but-not-converging
  (activity every round, no goal_check ever changes status/value), folds ONE
  grounded ``Advisory`` into a supervisor-owned lessons ledger the operator
  lists in ``[prompt] files`` -- structurally unable to kill or branch a
  round (``Advisory`` has no kill/severity/action field).

This is runner.py's ONE events-reading edge (see
tests/invariants/test_module_boundaries.py's ouroboros carve-out): the read
happens here, never in runner.py itself, and the fold is advisory-only, so it
proves-by-construction it cannot alter round control flow.

Kept out of the startup import graph on purpose (see
tests/invariants/test_import_footprint.py): ``runner.py`` imports every
function here function-scope, behind ``cfg.goal is not None``.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_runner import event_log, events
from agent_runner._bounded import run_bounded
from agent_runner._redact import redact_secrets
from agent_runner._throttle import _coerce_float
from agent_runner.config import GoalConfig
from agent_runner.events import emit


def _last_float_token(stdout: str) -> float | None:
    """The last whitespace-separated token of ``stdout`` that parses as a
    FINITE float, else ``None``. A plain stdout scrape -- not a read of a
    plugin-controlled events-*.jsonl field -- so no ``_coerce_float`` is
    needed here; ``assess_treadmill`` reads this field back (once it is
    round-tripped through JSON) through ``_coerce_float``. ``nan``/``inf``
    are rejected the same as an unparseable token -- ``nan`` isn't even equal
    to itself, which would break the assessor's "did the value change"
    comparison."""
    for token in reversed(stdout.split()):
        try:
            value = float(token)
        except ValueError:
            continue
        if math.isfinite(value):
            return value
    return None


def _resolve_check_cwd(check_cwd: str | None, work_dir: Path) -> Path:
    """Resolve a ``[[goal.checks]]`` entry's ``cwd`` against ``work_dir`` (see
    ``_GoalCheckConfig``'s docstring: the check runner resolves it, not the
    config loader). ``/`` keeps an already-absolute ``check_cwd`` as-is and
    expands a leading ``~``; ``None`` falls back to ``work_dir`` itself."""
    if not check_cwd:
        return work_dir
    return work_dir / Path(check_cwd).expanduser()


def run_goal_checks(
    cfg_goal: GoalConfig,
    *,
    work_dir: Path,
    log_dir: Path,
    dry_run: bool,
) -> None:
    """Run every ``[[goal.checks]]`` entry, bounded by its own ``timeout_s``,
    and emit one ``goal_check`` event per check. ``dry_run`` emits
    ``{skipped: True}`` for every check and never spawns a subprocess. A
    misconfigured check (missing binary, missing cwd, ...) must not crash the
    round child -- ``run_bounded``'s own ``subprocess.Popen`` call can raise
    OSError (FileNotFoundError/PermissionError/NotADirectoryError) BEFORE its
    own timeout machinery ever engages, so that's caught here and reported as
    an unsatisfied check (advisory-only, never a crash-loop)."""
    for check in cfg_goal.checks:
        if dry_run:
            emit(log_dir, events.GOAL_CHECK, name=check.name, skipped=True)
            continue
        cwd = _resolve_check_cwd(check.cwd, work_dir)
        try:
            result = run_bounded(check.cmd, cwd=cwd, timeout_s=check.timeout_s)
        except OSError as exc:
            emit(
                log_dir,
                events.GOAL_CHECK,
                name=check.name,
                satisfied=False,
                value=None,
                timed_out=False,
                skipped=False,
                error=str(exc)[:200],
            )
            continue
        emit(
            log_dir,
            events.GOAL_CHECK,
            name=check.name,
            satisfied=result.rc == 0 and not result.timed_out,
            value=_last_float_token(result.stdout),
            timed_out=result.timed_out,
            skipped=False,
        )


@dataclass(frozen=True)
class Advisory:
    """One grounded observation folded into the lessons ledger.

    Deliberately has NO kill/severity/action/terminate field -- this is the
    type-structural half of the advisory-only firewall: an ``Advisory`` value
    cannot express a command, so nothing downstream of
    :func:`assess_treadmill` can be wired to end a round even by mistake.
    """

    observation: str
    question: str
    confidence: Literal["low", "medium", "high"]


_TREADMILL_WINDOW_ROUNDS = 3

_LEDGER_MAX_BYTES = 8192
# Blocks are separated by a single blank line -- plain prose, no HTML-comment
# marker cluttering every prompt this ledger gets folded into ([prompt] files
# is raw text, not rendered markdown, so a literal `<!-- ... -->` would be
# visible to the agent on every round). Safe as a round-trip delimiter because
# a block's own generated lines (below) never contain a blank line internally.
_LEDGER_BLOCK_SEP = "\n\n"


def _format_advisory_block(*, observation: str, question: str, confidence: str, ts: str) -> str:
    # No trailing newline -- _LEDGER_BLOCK_SEP alone supplies the blank line
    # between this block and the next one it's joined with.
    return (
        f"### Goal assessment -- {ts}\n"
        f"- Observation: {observation}\n"
        f"- Question: {question}\n"
        f"- Confidence: {confidence}"
    )


def write_ledger_advisory(ledger_path: Path, advisory: Advisory, *, log_dir: Path) -> None:
    """PREPEND ``advisory`` as one bounded markdown block onto ``ledger_path``
    (newest first), TRUNCATE the whole file to <= 8192 bytes at a block
    boundary (oldest blocks dropped first, the newest always kept even if it
    alone exceeds the cap), and write it atomically (``<path>.tmp`` then
    ``os.replace``, so a reader never observes a half-written ledger).

    Every field is run through ``_redact.redact_secrets`` BEFORE it touches
    disk or the emitted event -- a goal-check's stdout (the raw material an
    ``Advisory`` is built from) can carry a leaked token. The ledger itself
    sits in ``[prompt] files`` (an operator-listed, agent-readable file), so
    this is the same durable-log redaction discipline ``_redact.py`` already
    applies to other free-text event fields.

    Emits ``events.GOAL_ASSESSMENT`` AFTER the atomic write lands, with the
    (redacted) advisory fields verbatim -- the one observability breadcrumb
    for the fold, and the edge-trigger marker :func:`assess_treadmill` scans
    for so the same stuck signature doesn't refire every round.
    """
    observation = redact_secrets(advisory.observation)
    question = redact_secrets(advisory.question)
    confidence = redact_secrets(advisory.confidence)

    ts = events.now_iso_ms()
    new_block = _format_advisory_block(
        observation=observation, question=question, confidence=confidence, ts=ts
    )

    try:
        existing = ledger_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    prior_blocks = [b for b in existing.split(_LEDGER_BLOCK_SEP) if b.strip()]

    sep_bytes = len(_LEDGER_BLOCK_SEP.encode("utf-8"))
    kept: list[str] = []
    total = 0
    for block in (new_block, *prior_blocks):
        block_bytes = len(block.encode("utf-8"))
        addition = block_bytes + (sep_bytes if kept else 0)
        if kept and total + addition > _LEDGER_MAX_BYTES:
            break  # oldest-first drop; the newest block (kept==[] case) is never dropped
        kept.append(block)
        total += addition

    content = _LEDGER_BLOCK_SEP.join(kept)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ledger_path.with_name(ledger_path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, ledger_path)

    emit(
        log_dir,
        events.GOAL_ASSESSMENT,
        observation=observation,
        question=question,
        confidence=confidence,
    )


def _round_num_of(ev: dict) -> int | None:
    """``ev["round_num"]`` when it's a genuine int (bool is an int subclass --
    excluded), else ``None`` -- only ``round_substrate_before``/``_after``,
    ``round_start``, ``agent_spawn``/``agent_exit``, and the dirty events carry
    this field directly; ``goal_check``/``goal_assessment`` do not."""
    rn = ev.get("round_num")
    if isinstance(rn, bool) or not isinstance(rn, int):
        return None
    return rn


def _tag_rounds(tail: list[dict]) -> list[tuple[int | None, dict]]:
    """Attribute every event in ``tail`` to a round number: an event carrying
    its own ``round_num`` sets the "current round"; an event without one
    (``goal_check``/``goal_assessment``) inherits whatever round most recently
    set it. An event before any round_num has been seen at all (a rotated-out
    tail boundary) tags as ``None`` and contributes no signal."""
    tagged: list[tuple[int | None, dict]] = []
    current: int | None = None
    for ev in tail:
        rn = _round_num_of(ev)
        if rn is not None:
            current = rn
        tagged.append((current, ev))
    return tagged


def _git_head_moved(bucket: list[dict]) -> bool:
    """True iff this round's own ``round_substrate_before``/``_after`` pair
    both resolved a git HEAD and it differs -- the agent (or an auto-commit)
    moved HEAD during the round, independent of the dirty-tree detector."""
    before = after = None
    for ev in bucket:
        kind = ev.get("event")
        if kind == events.ROUND_SUBSTRATE_BEFORE:
            before = ev.get("git_head")
        elif kind == events.ROUND_SUBSTRATE_AFTER:
            after = ev.get("git_head")
    return before is not None and after is not None and before != after


def _round_has_activity(bucket: list[dict]) -> bool:
    """CLI-agnostic activity signal for one round's bucket: >= 1 dirty event,
    or its own substrate before/after git HEAD differs. Shared by the window
    gate and the backward episode-boundary walk below."""
    has_dirty = any(
        ev.get("event") in (events.DIRTY_DETECTED, events.DIRTY_AUTO_COMMITTED) for ev in bucket
    )
    return has_dirty or _git_head_moved(bucket)


def _coerced_check_value(raw_value: object) -> float | None:
    """``goal_check.value`` read defensively: ``None`` stays ``None``
    (unparsed check output); anything else goes through ``_coerce_float`` so a
    poisoned/non-finite field degrades to "unparsed" too, rather than
    poisoning the "did the value change" comparison with a NaN that never
    equals itself (see ``_last_float_token``'s docstring)."""
    if raw_value is None:
        return None
    coerced = _coerce_float(raw_value, math.nan)
    return None if math.isnan(coerced) else coerced


def _check_signature(bucket: list[dict]) -> dict[str, tuple[bool, float | None]]:
    """Per-check-name ``(satisfied, value)`` for every non-skipped
    ``goal_check`` in one round's bucket -- the window gate folds these across
    the whole window; the episode-boundary walk compares one round's
    signature at a time against the window's constant one."""
    sig: dict[str, tuple[bool, float | None]] = {}
    for ev in bucket:
        if ev.get("event") != events.GOAL_CHECK or ev.get("skipped"):
            continue
        name = ev.get("name")
        if not isinstance(name, str):
            continue
        sig[name] = (bool(ev.get("satisfied")), _coerced_check_value(ev.get("value")))
    return sig


def assess_treadmill(
    log_dir: Path, *, current_round: int, k: int = _TREADMILL_WINDOW_ROUNDS
) -> Advisory | None:
    """Advisory-only treadmill signal: fires IFF, across the last ``k``
    COMPLETED rounds strictly before ``current_round`` (default
    :data:`_TREADMILL_WINDOW_ROUNDS`) --

    1. **Activity** (CLI-agnostic, :func:`_round_has_activity`): EACH round
       shows >= 1 of ``events.DIRTY_DETECTED`` / ``events.DIRTY_AUTO_COMMITTED``,
       OR its own ``round_substrate_before``/``_after`` git HEAD differs.
       Deliberately never reads ``agent_usage_recorded`` /
       ``anomaly_repetitive_tool`` -- both are agent-specific, and a custom
       ``[agent] command`` with no usage-emitting plugin must still be
       assessable.
    2. **No convergence, and not already met**: no ``goal_check``'s
       ``satisfied`` OR (``_coerce_float``-read) ``value`` differs across the
       rounds it appeared in, for ANY check name, AND at least one check in
       the window is unsatisfied. A window with no non-skipped ``goal_check``
       at all has no signal to judge convergence by, so it does NOT fire
       (vacuous "nothing changed" is not treated as "stuck"). A window where
       EVERY check is satisfied -- even if that satisfied signature is
       perfectly constant -- is "done", not stuck, so it does NOT fire
       either: a constant signature alone doesn't mean stuck, it can just as
       well mean the goal was met and the agent kept working past it.
    3. **Not already fired** (edge-trigger, keyed off the EPISODE, not the
       window): walk backward from the window over earlier completed rounds
       that still match the window's activity + per-check signature; the
       first round the pattern breaks marks the episode boundary, and the
       round right after it is the episode's start. No ``goal_assessment``
       may appear in the tail from that round onward -- the emitted event IS
       the fired-marker. This re-arms exactly when the design calls for it:
       once the signature clears (gate 1 fails) or a check's status/value
       moves (gate 2 fails) somewhere in the episode, NOT merely once the
       fixed-size window slides past the round that fired.

    ``current_round`` excludes the round currently being assessed FOR: under
    `serve`, ``round_substrate_before(round_num=current_round)`` is already in
    the tail (serve emits it before spawning the child -- see
    ``cli/serve_cmd.py``'s ``_capture_substrate``), so without this the
    in-progress round would itself be "the newest round in the window" with
    no activity yet recorded, permanently failing gate 1. NOT the forbidden
    ``agent`` parameter -- it makes "last k completed rounds" true by
    construction, independent of any particular pre-spawn serve event.

    Reads only the newest two monthly ``events-*.jsonl`` files
    (:func:`event_log.newest_scope`) -- the same tail window every other
    events-derived detector in this codebase uses.
    """
    tail = list(event_log.scan(log_dir, event_log.newest_scope(2)))
    tagged = _tag_rounds(tail)

    round_nums = sorted({rn for rn, _ in tagged if rn is not None and rn < current_round})
    if len(round_nums) < k:
        return None
    window = round_nums[-k:]

    buckets: dict[int, list[dict]] = {}
    for rn, ev in tagged:
        if rn in round_nums:
            buckets.setdefault(rn, []).append(ev)

    # Gate 1: activity in EVERY round of the window.
    for rn in window:
        if not _round_has_activity(buckets.get(rn, [])):
            return None

    # Gate 2: no goal_check status/value change across the window, and not
    # every check already satisfied (a constant ALL-satisfied signature is
    # "done", not "stuck" -- see the constant_sig check below).
    check_series: dict[str, list[tuple[bool, float | None]]] = {}
    for rn in window:
        for name, entry in _check_signature(buckets.get(rn, [])).items():
            check_series.setdefault(name, []).append(entry)
    if not check_series:
        return None  # nothing objective to judge convergence by -- no signal, no advisory
    for series in check_series.values():
        if len(set(series)) > 1:
            return None  # a check moved -- converging, not stuck
    constant_sig = {name: series[0] for name, series in check_series.items()}
    if all(satisfied for satisfied, _value in constant_sig.values()):
        return None  # goal met and stable -- convergence, not a treadmill

    # Gate 3: edge-trigger, keyed off the EPISODE start, not the window start.
    # Walk backward over earlier completed rounds while each still matches
    # the window's activity + signature; the last round that still matches is
    # the episode's start.
    window_start_idx = len(round_nums) - k
    episode_start = window[0]
    for prev_idx in range(window_start_idx - 1, -1, -1):
        prev_rn = round_nums[prev_idx]
        prev_bucket = buckets.get(prev_rn, [])
        if not _round_has_activity(prev_bucket):
            break
        if _check_signature(prev_bucket) != constant_sig:
            break
        episode_start = prev_rn

    start_idx = next(i for i, (rn, _ev) in enumerate(tagged) if rn == episode_start)
    already_fired = any(ev.get("event") == events.GOAL_ASSESSMENT for _rn, ev in tagged[start_idx:])
    if already_fired:
        return None

    names = sorted(check_series)
    return Advisory(
        observation=(
            f"For the last {k} rounds the working tree kept changing, but "
            f"goal-check(s) {', '.join(names)} never changed status or value."
        ),
        question=(
            "What specifically is blocking progress on the unmet check(s) -- "
            "is there a concrete next step, or is the check itself measuring "
            "the wrong thing?"
        ),
        confidence="medium",
    )
