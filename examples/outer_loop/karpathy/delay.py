#!/usr/bin/env python3
"""Keep or discard one source file during the serve delay. Not a second supervisor.

Never starts serve. Never restarts on 78, 75, or 70. Never creates stop_file.
Does not run git checkout or stash. A missing float is not a keep.

logs/serve.pid is JSON: {"pid": int, "create_time": number}. A bare integer
does not authorize a write. Writes happen only while that pid is alive and
the round_end is still inside the 60 second delay, before the next round starts.

    KARPATHY_WORK=<repo> KARPATHY_SNAPSHOT=<snapshot/train.py> \\
        KARPATHY_QUEUE=<queue/round2.md> python3 delay.py
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

BOOT = 10.0
DELAY_S = 60
DEADLINE_S = 4 * (1800 + 60)
MIN_PROMPT_BYTES = 500
FORBIDDEN_FIRST = frozenset({"-", " ", "\n", "\t", "\r"})


def log(path: Path, msg: str) -> None:
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".swap-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def prompt_smoke_error(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "not utf-8"
    if not text:
        return "empty"
    if text[0] in FORBIDDEN_FIRST:
        return "bad first character"
    if len(data) < MIN_PROMPT_BYTES:
        return "under 500 bytes"
    return None


def read_pid_record(path: Path) -> tuple[int, float] | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw.isdigit():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    create_time = data.get("create_time")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if isinstance(create_time, bool) or not isinstance(create_time, (int, float)):
        return None
    return pid, float(create_time)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def parse_ts(ts: str) -> float:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def finite_value(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value):
        return None
    return value


def load_events(work: Path) -> list[dict]:
    log_dir = work / "logs"
    if not log_dir.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(log_dir.glob("events-*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out


def round_num(ev: dict) -> int | None:
    n = ev.get("round_num")
    if isinstance(n, bool) or not isinstance(n, int):
        return None
    return n


def index_rounds(events: list[dict]) -> dict[int, dict]:
    rounds: dict[int, dict] = {}
    open_round: int | None = None
    for ev in events:
        kind = ev.get("event")
        n = round_num(ev)
        if kind == "round_start" and n is not None:
            rounds.setdefault(n, {})["start"] = ev
            open_round = n
        elif kind == "round_end" and n is not None:
            rounds.setdefault(n, {})["end"] = ev
            if open_round == n:
                open_round = None
        elif kind == "goal_check":
            target = n if n is not None else open_round
            if target is None:
                continue
            checks = rounds.setdefault(target, {}).setdefault("checks", [])
            if isinstance(checks, list):
                checks.append(ev)
    return rounds


def round_value(info: dict) -> float | None:
    checks = info.get("checks") or []
    if not checks:
        return None
    return finite_value(checks[-1].get("value"))


def inside_delay(end_ts: object, now: float) -> bool:
    if not isinstance(end_ts, str):
        return False
    try:
        elapsed = now - parse_ts(end_ts)
    except ValueError:
        return False
    return 0 <= elapsed <= DELAY_S


def apply_round(
    *,
    round_num: int,
    value: float | None,
    source: Path,
    prompt: Path,
    snapshot: Path,
    queue: Path,
    seed: bytes,
    authorized: bool,
    in_delay: bool,
    later_started: bool,
    already: set[str],
) -> str:
    """One decision. Discard only on round 1. Keep only on round 2. Round 3 is idle."""
    if not authorized or not in_delay or later_started:
        return "closed"
    if round_num >= 3 or "idle" in already:
        already.add("idle")
        return "idle no_snapshot_write=1 no_prompt_swap=1"
    if round_num == 1 and "discard" not in already:
        if value is None or not value < BOOT:
            return "no_discard missing_or_not_worse=1 no_restore=1 no_prompt_swap=1"
        queue_bytes = read_bytes(queue)
        smoke = prompt_smoke_error(queue_bytes)
        if smoke is not None:
            return f"no_discard smoke={smoke} no_restore=1 no_prompt_swap=1"
        before = sha256(read_bytes(prompt))
        atomic_replace(source, seed)
        atomic_replace(prompt, queue_bytes)
        already.add("discard")
        restore = sha256(read_bytes(source))
        after = sha256(read_bytes(prompt))
        return (
            "decision=discard"
            f" restore_hash={restore}"
            f" seed_hash={sha256(seed)}"
            f" equal_seed={int(restore == sha256(seed))}"
            f" prompt_hash_before={before}"
            f" prompt_hash_after={after}"
            f" prompt_hashes_differ={int(before != after)}"
            f" value={value}"
            " not_keep=1"
        )
    if round_num == 2 and "keep" not in already:
        if value is None or not value > BOOT:
            return "no_keep missing_or_not_better=1 no_snapshot_write=1 no_prompt_swap=1"
        kept = read_bytes(source)
        atomic_replace(snapshot, kept)
        already.add("keep")
        snap = sha256(kept)
        return (
            "decision=keep"
            f" snapshot_hash={snap}"
            f" seed_hash={sha256(seed)}"
            f" differs_from_seed={int(snap != sha256(seed))}"
            " not_restore=1 prompt_unchanged=1 no_prompt_swap=1"
            f" value={value}"
        )
    return "idle no_snapshot_write=1 no_prompt_swap=1"


def watch(work: Path, snapshot: Path, queue: Path, log_path: Path) -> None:
    source = work / "train.py"
    prompt = work / "prompts" / "program.md"
    pid_file = work / "logs" / "serve.pid"
    seed = read_bytes(snapshot)
    log(log_path, f"seed_hash={sha256(seed)} boot={BOOT} direction=larger-is-better")
    log(log_path, "serve_not_started=0 watcher_start=1")
    latched: tuple[int, float] | None = None
    acted: set[int] = set()
    already: set[str] = set()
    deadline = time.time() + DEADLINE_S
    while time.time() < deadline:
        record = read_pid_record(pid_file)
        if latched is None:
            if record is not None and pid_alive(record[0]):
                latched = record
                log(log_path, f"JSON_PID pid={record[0]} create_time={record[1]}")
            time.sleep(1)
            continue
        if record != latched or not pid_alive(latched[0]):
            log(log_path, "serve_finished pid_gone=1")
            return
        rounds = index_rounds(load_events(work))
        now = time.time()
        for rn, info in sorted(rounds.items()):
            if rn in acted or "end" not in info:
                continue
            later = any(k > rn and "start" in rounds.get(k, {}) for k in rounds)
            end_ts = info["end"].get("ts")
            if later:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_write=1")
                continue
            if not isinstance(end_ts, str):
                continue
            try:
                elapsed = now - parse_ts(end_ts)
            except ValueError:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_write=1")
                continue
            if elapsed < 0:
                continue
            if elapsed > DELAY_S:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_write=1")
                continue
            msg = apply_round(
                round_num=rn,
                value=round_value(info),
                source=source,
                prompt=prompt,
                snapshot=snapshot,
                queue=queue,
                seed=seed,
                authorized=True,
                in_delay=True,
                later_started=False,
                already=already,
            )
            log(log_path, f"round {rn} {msg}")
            if msg != "closed":
                acted.add(rn)
        time.sleep(1)
    log(log_path, "watcher_deadline=1")


def self_check() -> None:
    root = Path(tempfile.mkdtemp(prefix="karpathy-self-"))
    try:
        work = root / "repo"
        prompt = work / "prompts" / "program.md"
        prompt.parent.mkdir(parents=True)
        source = work / "train.py"
        snapshot = root / "snapshot.py"
        queue = root / "round2.md"
        boot = b"BOOT\n"
        worse = b"WORSE\n"
        better = b"BETTER-THAN-BOOT\n"
        queue_text = b"Q" + b"x" * 499
        short_queue = b"Qshort\n"
        source.write_bytes(worse)
        snapshot.write_bytes(boot)
        prompt.write_bytes(b"P" + b"y" * 499)
        queue.write_bytes(queue_text)
        pid_path = root / "serve.pid"
        pid_path.write_text("424242\n", encoding="utf-8")
        if read_pid_record(pid_path) is not None:
            raise SystemExit("bare pid authorized a write")
        pid_path.write_text(
            json.dumps({"pid": os.getpid(), "create_time": 1700000000.5}),
            encoding="utf-8",
        )
        record = read_pid_record(pid_path)
        if record is None or not pid_alive(record[0]):
            raise SystemExit("JSON pid was not authorized")
        already: set[str] = set()
        closed = apply_round(
            round_num=1,
            value=1.0,
            source=source,
            prompt=prompt,
            snapshot=snapshot,
            queue=queue,
            seed=boot,
            authorized=False,
            in_delay=True,
            later_started=False,
            already=already,
        )
        if closed != "closed" or source.read_bytes() != worse:
            raise SystemExit("unauthorized discard wrote")
        late = apply_round(
            round_num=1,
            value=1.0,
            source=source,
            prompt=prompt,
            snapshot=snapshot,
            queue=queue,
            seed=boot,
            authorized=True,
            in_delay=False,
            later_started=False,
            already=already,
        )
        if late != "closed" or source.read_bytes() != worse:
            raise SystemExit("outside delay wrote")
        source.write_bytes(worse)
        prompt_before = prompt.read_bytes()
        (root / "short.md").write_bytes(short_queue)
        smoke_fail = apply_round(
            round_num=1,
            value=1.0,
            source=source,
            prompt=prompt,
            snapshot=snapshot,
            queue=root / "short.md",
            seed=boot,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=set(),
        )
        restored = source.read_bytes() != worse
        swapped = prompt.read_bytes() != prompt_before
        if "no_restore=1" not in smoke_fail or restored or swapped:
            raise SystemExit("smoke failure restored or swapped")
        missing = apply_round(
            round_num=1,
            value=None,
            source=source,
            prompt=prompt,
            snapshot=snapshot,
            queue=queue,
            seed=boot,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=set(),
        )
        if "no_restore=1" not in missing or source.read_bytes() != worse:
            raise SystemExit("missing float discarded")
        discarded = apply_round(
            round_num=1,
            value=1.0,
            source=source,
            prompt=prompt,
            snapshot=snapshot,
            queue=queue,
            seed=boot,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=already,
        )
        restored_boot = source.read_bytes() == boot
        swapped_queue = prompt.read_bytes() == queue_text
        if "decision=discard" not in discarded or not restored_boot or not swapped_queue:
            raise SystemExit("discard did not restore then swap")
        if snapshot.read_bytes() != boot or not queue.exists():
            raise SystemExit("discard rewrote the snapshot or removed the queue")
        source.write_bytes(better)
        prompt_after_swap = prompt.read_bytes()
        missing_keep = apply_round(
            round_num=2,
            value=None,
            source=source,
            prompt=prompt,
            snapshot=snapshot,
            queue=queue,
            seed=boot,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=already,
        )
        if "no_keep" not in missing_keep or snapshot.read_bytes() != boot:
            raise SystemExit("missing float was a keep")
        kept = apply_round(
            round_num=2,
            value=11.0,
            source=source,
            prompt=prompt,
            snapshot=snapshot,
            queue=queue,
            seed=boot,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=already,
        )
        if "decision=keep" not in kept or "not_restore=1" not in kept:
            raise SystemExit("keep missing")
        snap_ok = snapshot.read_bytes() == better
        source_ok = source.read_bytes() == better
        prompt_ok = prompt.read_bytes() == prompt_after_swap
        if not snap_ok or not source_ok or not prompt_ok:
            raise SystemExit("keep restored or swapped the prompt")
        snapshot.write_bytes(better)
        idle = apply_round(
            round_num=3,
            value=12.0,
            source=source,
            prompt=prompt,
            snapshot=snapshot,
            queue=queue,
            seed=boot,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=already,
        )
        if "no_snapshot_write=1" not in idle or snapshot.read_bytes() != better:
            raise SystemExit("round 3 wrote the snapshot")
        if not inside_delay("2026-09-26T00:00:00.000Z", parse_ts("2026-09-26T00:00:30.000Z")):
            raise SystemExit("delay window rejected an inside timestamp")
        if inside_delay("2026-09-26T00:00:00.000Z", parse_ts("2026-09-26T00:01:01.000Z")):
            raise SystemExit("delay window accepted a late timestamp")
    finally:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
        for path in sorted(root.rglob("*"), reverse=True):
            path.rmdir()
        root.rmdir()
    print("SELF_CHECK_OK")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        self_check()
    else:
        work = Path(os.environ["KARPATHY_WORK"])
        snapshot = Path(os.environ["KARPATHY_SNAPSHOT"])
        queue = Path(os.environ["KARPATHY_QUEUE"])
        log_path = Path(os.environ.get("KARPATHY_LOG", str(snapshot.parent / "delay.log")))
        watch(work, snapshot, queue, log_path)
