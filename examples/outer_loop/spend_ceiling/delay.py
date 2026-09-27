#!/usr/bin/env python3
"""Sum usage events between rounds and create stop_file at the ceiling.

Never starts serve. Never loops round. Never restarts on 78, 75, or 70.
Never edits the cold command. Never adds or removes a tools flag.
Never reads goal notes. Never records a money or token total.

The only serve-time write is creating the boot-configured stop file
while logs/serve.pid is a live JSON pid whose /proc start time matches
create_time within 1 second, inside the 60 second delay, and before the
next round_start. A missing usage event is not zero and does not enter
the addend list.

    SPEND_WORK=<repo> SPEND_CFG=<beside> SPEND_STOP=<STOP> python3 delay.py
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import time
import tomllib
from datetime import datetime
from pathlib import Path

DELAY_S = 60
WRITE_AFTER_S = 2.0
WRITE_BEFORE_S = 50.0
SETTLE_S = 10.0
DEADLINE_S = 4 * (1800 + 60)
MIN_PROMPT_BYTES = 500
PINNED = ["-p", "-na", "--mode", "json", "--model"]
EXAMPLE_COMMAND = ["PI", *PINNED, "PROVIDER/MODEL"]
STOP_NAME = "STOP"
MARKER = "MARKER"
STOP_BODY = b"ceiling\n"
FORBIDDEN_FIRST = frozenset("- \n\t\r")
GIVEUP = {
    "config_broken",
    "crash_loop",
    "mem_loop",
    "mem_loop_persistent",
    "stalled_no_progress",
}
# Any positive finite addend crosses this. It is not an observed spend.
CEILING = math.nextafter(0.0, 1.0)


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


def command_of(data: dict) -> list[str]:
    command = data.get("agent", {}).get("command", [])
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        return []
    return list(command)


def command_digest(command: list[str]) -> str:
    return sha256_bytes(json.dumps(command, ensure_ascii=False).encode("utf-8"))


def tools_count(command: list[str]) -> int:
    return sum(1 for item in command if item == "--tools")


def command_ok(command: list[str]) -> bool:
    """Shared argv, and no tools flag. The binary and model are replaced."""
    if len(command) != 7 or not command[0] or command[0].startswith("-"):
        return False
    if command[1:6] != PINNED:
        return False
    model = command[6]
    if not model or model.startswith("-"):
        return False
    if "--no-session" in command or "--tools" in command or tools_count(command) != 0:
        return False
    return True


def apply_gap(command: list[str]) -> list[str]:
    """The gap does not edit argv. It cannot add or remove a tools flag."""
    return list(command)


def as_int(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def positive_cost(row: dict) -> float | None:
    """A finite amount above zero, or None. None is not inserted as zero."""
    if "cost_usd" not in row:
        return None
    raw = row.get("cost_usd")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        return None
    return value


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
    for event in events:
        kind = event.get("event")
        number = as_int(event.get("round_num"))
        if kind == "round_start" and number is not None:
            rounds.setdefault(number, {})["start"] = event
        elif kind == "round_end" and number is not None:
            rounds.setdefault(number, {})["end"] = event
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


def usage_rows(events: list[dict], round_num: int) -> list[dict]:
    rows: list[dict] = []
    for event in events:
        if event.get("event") != "agent_usage_recorded":
            continue
        if as_int(event.get("round_num")) == round_num:
            rows.append(event)
    return rows


def addends_through(events: list[dict], through: int) -> list[float]:
    """Positive finite costs for ended rounds. A missing event adds nothing."""
    ended = sorted(
        number
        for number, info in index_rounds(events).items()
        if "end" in info and number <= through
    )
    addends: list[float] = []
    for number in ended:
        rows = usage_rows(events, number)
        if not rows:
            continue
        for row in rows:
            value = positive_cost(row)
            if value is None:
                continue
            addends.append(value)
    return addends


def crossed(addends: list[float], ceiling: float) -> bool:
    """True only when included addends meet the ceiling. Empty is not a cross."""
    if not addends or not math.isfinite(ceiling) or ceiling <= 0:
        return False
    folded = 0.0
    for item in addends:
        folded += item
    return folded >= ceiling


def missing_probe_advanced(events: list[dict], through: int) -> int:
    """1 if an extra ended round with no usage event changes the addends."""
    before = addends_through(events, through)
    probe_round = 9_000_001
    probe = list(events) + [{"event": "round_end", "round_num": probe_round}]
    after = addends_through(probe, probe_round)
    return int(after != before)


def stop_decision(
    *,
    alive: bool,
    later: bool,
    elapsed: float,
    giveup: bool,
    already: bool,
    crossed_now: bool,
) -> str:
    """Create the stop only inside the delay, and only when the ceiling is met."""
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
    if not crossed_now:
        return "hold"
    return "write"


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


def create_stop(path: Path, *, allow_real: bool, real: Path, work: Path) -> bool:
    """Create a stop file. Self-check passes allow_real false and a temp path."""
    try:
        resolved = path.resolve()
        real_resolved = real.resolve()
        work_resolved = work.resolve()
    except OSError:
        return False
    if inside(resolved, work_resolved):
        return False
    if resolved == real_resolved and not allow_real:
        return False
    if path.exists() or path.is_symlink():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".stop-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(STOP_BODY)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return path.is_file() and path.read_bytes() == STOP_BODY


def decision_line(
    *,
    decision: str,
    events_in_sum: int,
    missing_advanced: int,
    ceiling_met: int,
    stop_created: int,
    in_delay: int,
    elapsed: float,
    later: int,
    unchanged: int,
    added: int,
    removed: int,
    end_line: object,
    pid: int | None,
    recorded: float | None,
    proc_match: int,
) -> str:
    """Counts and flags only. No money figure and no token figure."""
    shown = "none" if recorded is None else repr(recorded)
    return (
        f"decision={decision} summed=1 cost_usd_events_in_sum={events_in_sum} "
        f"missing_usage_advanced={missing_advanced} missing_not_zero=1 "
        f"ceiling_crossed={ceiling_met} stop_created={stop_created} "
        f"in_delay={in_delay} elapsed={elapsed:.1f} "
        f"before_next_round_start={int(not later)} end_line={end_line} "
        f"command_unchanged={unchanged} tools_added={added} tools_removed={removed} "
        f"no_tools_flag=1 no_session_flag=0 pid={pid} create_time={shown} "
        f"proc_match={proc_match} ps_used=0 read_goal_notes=0 budget_knob=0 "
        "total_recorded=0"
    )


def prompt_errors(text: str, readme: str) -> list[str]:
    failures: list[str] = []
    if not text or text[0] in FORBIDDEN_FIRST:
        failures.append("prompt first")
    if len(text.encode("utf-8")) < MIN_PROMPT_BYTES:
        failures.append("prompt short")
    if MARKER not in text or MARKER not in readme:
        failures.append("marker")
    if "$" in text or "cost_usd" in text:
        failures.append("prompt amount")
    return failures


def toml_errors(
    data: dict,
    raw: str,
    work: str | Path,
    stop: str | Path,
    *,
    example: bool,
) -> list[str]:
    failures: list[str] = []
    if "agent" not in data:
        failures.append("missing [agent]")
    if "budget" in data or "[budget]" in raw:
        failures.append("budget knob")
    command = command_of(data)
    if example:
        if command != EXAMPLE_COMMAND:
            failures.append("cold command")
    elif not command_ok(command):
        failures.append("cold command")
    if "--no-session" in command or "--no-session" in raw:
        failures.append("no-session flag")
    if tools_count(command) != 0 or "--tools" in command or "--tools" in raw:
        failures.append("tools flag")
    runtime = data.get("runtime", {})
    if not isinstance(runtime, dict):
        return failures + ["runtime"]
    if runtime.get("max_rounds") != 3:
        failures.append("max_rounds")
    if "stop_file" not in runtime:
        failures.append("stop_file unset")
    elif example:
        if runtime.get("stop_file") != "STOP":
            failures.append("stop_file path")
    elif not same_dir(str(runtime.get("stop_file", "")), stop):
        failures.append("stop_file path")
    if runtime.get("restart_delay_s") != 60 or runtime.get("round_budget_s") != 1800:
        failures.append("delay")
    work_dir = str(runtime.get("work_dir", ""))
    if example:
        if work_dir != "WORK":
            failures.append("work_dir")
    elif not same_dir(work_dir, work):
        failures.append("work_dir")
    prompt = data.get("prompt", {})
    if not isinstance(prompt, dict) or "file" in prompt:
        failures.append("prompt form")
    files = prompt.get("files") if isinstance(prompt, dict) else None
    if files != ["prompts/program.md", "logs/lessons.md"]:
        failures.append("prompt files")
    vcs = data.get("vcs", {})
    if not isinstance(vcs, dict) or vcs.get("dirty_action") != "ignore" or "auto_commit" in vcs:
        failures.append("vcs")
    if "auto_commit" in raw:
        failures.append("auto_commit")
    goal = data.get("goal", {})
    if not isinstance(goal, dict) or goal.get("ledger") != "logs/lessons.md":
        failures.append("ledger")
    return failures


def source_errors(text: str) -> list[str]:
    failures: list[str] = []
    banned_mod = "import " + "psutil"
    banned_ps = "ps -o " + "lstart"
    banned_sub = "import " + "subprocess"
    if banned_mod in text or banned_ps in text or banned_sub in text:
        failures.append("ps used")
    banned_amount = "cost_" + "usd="
    banned_in = "input_" + "tokens"
    banned_out = "output_" + "tokens"
    if banned_amount in text or banned_in in text or banned_out in text:
        failures.append("total field")
    return failures


def self_check() -> int:
    root = Path(__file__).resolve().parent
    example_path = root / "agent-runner.toml.example"
    prompt_path = root / "prompts" / "program.md"
    marker_path = root / "marker.md"
    stop = root / STOP_NAME
    failures: list[str] = []
    text = Path(__file__).read_text(encoding="utf-8")
    failures.extend(source_errors(text))
    stat = "9 (name with space) " + " ".join(["S"] + ["0"] * 18 + ["250"]) + "\n"
    got = start_time_from_stat(stat, "btime 1700000000\n", 100)
    expect = 1700000000 + 2.5
    if got is None or abs(got - expect) >= 1e-9:
        failures.append("proc formula")
    with tempfile.TemporaryDirectory(prefix="spend-pid-") as tmp:
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
            with tempfile.TemporaryDirectory(prefix="spend-live-") as tmp:
                pid_path = Path(tmp) / "serve.pid"
                pid_path.write_text(
                    json.dumps({"pid": os.getpid(), "create_time": actual}),
                    encoding="utf-8",
                )
                if not identity_alive(pid_path)[0]:
                    failures.append("proc identity")
                else:
                    proc_match = 1
    if not example_path.is_file() or not prompt_path.is_file() or not marker_path.is_file():
        failures.append("fixture missing")
        print("SELF_CHECK_FAIL " + "; ".join(failures))
        return 1
    example_before = example_path.read_bytes()
    prompt_before = prompt_path.read_bytes()
    marker_before = marker_path.read_bytes()
    raw = example_before.decode("utf-8")
    data = load_toml(example_path)
    failures.extend(toml_errors(data, raw, "WORK", "STOP", example=True))
    failures.extend(prompt_errors(prompt_before.decode("utf-8"), marker_before.decode("utf-8")))
    if stop.exists():
        failures.append("real stop present")
    if apply_gap(EXAMPLE_COMMAND) != EXAMPLE_COMMAND or tools_count(apply_gap(EXAMPLE_COMMAND)):
        failures.append("gap edited command")
    ended = [{"event": "round_end", "round_num": 1, "ts": "2026-09-27T00:00:00.000Z"}]
    present = ended + [
        {"event": "agent_usage_recorded", "round_num": 1, "cost_usd": CEILING},
    ]
    missing = present + [{"event": "round_end", "round_num": 2}]
    null_row = ended + [{"event": "agent_usage_recorded", "round_num": 1, "cost_usd": None}]
    absent_field = ended + [{"event": "agent_usage_recorded", "round_num": 1}]
    zero_row = ended + [{"event": "agent_usage_recorded", "round_num": 1, "cost_usd": 0}]
    base = addends_through(present, 1)
    if addends_through(missing, 2) != base:
        failures.append("missing usage advanced")
    if missing_probe_advanced(present, 1) != 0:
        failures.append("missing probe advanced")
    absent = addends_through(ended, 1) or addends_through(null_row, 1)
    if absent or addends_through(absent_field, 1):
        failures.append("absent cost inserted")
    if addends_through(zero_row, 1):
        failures.append("zero invented")
    if not base or not crossed(base, CEILING) or crossed([], CEILING):
        failures.append("ceiling fold")
    below = stop_decision(
        alive=True,
        later=False,
        elapsed=3,
        giveup=False,
        already=False,
        crossed_now=False,
    )
    create = stop_decision(
        alive=True,
        later=False,
        elapsed=3,
        giveup=False,
        already=False,
        crossed_now=True,
    )
    dead = stop_decision(
        alive=False,
        later=False,
        elapsed=3,
        giveup=False,
        already=False,
        crossed_now=True,
    )
    late = stop_decision(
        alive=True,
        later=True,
        elapsed=3,
        giveup=False,
        already=False,
        crossed_now=True,
    )
    window = stop_decision(
        alive=True,
        later=False,
        elapsed=DELAY_S + 1,
        giveup=False,
        already=False,
        crossed_now=True,
    )
    decisions = (below, create, dead, late, window)
    if decisions != ("hold", "write", "dead", "late", "window"):
        failures.append("decision")
    proof_left = False
    with tempfile.TemporaryDirectory(prefix="spend-self-") as tmp:
        proof = Path(tmp)
        temp_stop = proof / STOP_NAME
        if create_stop(stop, allow_real=False, real=stop, work=root):
            failures.append("real stop created")
        if create != "write" or not create_stop(temp_stop, allow_real=False, real=stop, work=root):
            failures.append("temp stop missing")
        if not temp_stop.is_file() or temp_stop.read_bytes() != STOP_BODY:
            failures.append("temp stop body")
        body = temp_stop.read_text(encoding="utf-8")
        if "$" in body or any(ch.isdigit() for ch in body):
            failures.append("temp stop amount")
        if below == "write" or dead == "write" or late == "write" or window == "write":
            failures.append("non-ceiling decision was write")
        line = decision_line(
            decision="write",
            events_in_sum=len(base),
            missing_advanced=0,
            ceiling_met=1,
            stop_created=1,
            in_delay=1,
            elapsed=3,
            later=0,
            unchanged=1,
            added=0,
            removed=0,
            end_line=7,
            pid=None,
            recorded=None,
            proc_match=proc_match,
        )
        if "total_recorded=0" not in line or "$" in line or ("cost_" + "usd=") in line:
            failures.append("line recorded a total")
        if "missing_usage_advanced=0" not in line or "summed=1" not in line:
            failures.append("line missing the fold")
    if proof.exists():
        proof_left = True
    if (
        stop.exists()
        or example_path.read_bytes() != example_before
        or prompt_path.read_bytes() != prompt_before
        or marker_path.read_bytes() != marker_before
    ):
        failures.append("real files changed")
    if proof_left:
        failures.append("proof left")
    if failures:
        print("SELF_CHECK_FAIL " + "; ".join(failures))
        return 1
    print("SELF_CHECK_OK")
    print(
        "missing_usage_advanced=0 missing_not_in_addends=1 null_cost_not_in_addends=1 "
        "zero_not_in_addends=1 ceiling_stop_temp_only=1 real_stop_absent=1 "
        "real_stop_refused=1 proof_deleted=1 below_ceiling_no_stop=1 dead_no_stop=1 "
        "late_no_stop=1 next_round_no_stop=1 command_unchanged=1 tools_added=0 "
        "tools_removed=0 no_tools_flag=1 no_session_flag=0 max_rounds=3 "
        "stop_file_set=1 stop_file_absent=1 dirty_action_ignore=1 budget_knob=0 "
        "total_recorded=0 read_goal_notes=0 prompt_unchanged=1 toml_unchanged=1 "
        f"ps_used=0 proc_formula=1 proc_create_time_match={proc_match} "
        "bare_pid_rejected=1 missing_create_time_rejected=1"
    )
    return 0


def watch() -> int:
    work_raw = os.environ.get("SPEND_WORK", "")
    cfg_raw = os.environ.get("SPEND_CFG", "")
    stop_raw = os.environ.get("SPEND_STOP", "")
    if not work_raw or not cfg_raw or not stop_raw:
        print("preflight missing=env", file=sys.stderr)
        return 1
    work = Path(work_raw).resolve()
    cfg = Path(cfg_raw).resolve()
    stop = Path(stop_raw).resolve()
    log_path = Path(os.environ.get("SPEND_LOG", str(cfg / "delay.log")))
    toml_path = cfg / "agent-runner.toml"
    prompt_path = work / "prompts" / "program.md"
    readme_path = work / "README.md"
    pid_file = work / "logs" / "serve.pid"
    if not toml_path.is_file() or not prompt_path.is_file() or not readme_path.is_file():
        log(log_path, "preflight missing=fixture")
        return 1
    if inside(toml_path, work) or inside(Path(__file__).resolve(), work) or inside(stop, work):
        log(log_path, "preflight path_inside_repo=1")
        return 1
    if not stop.is_absolute():
        log(log_path, "preflight stop_relative=1")
        return 1
    raw = toml_path.read_text(encoding="utf-8")
    if "--no-session" in raw or "--tools" in raw or "[budget]" in raw:
        log(log_path, "preflight contract=0")
        return 1
    data = load_toml(toml_path)
    script = Path(__file__).read_text(encoding="utf-8")
    if toml_errors(data, raw, work, stop, example=False) or source_errors(script):
        log(log_path, "preflight contract=0")
        return 1
    prompt_text = prompt_path.read_text(encoding="utf-8")
    readme_text = readme_path.read_text(encoding="utf-8")
    if prompt_errors(prompt_text, readme_text):
        log(log_path, "preflight prompt=0")
        return 1
    if stop.exists():
        log(log_path, "preflight stop_present=1")
        return 1
    command = command_of(data)
    boot_digest = command_digest(command)
    log(
        log_path,
        "phase=before_serve no_tools_flag=1 no_session_flag=0 "
        f"command_sha256={boot_digest} max_rounds=3 stop_file_absent=1 "
        "dirty_action=ignore budget_knob=0 total_recorded=0 ps_used=0 "
        "read_goal_notes=0",
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
        pending = [
            number for number in sorted(rounds) if number not in acted and "end" in rounds[number]
        ]
        if not pending:
            time.sleep(1)
            continue
        number = pending[0]
        end_ts = parse_ts(rounds[number]["end"].get("ts"))
        if end_ts is None:
            time.sleep(1)
            continue
        elapsed = time.time() - end_ts
        later = round_started(events, number + 1)
        if elapsed < WRITE_AFTER_S and not later:
            time.sleep(1)
            continue
        if (
            not usage_rows(events, number)
            and elapsed < SETTLE_S
            and not later
            and not giveup_present(events)
            and elapsed <= WRITE_BEFORE_S
        ):
            time.sleep(1)
            continue
        addends = addends_through(events, number)
        probe = missing_probe_advanced(events, number)
        is_crossed = crossed(addends, CEILING)
        decision = stop_decision(
            alive=alive,
            later=later,
            elapsed=elapsed,
            giveup=giveup_present(events),
            already=stop.exists(),
            crossed_now=is_crossed,
        )
        if decision == "wait":
            time.sleep(1)
            continue
        written = 0
        if decision == "write":
            alive_now, pid_now, recorded_now = identity_alive(pid_file)
            if not alive_now or read_pid_record(pid_file) != latched:
                log(
                    log_path,
                    "decision=dead stop_created=0 ps_used=0 read_goal_notes=0 total_recorded=0",
                )
                time.sleep(1)
                continue
            pid, recorded = pid_now, recorded_now
            if create_stop(stop, allow_real=True, real=stop, work=work):
                written = 1
        after = command_of(load_toml(toml_path))
        added = tools_count(after)
        removed = tools_count(command) - tools_count(after)
        if removed < 0:
            removed = 0
        unchanged = int(after == command and command_ok(after) and added == 0)
        in_window = WRITE_AFTER_S <= elapsed <= min(WRITE_BEFORE_S, DELAY_S)
        in_delay = int(bool(alive) and in_window and not later)
        end_line = rounds[number]["end"].get("_line")
        log(
            log_path,
            decision_line(
                decision=decision,
                events_in_sum=len(addends),
                missing_advanced=probe,
                ceiling_met=int(is_crossed),
                stop_created=written,
                in_delay=in_delay,
                elapsed=elapsed,
                later=int(later),
                unchanged=unchanged,
                added=added,
                removed=removed,
                end_line=end_line,
                pid=pid,
                recorded=recorded,
                proc_match=int(alive),
            ),
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
