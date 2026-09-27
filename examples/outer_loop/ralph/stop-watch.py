#!/usr/bin/env python3
"""Create stop_file only after round_end, inside the delay, while serve is alive.

Not a second supervisor. Never starts serve. Never restarts on 78, 75, or 70.

logs/serve.pid is JSON: {"pid": int, "create_time": number}. A bare integer
is only the legacy form. Requiring all-digit text skips the stop.

Paths come from the environment, not from this file:

    RALPH_WORK=/path/to/repo RALPH_STOP=/path/beside/the/repo/stop-requested \\
        python3 stop-watch.py

RALPH_STOP must be the boot-configured stop_file. It is absent at boot.
A false goal_check does not create it.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Longer than max_rounds * (round_budget_s + restart_delay_s). Not the pin.
DEADLINE_S = 7 * (1800 + 60)
TERMINAL = {
    "stop_file_detected",
    "max_rounds_reached",
    "config_broken",
    "crash_loop",
    "stalled_no_progress",
}


def log(path: Path, msg: str) -> None:
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line, end="", flush=True)


def read_pid_record(path: Path) -> tuple[int, float | None] | None:
    """Return (pid, create_time) from a JSON serve.pid, or a legacy bare int."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if raw.isdigit():
            pid = int(raw)
            return (pid, None) if pid > 0 else None
        return None
    if isinstance(data, bool):
        return None
    if isinstance(data, int):
        return (data, None) if data > 0 else None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    create_time = data.get("create_time")
    if create_time is None:
        return (pid, None)
    if isinstance(create_time, bool) or not isinstance(create_time, (int, float)):
        return None
    return (pid, float(create_time))


def pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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
        for i, line in enumerate(text.splitlines(), 1):
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                ev["_line"] = i
                out.append(ev)
    return out


def index_rounds(events: list[dict]) -> dict[int, dict]:
    rounds: dict[int, dict] = {}
    open_round: int | None = None
    for ev in events:
        kind = ev.get("event")
        n = ev.get("round_num")
        if kind == "round_start" and isinstance(n, int) and not isinstance(n, bool):
            rounds.setdefault(n, {})["start"] = ev
            open_round = n
        elif (
            kind in ("agent_exit", "dirty_detected")
            and isinstance(n, int)
            and not isinstance(n, bool)
        ):
            rounds.setdefault(n, {})[kind] = ev
            open_round = n
        elif kind == "round_end" and isinstance(n, int) and not isinstance(n, bool):
            rounds.setdefault(n, {})["end"] = ev
            if open_round == n:
                open_round = None
        elif kind == "goal_check":
            target = n if isinstance(n, int) and not isinstance(n, bool) else open_round
            if target is None:
                continue
            rounds.setdefault(target, {}).setdefault("checks", []).append(ev)
    return rounds


def action_for(
    info: dict,
    *,
    pid_alive_now: bool,
    stop_exists: bool,
    later_started: bool,
) -> str:
    """Return create only after round_end, when satisfied is true and pid is alive."""
    if "end" not in info or not info.get("checks"):
        return "wait"
    satisfied = all(
        isinstance(c.get("satisfied"), bool) and c.get("satisfied") for c in info["checks"]
    )
    if not satisfied:
        return "red_noop"
    if later_started:
        return "late_noop"
    if not pid_alive_now:
        return "dead_noop"
    if stop_exists:
        return "already"
    return "create"


def watch(work: Path, stop: Path, log_path: Path) -> None:
    log(log_path, "watcher_start")
    acted: set[int] = set()
    pid_file = work / "logs" / "serve.pid"
    deadline = time.time() + DEADLINE_S
    while time.time() < deadline:
        record = read_pid_record(pid_file)
        pid = None if record is None else record[0]
        alive = pid_alive(pid)
        events = load_events(work)
        rounds = index_rounds(events)
        for rn, info in sorted(rounds.items()):
            if rn in acted:
                continue
            later = any(k > rn and "start" in rounds.get(k, {}) for k in rounds)
            decision = action_for(
                info,
                pid_alive_now=alive,
                stop_exists=stop.exists(),
                later_started=later,
            )
            if decision == "wait":
                continue
            log(
                log_path,
                f"round {rn} decision={decision} pid={pid} alive={alive} "
                f"line={info['end'].get('_line')}",
            )
            if decision == "create":
                stop.write_text(
                    f"delay stop after round {rn} goal_check satisfied "
                    + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    + "\n",
                    encoding="utf-8",
                )
                log(log_path, f"created stop_file after round {rn}")
            if decision != "dead_noop":
                acted.add(rn)
        kinds = {ev.get("event") for ev in events}
        if events and not pid_file.exists() and kinds & TERMINAL:
            log(log_path, "serve finished; watcher stop")
            return
        time.sleep(1)
    log(log_path, "watcher deadline")


