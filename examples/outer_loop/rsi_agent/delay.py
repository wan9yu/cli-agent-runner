#!/usr/bin/env python3
"""Append task B to the course file once during the serve delay.

Not a second supervisor. Never starts serve. Never restarts on 78, 75, or 70.
Never creates stop_file. Does not swap files[0]. Does not restore the source
file. Does not read child prose. A missing float does not block this write.
Round 2 does not write.

logs/serve.pid is JSON: {"pid": int, "create_time": number}. A bare integer
does not authorize a write.

    RSI_WORK=<repo> RSI_COURSE=<course.md> RSI_QUEUE=<queue/task-b.md> \\
        python3 delay.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

DELAY_S = 60
DEADLINE_S = 4 * (1800 + 60)
TASK_B = "TASK_B_CURRICULUM"
MARK_B = "MARK_B = 1"
MIN_PROMPT_BYTES = 500
FORBIDDEN_FIRST = frozenset({"-", " ", "\n", "\t", "\r"})


def log(path: Path, msg: str) -> None:
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def round_num(ev: dict) -> int | None:
    n = ev.get("round_num")
    if isinstance(n, bool) or not isinstance(n, int):
        return None
    return n


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


def apply_round(
    *,
    round_num_now: int,
    course: Path,
    queue: Path,
    program: Path,
    seed: bytes,
    authorized: bool,
    in_delay: bool,
    later_started: bool,
    already: set[str],
) -> str:
    if not authorized or not in_delay or later_started:
        return "closed"
    if round_num_now != 1 or "write" in already:
        return "decision=idle no_course_write=1 not_files0_swap=1"
    addition = queue.read_bytes()
    if addition and not addition.startswith(b"\n") and seed.endswith(b"\n"):
        body = seed + addition
    else:
        body = seed + b"\n" + addition
    text = body.decode("utf-8")
    if TASK_B not in text or MARK_B not in text:
        return "no_write queue_missing_task_b=1"
    smoke = prompt_smoke_error(body)
    if smoke is not None:
        return f"no_write smoke={smoke}"
    program_before = program.read_bytes()
    atomic_replace(course, body)
    already.add("write")
    written = course.read_bytes()
    prefix_ok = written.startswith(seed)
    has_b = TASK_B.encode() in written and MARK_B.encode() in written
    program_same = program.read_bytes() == program_before
    return (
        "decision=write"
        f" course_hash_before={sha256(seed)}"
        f" course_hash_after={sha256(written)}"
        f" prefix_hash={sha256(written[: len(seed)])}"
        f" prefix_unchanged={int(prefix_ok)}"
        f" contains_task_b={int(has_b)}"
        " append=1 not_files0_swap=1 program_unchanged_by_writer=1"
        f" program_unchanged={int(program_same)} once=1"
    )


def watch(work: Path, course: Path, queue: Path, log_path: Path) -> None:
    program = work / "prompts" / "program.md"
    pid_file = work / "logs" / "serve.pid"
    seed = course.read_bytes()
    log(log_path, f"course_hash={sha256(seed)} before_write=1")
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
        events = load_events(work)
        ends = {
            round_num(ev): ev
            for ev in events
            if ev.get("event") == "round_end" and round_num(ev) is not None
        }
        starts = {
            n
            for ev in events
            if ev.get("event") == "round_start" and (n := round_num(ev)) is not None
        }
        now = time.time()
        for rn, end in sorted(ends.items()):
            if rn is None or rn in acted:
                continue
            later = any(k > rn for k in starts)
            end_ts = end.get("ts")
            if later:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_course_write=1")
                continue
            if not isinstance(end_ts, str):
                continue
            try:
                elapsed = now - parse_ts(end_ts)
            except ValueError:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_course_write=1")
                continue
            if elapsed < 0:
                continue
            if elapsed > DELAY_S:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_course_write=1")
                continue
            msg = apply_round(
                round_num_now=rn,
                course=course,
                queue=queue,
                program=program,
                seed=seed,
                authorized=True,
                in_delay=True,
                later_started=False,
                already=already,
            )
            log(log_path, f"round {rn} {msg}")
            acted.add(rn)
        time.sleep(1)
    log(log_path, "watcher_deadline=1")


def self_check() -> None:
    root = Path(tempfile.mkdtemp(prefix="rsi-self-"))
    try:
        work = root / "repo"
        program = work / "prompts" / "program.md"
        program.parent.mkdir(parents=True)
        program.write_bytes(b"Actor standing instruction\n")
        course = root / "course.md"
        queue = root / "task-b.md"
        seed = b"C" + b"a" * 520 + b"\nTASK_A_CURRICULUM\n"
        addition = f"\n{TASK_B}\nSet {MARK_B}.\n".encode()
        course.write_bytes(seed)
        queue.write_bytes(addition)
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
            round_num_now=1,
            course=course,
            queue=queue,
            program=program,
            seed=seed,
            authorized=False,
            in_delay=True,
            later_started=False,
            already=already,
        )
        if closed != "closed" or course.read_bytes() != seed:
            raise SystemExit("unauthorized course write")
        missing = apply_round(
            round_num_now=1,
            course=course,
            queue=queue,
            program=program,
            seed=seed,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=already,
        )
        written = course.read_bytes()
        if "decision=write" not in missing or not written.startswith(seed):
            raise SystemExit("missing float blocked the course write")
        if TASK_B.encode() not in written or MARK_B.encode() not in written:
            raise SystemExit("task B was not appended")
        if program.read_bytes() != b"Actor standing instruction\n":
            raise SystemExit("writer swapped files[0]")
        second = apply_round(
            round_num_now=1,
            course=course,
            queue=queue,
            program=program,
            seed=seed,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=already,
        )
        if "no_course_write=1" not in second or course.read_bytes() != written:
            raise SystemExit("task B was written twice")
        round2 = apply_round(
            round_num_now=2,
            course=course,
            queue=queue,
            program=program,
            seed=seed,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=set(),
        )
        if "no_course_write=1" not in round2 or course.read_bytes() != written:
            raise SystemExit("round 2 wrote the course")
        print("SELF_CHECK_OK")
        print("WRITE_ONCE round1=1 round2=0")
    finally:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
        for path in sorted(root.rglob("*"), reverse=True):
            path.rmdir()
        root.rmdir()


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        self_check()
    else:
        work = Path(os.environ["RSI_WORK"])
        course = Path(os.environ["RSI_COURSE"])
        queue = Path(os.environ["RSI_QUEUE"])
        log_path = Path(os.environ.get("RSI_LOG", str(course.parent / "delay.log")))
        watch(work, course, queue, log_path)
