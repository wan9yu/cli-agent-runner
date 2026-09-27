#!/usr/bin/env python3
"""Create stop_file only after a second identical red. Not a second supervisor.

Never starts serve. Never loops agent-runner round. Never restarts on
78, 75, or 70. Never reads assistant text. Never compares full check
text. An advisory is not a stop.

The only serve-time mutation is creating the boot-configured stop file
after round 2 round_end, when both goal checks are red and the
fingerprint matches, while logs/serve.pid is a live JSON pid whose
/proc start time matches create_time within 1 second, inside the 60
second delay, and before round 3 round_start. Round 1 records the red
and does not create the stop file.

A finite value makes the fingerprint (satisfied, value). When both
rounds have no value, the fingerprint is satisfied only, and two reds
still match. One value and one missing value do not match. Different
values do not match.

    REPEAT_WORK=<repo> REPEAT_CFG=<beside> REPEAT_STOP=<STOP> python3 delay.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
import os
import sys
import tempfile
import time
import tomllib
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

DELAY_S = 60
WRITE_AFTER_S = 2.0
WRITE_BEFORE_S = 50.0
DEADLINE_S = 5 * (1800 + 60)
MIN_PROMPT_BYTES = 500
STABLE = "VALUE = 0.25"
FLIP = "VALUE = 1.0"
PLANTED_VALUE = 0.25
FORBIDDEN_FIRST = frozenset("- \n\t\r")
GIVEUP = {
    "config_broken",
    "crash_loop",
    "mem_loop",
    "mem_loop_persistent",
    "stalled_no_progress",
}
PROMPT_MARKERS = (
    FLIP,
    "fail_source.py",
    "delay.py",
    "agent-runner.toml",
)


def log(path: Path, msg: str) -> None:
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def last_float(stdout: str) -> float | None:
    for token in reversed(stdout.split()):
        try:
            value = float(token)
        except ValueError:
            continue
        if math.isfinite(value):
            return value
    return None


def finite_value(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value):
        return None
    return value


def leaked(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker and marker in text]


def start_time_from_stat(stat_text: str, btime_text: str, ticks: float) -> float | None:
    """Start time from stat field 22 and btime. Does not call ps or psutil."""
    rparen = stat_text.rfind(")")
    if rparen < 0 or ticks <= 0:
        return None
    fields = stat_text[rparen + 2 :].split()
    if len(fields) < 20:
        return None
    boot = None
    for line in btime_text.splitlines():
        if line.startswith("btime "):
            parts = line.split()
            if len(parts) < 2:
                return None
            try:
                boot = float(parts[1])
            except ValueError:
                return None
            break
    if boot is None:
        return None
    try:
        start_ticks = float(fields[19])
    except ValueError:
        return None
    return boot + start_ticks / ticks


def linux_create_time(pid: int) -> float | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        btime = Path("/proc/stat").read_text(encoding="utf-8")
        ticks = float(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError):
        return None
    return start_time_from_stat(stat, btime, ticks)


def read_pid_record(path: Path) -> tuple[int, float] | None:
    """JSON {pid, create_time} only. A bare integer does not authorize a write."""
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
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        return None
    if isinstance(create_time, bool) or not isinstance(create_time, (int, float)):
        return None
    return pid, float(create_time)


def identity_alive(path: Path) -> tuple[bool, int | None, float | None]:
    record = read_pid_record(path)
    if record is None:
        return False, None, None
    pid, recorded = record
    try:
        os.kill(pid, 0)
    except OSError:
        return False, pid, recorded
    actual = linux_create_time(pid)
    if actual is None or abs(actual - recorded) >= 1.0:
        return False, pid, recorded
    return True, pid, recorded


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise SystemExit("toml is not a table")
    return data


def check_cmd_line(data: dict) -> str:
    checks = data.get("goal", {}).get("checks", [])
    if not isinstance(checks, list) or len(checks) != 1 or not isinstance(checks[0], dict):
        return ""
    cmd = checks[0].get("cmd")
    if not isinstance(cmd, list):
        return ""
    return json.dumps(cmd, ensure_ascii=False)


def as_int(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def load_events(log_dir: Path) -> list[dict]:
    if not log_dir.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(log_dir.glob("events-*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event["_line"] = number
                out.append(event)
    return out


def index_rounds(events: list[dict]) -> dict[int, dict]:
    rounds: dict[int, dict] = {}
    open_round: int | None = None
    for event in events:
        kind = event.get("event")
        number = as_int(event.get("round_num"))
        if kind == "round_start" and number is not None:
            rounds.setdefault(number, {})["start"] = event
            open_round = number
        elif kind == "round_end" and number is not None:
            rounds.setdefault(number, {})["end"] = event
            if open_round == number:
                open_round = None
        elif kind == "goal_check":
            target = number if number is not None else open_round
            skipped = event.get("skipped")
            if target is None or (isinstance(skipped, bool) and skipped):
                continue
            rounds.setdefault(target, {}).setdefault("checks", []).append(event)
    return rounds


def parse_ts(raw: object) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def round_started(events: list[dict], round_num: int) -> bool:
    for event in events:
        if event.get("event") == "round_start" and as_int(event.get("round_num")) == round_num:
            return True
    return False


def giveup_present(events: list[dict]) -> bool:
    return any(event.get("event") in GIVEUP for event in events)


def atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".stop-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def is_red(check: dict | None) -> bool:
    if not isinstance(check, dict):
        return False
    satisfied = check.get("satisfied")
    return isinstance(satisfied, bool) and not satisfied


def fingerprint(check: dict | None) -> tuple[bool, float] | tuple[bool] | None:
    """(satisfied, value) when value is finite. satisfied only when absent."""
    if not isinstance(check, dict):
        return None
    satisfied = check.get("satisfied")
    if not isinstance(satisfied, bool):
        return None
    value = finite_value(check.get("value"))
    if value is None:
        return (satisfied,)
    return (satisfied, value)


def fingerprints_match(left: dict | None, right: dict | None) -> bool:
    left_fp = fingerprint(left)
    right_fp = fingerprint(right)
    if left_fp is None or right_fp is None:
        return False
    return left_fp == right_fp


def stop_decision(
    *,
    round_num: int,
    alive: bool,
    later: bool,
    elapsed: float,
    already: bool,
    giveup: bool,
    first: dict | None,
    second: dict | None,
) -> str:
    """Create only after a second identical red, inside the delay window."""
    if round_num == 1:
        return "record"
    if round_num != 2:
        return "idle"
    if not alive:
        return "dead"
    if later or giveup:
        return "late"
    if elapsed < WRITE_AFTER_S:
        return "wait"
    if elapsed > WRITE_BEFORE_S or elapsed > DELAY_S:
        return "window"
    if already:
        return "already"
    if not is_red(first) or not is_red(second) or not fingerprints_match(first, second):
        return "hold"
    return "create"


def maybe_create(path: Path, decision: str) -> bool:
    if decision != "create" or path.exists():
        return False
    atomic_replace(path, b"identical-red\n")
    return True


def load_module(path: Path):
    name = f"stable_{path.stem}_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_check(path: Path) -> tuple[int, float | None]:
    module = load_module(path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = module.main()
    if isinstance(rc, bool) or not isinstance(rc, int):
        rc = 1
    return rc, last_float(buf.getvalue())


def red_check(value: float | None, *, text: str = "") -> dict:
    event: dict[str, object] = {
        "event": "goal_check",
        "name": "stable",
        "satisfied": False,
        "text": text,
    }
    if value is not None:
        event["value"] = value
    return event


def prompt_smoke(text: str) -> str | None:
    if not text or text[0] in FORBIDDEN_FIRST:
        return "first"
    if len(text.encode("utf-8")) < MIN_PROMPT_BYTES:
        return "short"
    return None


def same_dir(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def bundled_errors(root: Path) -> list[str]:
    failures: list[str] = []
    prompt = root / "prompts" / "program.md"
    source = root / "fail_source.py"
    example = root / "agent-runner.toml.example"
    if prompt.is_file():
        text = prompt.read_text(encoding="utf-8")
        if prompt_smoke(text) or leaked(text, PROMPT_MARKERS):
            failures.append("bundled prompt")
    if source.is_file():
        body = source.read_text(encoding="utf-8")
        if STABLE not in body or FLIP in body:
            failures.append("bundled failure source")
    if example.is_file():
        data = load_toml(example)
        joined = example.read_text(encoding="utf-8")
        command = data.get("agent", {}).get("command", [])
        if "agent" not in data:
            failures.append("example missing [agent]")
        if "--no-session" in command or "--no-session" in joined:
            failures.append("example has --no-session")
        if command[1:] != ["-p", "-na", "--mode", "json", "--model", "PROVIDER/MODEL"]:
            failures.append("example command shape")
        runtime = data.get("runtime", {})
        if runtime.get("max_rounds") != 3 or runtime.get("stop_file") != "STOP":
            failures.append("example stop")
        if data.get("vcs", {}).get("dirty_action") != "ignore" or "auto_commit" in joined:
            failures.append("example vcs")
        if FLIP in joined:
            failures.append("example flip")
    if (root / "STOP").exists():
        failures.append("real stop exists")
    return failures


def self_check() -> int:
    root = Path(__file__).resolve().parent
    source = root / "fail_source.py"
    source_before = source.read_bytes() if source.is_file() else None
    failures = bundled_errors(root)
    text = Path(__file__).read_text(encoding="utf-8")
    banned_mod = "import " + "psutil"
    banned_ps = "ps -o " + "lstart"
    if banned_mod in text or banned_ps in text:
        failures.append("ps used")
    stat = "9 (name with space) " + " ".join(["S"] + ["0"] * 18 + ["250"]) + "\n"
    got = start_time_from_stat(stat, "btime 1700000000\n", 100)
    expect = 1700000000 + 2.5
    if got is None or abs(got - expect) >= 1e-9 or abs(got - (got + 1.0)) < 1.0:
        failures.append("proc formula")
    with tempfile.TemporaryDirectory(prefix="repeat-pid-") as tmp:
        pid_path = Path(tmp) / "serve.pid"
        pid_path.write_text("424242\n", encoding="utf-8")
        if read_pid_record(pid_path) is not None:
            failures.append("bare pid authorized")
        pid_path.write_text('{"pid": 424242}\n', encoding="utf-8")
        if read_pid_record(pid_path) is not None:
            failures.append("missing create_time authorized")
    proc_match = 0
    if Path("/proc/self/stat").is_file():
        actual = linux_create_time(os.getpid())
        if actual is None:
            failures.append("proc start time missing")
        else:
            with tempfile.TemporaryDirectory(prefix="repeat-live-") as tmp:
                pid_path = Path(tmp) / "serve.pid"
                pid_path.write_text(
                    json.dumps({"pid": os.getpid(), "create_time": actual}),
                    encoding="utf-8",
                )
                if not identity_alive(pid_path)[0]:
                    failures.append("proc identity")
                else:
                    proc_match = 1
    first = red_check(PLANTED_VALUE, text="advisory")
    second = red_check(PLANTED_VALUE, text="I am stuck")
    second["assistant"] = "stuck"
    missing = red_check(None, text="advisory")
    other = red_check(1.0, text="advisory")
    if not fingerprints_match(first, second):
        failures.append("identical red")
    if fingerprints_match(first, other) or fingerprints_match(first, missing):
        failures.append("mismatch should not stop")
    if not fingerprints_match(missing, red_check(None)):
        failures.append("both missing values")
    if fingerprint(first) != fingerprint(second):
        failures.append("assistant text changed the fingerprint")
    with tempfile.TemporaryDirectory(prefix="repeat-self-") as tmp:
        proof = Path(tmp)
        stop = proof / "STOP"
        record = stop_decision(
            round_num=1,
            alive=True,
            later=False,
            elapsed=3,
            already=False,
            giveup=False,
            first=first,
            second=None,
        )
        if record != "record" or maybe_create(stop, record):
            failures.append("first red created a stop")
        if stop.exists():
            failures.append("first red left a stop file")
        create = stop_decision(
            round_num=2,
            alive=True,
            later=False,
            elapsed=3,
            already=False,
            giveup=False,
            first=first,
            second=second,
        )
        if create != "create" or not maybe_create(stop, create):
            failures.append("second identical red did not stop")
        hold = stop_decision(
            round_num=2,
            alive=True,
            later=False,
            elapsed=3,
            already=False,
            giveup=False,
            first=first,
            second=other,
        )
        hold_path = proof / "hold"
        if hold != "hold" or maybe_create(hold_path, hold):
            failures.append("different value stopped")
        missing_hold = stop_decision(
            round_num=2,
            alive=True,
            later=False,
            elapsed=3,
            already=False,
            giveup=False,
            first=first,
            second=missing,
        )
        if missing_hold != "hold":
            failures.append("one missing value stopped")
        both_missing = stop_decision(
            round_num=2,
            alive=True,
            later=False,
            elapsed=3,
            already=False,
            giveup=False,
            first=missing,
            second=red_check(None),
        )
        if both_missing != "create":
            failures.append("two missing values did not match")
        if source.is_file():
            copy = proof / "fail_source.py"
            copy.write_bytes(source.read_bytes())
            red_rc, red_value = run_check(copy)
            flipped = source.read_bytes().replace(STABLE.encode(), FLIP.encode(), 1)
            copy.write_bytes(flipped)
            _flip_rc, flip_value = run_check(copy)
            if red_rc != 1 or red_value != PLANTED_VALUE or flip_value != 1.0:
                failures.append("proof value")
            if source.read_bytes() != source_before:
                failures.append("proof modified the real source")
    if (root / "STOP").exists() or (source.is_file() and source.read_bytes() != source_before):
        failures.append("real files changed")
    if failures:
        print("SELF_CHECK_FAIL " + "; ".join(failures))
        return 1
    print("SELF_CHECK_OK")
    print(
        "first_red_recorded=1 real_stop_absent=1 "
        "would_stop_after_second_identical_red=1 first_red_did_not_create=1 "
        "both_missing_value_would_stop=1 missing_value_mismatch_no_stop=1 "
        "value_mismatch_no_stop=1 advisory_not_stop=1 assistant_ignored=1 "
        f"proof_deleted=1 ps_used=0 proc_formula=1 proc_create_time_match={proc_match}"
    )
    return 0


def round_check(info: dict) -> dict | None:
    checks = info.get("checks")
    if not isinstance(checks, list) or not checks:
        return None
    last = checks[-1]
    return last if isinstance(last, dict) else None


def watch() -> int:
    work = Path(os.environ["REPEAT_WORK"]).resolve()
    cfg = Path(os.environ["REPEAT_CFG"]).resolve()
    stop = Path(os.environ["REPEAT_STOP"]).resolve()
    log_path = Path(os.environ.get("REPEAT_LOG", str(cfg / "delay.log")))
    source = cfg / "fail_source.py"
    prompt = work / "prompts" / "program.md"
    toml_path = cfg / "agent-runner.toml"
    pid_file = work / "logs" / "serve.pid"
    for path in (source, prompt, toml_path):
        if not path.is_file():
            log(log_path, f"preflight missing={path.name}")
            return 1
    if inside(source, work) or inside(stop, work) or inside(toml_path, work):
        log(log_path, "preflight path_inside_repo=1")
        return 1
    if stop.exists():
        log(log_path, "preflight stop_exists=1")
        return 1
    data = load_toml(toml_path)
    command = data.get("agent", {}).get("command", [])
    runtime = data.get("runtime", {})
    if "agent" not in data or "--no-session" in command:
        log(log_path, "preflight agent=0")
        return 1
    if not same_dir(str(runtime.get("work_dir", "")), work):
        log(log_path, "preflight work_dir_mismatch=1")
        return 1
    if runtime.get("max_rounds") != 3 or not same_dir(str(runtime.get("stop_file", "")), stop):
        log(log_path, "preflight stop_mismatch=1")
        return 1
    text = prompt.read_text(encoding="utf-8")
    if prompt_smoke(text) or leaked(text, PROMPT_MARKERS + (str(source), str(stop))):
        log(log_path, "preflight prompt_leak=1")
        return 1
    source_text = source.read_text(encoding="utf-8")
    if STABLE not in source_text or FLIP in source_text:
        log(log_path, "preflight source=0")
        return 1
    boot_cmd = sha256_bytes(check_cmd_line(data).encode("utf-8"))
    boot_source = sha256_file(source)
    log(
        log_path,
        "phase=before_serve stop_file_absent=1 "
        f"source_sha256={boot_source} check_cmd_sha256={boot_cmd} ps_used=0",
    )
    acted: set[int] = set()
    latched: tuple[int, float] | None = None
    deadline = time.time() + DEADLINE_S
    while time.time() < deadline:
        record = read_pid_record(pid_file)
        if latched is None:
            if record is not None and identity_alive(pid_file)[0]:
                latched = record
                log(log_path, f"JSON_PID pid={record[0]} create_time={record[1]} ps_used=0")
            time.sleep(1)
            continue
        alive, pid, recorded = identity_alive(pid_file)
        if read_pid_record(pid_file) != latched or not alive:
            log(log_path, "serve_finished pid_gone=1")
            return 0
        events = load_events(work / "logs")
        rounds = index_rounds(events)
        for number, info in sorted(rounds.items()):
            if number in acted or "end" not in info:
                continue
            end_ts = parse_ts(info["end"].get("ts"))
            if end_ts is None:
                continue
            elapsed = time.time() - end_ts
            first = round_check(rounds.get(1, {}))
            second = round_check(info if number == 2 else rounds.get(2, {}))
            decision = stop_decision(
                round_num=number,
                alive=alive,
                later=round_started(events, number + 1),
                elapsed=elapsed,
                already=stop.exists(),
                giveup=giveup_present(events),
                first=first,
                second=second,
            )
            if decision == "wait":
                continue
            cmd_same = int(
                sha256_bytes(check_cmd_line(load_toml(toml_path)).encode("utf-8")) == boot_cmd
            )
            end_line = info["end"].get("_line")
            current = round_check(info)
            check_line = None if current is None else current.get("_line")
            fp = fingerprint(current)
            if decision == "create" and cmd_same == 1 and not stop.exists():
                log(
                    log_path,
                    "fingerprints_equal=1 both_red=1 "
                    f"fingerprint={fp} previous={fingerprint(first)} "
                    f"check_line={check_line} end_line={end_line} "
                    "compared_text=0 read_assistant=0 advisory_not_stop=1",
                )
                created = maybe_create(stop, decision)
                shown = "none" if recorded is None else repr(recorded)
                log(
                    log_path,
                    f"decision=create stop_created={int(created)} in_delay=1 "
                    f"path={stop} elapsed={elapsed:.1f} before_round3_start=1 "
                    f"pid={pid} create_time={shown} proc_match=1 ps_used=0 "
                    f"check_cmd_unchanged={cmd_same} advisory_not_stop=1",
                )
            else:
                created = 0
                log(
                    log_path,
                    f"round {number} decision={decision} stop_created={created} "
                    f"fingerprint={fp} check_line={check_line} end_line={end_line} "
                    f"pid={pid} alive={int(alive)} elapsed={elapsed:.1f} "
                    f"proc_match={int(alive)} ps_used=0 "
                    f"check_cmd_unchanged={cmd_same} advisory_not_stop=1 "
                    "read_assistant=0",
                )
            acted.add(number)
        time.sleep(1)
    log(log_path, "watcher_deadline=1")
    return 0


def main(argv: list[str]) -> int:
    if "--self-check" in argv:
        return self_check()
    return watch()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