def read_pid_cli(path: Path) -> int:
    record = read_pid_record(path)
    if record is None:
        print("JSON_PID_UNPARSED", flush=True)
        return 1
    pid, create_time = record
    shown = "none" if create_time is None else repr(create_time)
    print(f"JSON_PID pid={pid} create_time={shown}", flush=True)
    return 0


def self_check() -> int:
    import tempfile

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pid_path = root / "serve.pid"
        pid_path.write_text(
            '{"pid": 424242, "create_time": 1700000000.5}\n',
            encoding="utf-8",
        )
        record = read_pid_record(pid_path)
        if record != (424242, 1700000000.5):
            failures.append(f"json record {record!r}")
        else:
            print("JSON_PID pid=424242 create_time=1700000000.5", flush=True)
        bare = root / "bare.pid"
        bare.write_text("99\n", encoding="utf-8")
        if read_pid_record(bare) != (99, None):
            failures.append("legacy bare int rejected")
        junk = root / "junk.pid"
        junk.write_text('{"pid": ', encoding="utf-8")
        if read_pid_record(junk) is not None:
            failures.append("partial json accepted")
        red = action_for(
            {"end": {"_line": 1}, "checks": [{"satisfied": False}]},
            pid_alive_now=True,
            stop_exists=False,
            later_started=False,
        )
        if red != "red_noop":
            failures.append(f"red -> {red}")
        waiting = action_for(
            {"checks": [{"satisfied": True}]},
            pid_alive_now=True,
            stop_exists=False,
            later_started=False,
        )
        if waiting != "wait":
            failures.append(f"no round_end -> {waiting}")
        dead = action_for(
            {"end": {"_line": 2}, "checks": [{"satisfied": True}]},
            pid_alive_now=False,
            stop_exists=False,
            later_started=False,
        )
        if dead != "dead_noop":
            failures.append(f"dead -> {dead}")
        late = action_for(
            {"end": {"_line": 3}, "checks": [{"satisfied": True}]},
            pid_alive_now=True,
            stop_exists=False,
            later_started=True,
        )
        if late != "late_noop":
            failures.append(f"late -> {late}")
        create = action_for(
            {"end": {"_line": 4}, "checks": [{"satisfied": True}]},
            pid_alive_now=True,
            stop_exists=False,
            later_started=False,
        )
        if create != "create":
            failures.append(f"create -> {create}")
        live = root / "live.pid"
        live.write_text(
            json.dumps({"pid": os.getpid(), "create_time": 1.0}),
            encoding="utf-8",
        )
        live_record = read_pid_record(live)
        if live_record is None or live_record[0] != os.getpid() or not pid_alive(live_record[0]):
            failures.append("live json pid not alive")
    if failures:
        print("SELF_CHECK_FAIL " + "; ".join(failures), flush=True)
        return 1
    print("SELF_CHECK_OK", flush=True)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--read-pid":
        return read_pid_cli(Path(argv[2]))
    if len(argv) == 2 and argv[1] == "--self-check":
        return self_check()
    if len(argv) != 1:
        print(
            "usage: stop-watch.py [--self-check | --read-pid PATH]",
            file=sys.stderr,
        )
        return 2
    work = os.environ.get("RALPH_WORK", "")
    stop = os.environ.get("RALPH_STOP", "")
    if not work or not stop:
        print(
            "usage: RALPH_WORK=... RALPH_STOP=... python3 stop-watch.py",
            file=sys.stderr,
        )
        return 2
    watch(Path(work), Path(stop), Path(stop).parent / "stop-watch.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
