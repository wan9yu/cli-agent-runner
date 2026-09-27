#!/usr/bin/env python3
"""Append one playbook bullet during the serve delay. Not a second supervisor.

The bullet is copied from goal_check name, value, and satisfied. It is not
copied from child prose and not from another model. A missing finite value
does not append. Round 2 does not append. The old entry stays the prefix.
The playbook is not the ledger. Never starts serve. Never restarts on 78,
75, or 70. Never creates stop_file. Does not swap files[0].

logs/serve.pid is JSON: {"pid": int, "create_time": number}. A bare integer
does not authorize a write.

    ACE_WORK=<repo> ACE_PLAYBOOK=<playbook.md> python3 delay.py
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


def format_value(value: float) -> str:
    if value.is_integer():
        return f"{value:.1f}"
    return format(value, "g")


def round_num(ev: dict) -> int | None:
    n = ev.get("round_num")
    if isinstance(n, bool) or not isinstance(n, int):
        return None
    return n


def bullet_from_check(ev: dict) -> str | None:
    """One bullet from goal_check fields only. Child prose is not an input."""
    name = ev.get("name")
    if not isinstance(name, str) or not name or any(ch.isspace() for ch in name):
        return None
    value = finite_value(ev.get("value"))
    if value is None:
        return None
    satisfied = ev.get("satisfied")
    if not isinstance(satisfied, bool):
        return None
    flag = "true" if satisfied else "false"
    return f"- ACE_DELTA name={name} value={format_value(value)} satisfied={flag}\n"


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


def checks_for(events: list[dict], rn: int) -> list[dict]:
    out: list[dict] = []
    open_round: int | None = None
    for ev in events:
        n = round_num(ev)
        if ev.get("event") == "round_start" and n is not None:
            open_round = n
        elif ev.get("event") == "round_end" and n is not None and open_round == n:
            open_round = None
        elif ev.get("event") == "goal_check":
            target = n if n is not None else open_round
            if target == rn:
                out.append(ev)
    return out


def apply_round(
    *,
    round_num_now: int,
    playbook: Path,
    program: Path,
    seed: bytes,
    check: dict | None,
    authorized: bool,
    in_delay: bool,
    later_started: bool,
    already: set[str],
) -> str:
    if not authorized or not in_delay or later_started:
        return "closed"
    if round_num_now != 1 or "write" in already:
        return "decision=idle no_playbook_write=1 not_wholesale=1"
    if check is None:
        return "no_append missing_check=1 not_child_prose=1"
    bullet = bullet_from_check(check)
    if bullet is None:
        return "no_append missing_value=1 not_child_prose=1"
    body = seed + bullet.encode("utf-8")
    smoke = prompt_smoke_error(body)
    if smoke is not None:
        return f"no_append smoke={smoke}"
    program_before = program.read_bytes()
    atomic_replace(playbook, body)
    already.add("write")
    written = playbook.read_bytes()
    prefix = written[: len(seed)]
    suffix = written[len(seed) :]
    text = written.decode("utf-8")
    program_same = program.read_bytes() == program_before
    return (
        "decision=write"
        f" playbook_hash_before={sha256(seed)}"
        f" playbook_hash_after={sha256(written)}"
        f" prefix_hash={sha256(prefix)}"
        f" prefix_unchanged={int(prefix == seed)}"
        f" suffix_is_new_bullet={int(suffix == bullet.encode())}"
        f" suffix={bullet.strip()!r}"
        f" seed_count={text.count('ACE_SEED_ENTRY')}"
        f" delta_count={text.count('ACE_DELTA')}"
        " append=1 not_wholesale=1 not_ledger=1 not_files0_swap=1"
        f" program_unchanged={int(program_same)} not_child_prose=1 once=1"
    )


def watch(work: Path, playbook: Path, log_path: Path) -> None:
    program = work / "prompts" / "program.md"
    pid_file = work / "logs" / "serve.pid"
    seed = playbook.read_bytes()
    log(log_path, f"prefix_hash={sha256(seed)} before_write=1")
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
            n: ev
            for ev in events
            if ev.get("event") == "round_end" and (n := round_num(ev)) is not None
        }
        starts = {
            n
            for ev in events
            if ev.get("event") == "round_start" and (n := round_num(ev)) is not None
        }
        now = time.time()
        for rn, end in sorted(ends.items()):
            if rn in acted:
                continue
            later = any(k > rn for k in starts)
            end_ts = end.get("ts")
            if later:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_playbook_write=1")
                continue
            if not isinstance(end_ts, str):
                continue
            try:
                elapsed = now - parse_ts(end_ts)
            except ValueError:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_playbook_write=1")
                continue
            if elapsed < 0:
                continue
            if elapsed > DELAY_S:
                acted.add(rn)
                log(log_path, f"round {rn} decision=late no_playbook_write=1")
                continue
            found = checks_for(events, rn)
            msg = apply_round(
                round_num_now=rn,
                playbook=playbook,
                program=program,
                seed=seed,
                check=found[-1] if found else None,
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
    root = Path(tempfile.mkdtemp(prefix="ace-self-"))
    try:
        work = root / "repo"
        program = work / "prompts" / "program.md"
        program.parent.mkdir(parents=True)
        program.write_bytes(b"G" + b"p" * 500)
        playbook = root / "playbook.md"
        seed = b"A" + b"s" * 520 + b"\n- ACE_SEED_ENTRY keep this prefix.\n"
        playbook.write_bytes(seed)
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
        prose = "CHILD PROSE SHOULD NOT BE COPIED"
        check = {
            "name": "metric",
            "value": 1.0,
            "satisfied": True,
            "error": prose,
        }
        already: set[str] = set()
        closed = apply_round(
            round_num_now=1,
            playbook=playbook,
            program=program,
            seed=seed,
            check=check,
            authorized=False,
            in_delay=True,
            later_started=False,
            already=already,
        )
        if closed != "closed" or playbook.read_bytes() != seed:
            raise SystemExit("unauthorized playbook write")
        missing = apply_round(
            round_num_now=1,
            playbook=playbook,
            program=program,
            seed=seed,
            check={"name": "metric", "value": None, "satisfied": True},
            authorized=True,
            in_delay=True,
            later_started=False,
            already=already,
        )
        if "missing_value=1" not in missing or playbook.read_bytes() != seed:
            raise SystemExit("missing value appended")
        written_msg = apply_round(
            round_num_now=1,
            playbook=playbook,
            program=program,
            seed=seed,
            check=check,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=already,
        )
        written = playbook.read_bytes()
        bullet = b"- ACE_DELTA name=metric value=1.0 satisfied=true\n"
        if "decision=write" not in written_msg or not written.startswith(seed):
            raise SystemExit("append did not keep the prefix")
        if written[len(seed) :] != bullet:
            raise SystemExit("suffix is not the one new bullet")
        if prose.encode() in written or "CHILD PROSE" in written_msg:
            raise SystemExit("bullet came from child prose")
        if program.read_bytes() != b"G" + b"p" * 500:
            raise SystemExit("writer swapped files[0]")
        second = apply_round(
            round_num_now=2,
            playbook=playbook,
            program=program,
            seed=seed,
            check=check,
            authorized=True,
            in_delay=True,
            later_started=False,
            already=set(),
        )
        if "no_playbook_write=1" not in second or playbook.read_bytes() != written:
            raise SystemExit("round 2 appended a second bullet")
        print("SELF_CHECK_OK")
        print("WRITE_ONCE round1=1 round2=0")
        print("BULLET_FROM_FIELD name=metric value=1.0 satisfied=true not_child_prose=1")
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
        work = Path(os.environ["ACE_WORK"])
        playbook = Path(os.environ["ACE_PLAYBOOK"])
        log_path = Path(os.environ.get("ACE_LOG", str(playbook.parent / "delay.log")))
        watch(work, playbook, log_path)
