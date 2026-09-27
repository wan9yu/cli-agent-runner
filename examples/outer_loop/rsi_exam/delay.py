#!/usr/bin/env python3
"""Score sealed data once after the last round. Not a second supervisor.

Never starts serve. Never loops agent-runner round. Never restarts on
78, 75, or 70. Never creates stop_file. Never copies the holdout scorer
into a prompt file or the ledger.

The only serve-time mutation is one local exam after round 2 round_end,
while logs/serve.pid is a live JSON pid whose /proc start time matches
create_time within 1 second, inside the 60 second delay, and before a
later round_start. Round 1 does not run the exam.

    RSI_EXAM_WORK=<repo> RSI_EXAM_CFG=<beside> python3 delay.py
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
DEADLINE_S = 4 * (1800 + 60)
MIN_PROMPT_BYTES = 500
TOKEN = "SEALED_ASSERT_7c1e9a"
PLANTED = b"PLANTED\n"
FORBIDDEN_FIRST = frozenset("- \n\t\r")
EXAM_FORBIDDEN = (
    "subprocess",
    "urllib",
    "socket",
    "requests",
    "openai",
    "grok",
    "http.client",
    "agent-runner",
)
GIVEUP = {
    "config_broken",
    "crash_loop",
    "mem_loop",
    "mem_loop_persistent",
    "stalled_no_progress",
}


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


def as_int(raw: object) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


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
        number = event.get("round_num")
        if (
            event.get("event") == "round_start"
            and isinstance(number, int)
            and not isinstance(number, bool)
            and number == round_num
        ):
            return True
    return False


def giveup_present(events: list[dict]) -> bool:
    return any(event.get("event") in GIVEUP for event in events)


def atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".score-", suffix=".tmp", dir=str(path.parent))
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


def exam_source_ok(text: str) -> bool:
    return not any(token in text for token in EXAM_FORBIDDEN)


def load_module(path: Path):
    name = f"holdout_{path.stem}_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_visible_file(path: Path) -> tuple[int, float | None]:
    module = load_module(path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = module.main()
    if isinstance(rc, bool) or not isinstance(rc, int):
        rc = 1
    return rc, last_float(buf.getvalue())


def run_exam(exam: Path, visible: Path) -> tuple[float | None, str]:
    """Load the exam file in-process. It is a local scorer, not a model."""
    module = load_module(exam)
    value = float(module.score(visible))
    if not math.isfinite(value):
        return None, ""
    text = f"{value:.1f}\n"
    return value, text


def exam_decision(
    *,
    round_num: int,
    alive: bool,
    later: bool,
    elapsed: float,
    already: bool,
    giveup: bool,
) -> str:
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
    if round_num != 2:
        return "idle"
    return "write"


def leak_markers(cfg: Path) -> tuple[str, ...]:
    sealed = cfg / "sealed.txt"
    rows: list[str] = []
    try:
        text = sealed.read_text(encoding="utf-8")
        rows = [line.strip() for line in text.splitlines() if line.strip()]
    except OSError:
        rows = []
    return (
        str(cfg / "exam.py"),
        str(cfg / "sealed.txt"),
        str(cfg / "score.txt"),
        "exam.py",
        "sealed.txt",
        "score.txt",
        "delay.py",
        "agent-runner.toml",
        TOKEN,
        *rows[1:],
    )


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


def bundled_prompt_errors(root: Path) -> list[str]:
    prompt = root / "prompts" / "program.md"
    if not prompt.is_file():
        return []
    text = prompt.read_text(encoding="utf-8")
    failures: list[str] = []
    if prompt_smoke(text):
        failures.append("bundled prompt smoke")
    found = leaked(text, leak_markers(root))
    if found:
        failures.append("bundled prompt markers")
    visible = root / "visible.py"
    if visible.is_file():
        source = visible.read_text(encoding="utf-8")
        if TOKEN in source or "exam.py" in source:
            failures.append("visible source contains holdout marker")
    example = root / "agent-runner.toml.example"
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
        if data.get("runtime", {}).get("max_rounds") != 2:
            failures.append("example max_rounds")
        if "stop_file" in data.get("runtime", {}):
            failures.append("example stop_file")
        if data.get("vcs", {}).get("dirty_action") != "ignore":
            failures.append("example dirty_action")
        if "auto_commit" in joined:
            failures.append("example auto_commit")
        if TOKEN in joined or "exam.py" in joined:
            failures.append("example contains holdout")
        files = data.get("prompt", {}).get("files")
        if files != ["prompts/program.md", "logs/lessons.md"]:
            failures.append("example prompt files")
        if data.get("goal", {}).get("checks", [{}])[0].get("cmd") != ["python3", "visible.py"]:
            failures.append("example check")
    return failures


def prove_visible(source: Path, proof: Path) -> tuple[int, float, int, float]:
    original = source.read_bytes()
    red = proof / "visible.py"
    red.write_bytes(original)
    red_rc, red_value = run_visible_file(red)
    green_bytes = original.replace(
        b"return 0  # planted",
        b"return left + right  # planted",
        1,
    )
    if green_bytes == original:
        raise SystemExit("proof patch did not match the planted return")
    red.write_bytes(green_bytes)
    green_rc, green_value = run_visible_file(red)
    if source.read_bytes() != original:
        raise SystemExit("proof copy modified the real visible source")
    if red_value is None or green_value is None:
        raise SystemExit("proof copy did not print a finite float")
    return red_rc, red_value, green_rc, green_value


def self_check() -> int:
    root = Path(__file__).resolve().parent
    score = root / "score.txt"
    score_before = score.read_bytes() if score.is_file() else None
    failures = bundled_prompt_errors(root)
    source = Path(__file__).read_text(encoding="utf-8")
    banned_mod = "import " + "psutil"
    banned_ps = "ps -o " + "lstart"
    if banned_mod in source or banned_ps in source:
        failures.append("ps used")
    stat = "9 (name with space) " + " ".join(["S"] + ["0"] * 18 + ["250"]) + "\n"
    got = start_time_from_stat(stat, "btime 1700000000\n", 100)
    expect = 1700000000 + 2.5
    if got is None or abs(got - expect) >= 1e-9:
        failures.append("proc formula")
    if got is not None and abs(got - (got + 1.0)) < 1.0:
        failures.append("one second window")
    bare = read_pid_record_text("424242\n")
    missing = read_pid_record_text('{"pid": 424242}\n')
    good = read_pid_record_text('{"pid": 424242, "create_time": 1700000000.5}\n')
    if bare is not None or missing is not None or good != (424242, 1700000000.5):
        failures.append("pid record")
    proc_match = 0
    if Path("/proc/self/stat").is_file():
        actual = linux_create_time(os.getpid())
        if actual is None:
            failures.append("proc start time missing")
        else:
            with tempfile.TemporaryDirectory(prefix="rsi-exam-pid-") as tmp:
                pid_path = Path(tmp) / "serve.pid"
                pid_path.write_text(
                    json.dumps({"pid": os.getpid(), "create_time": actual}),
                    encoding="utf-8",
                )
                alive, _pid, _recorded = identity_alive(pid_path)
                if not alive:
                    failures.append("proc identity")
                else:
                    proc_match = 1
                pid_path.write_text(
                    json.dumps({"pid": os.getpid(), "create_time": actual + 5}),
                    encoding="utf-8",
                )
                if identity_alive(pid_path)[0]:
                    failures.append("stale create_time authorized")
    exam = root / "exam.py"
    visible = root / "visible.py"
    if exam.is_file() and not exam_source_ok(exam.read_text(encoding="utf-8")):
        failures.append("exam source")
    with tempfile.TemporaryDirectory(prefix="rsi-exam-self-") as tmp:
        proof = Path(tmp)
        if visible.is_file() and exam.is_file():
            red_rc, red_value, green_rc, green_value = prove_visible(visible, proof)
            if red_rc == 0 or red_value != 0.0 or green_rc != 0 or green_value != 1.0:
                failures.append("visible proof")
            planted = proof / "score.txt"
            planted.write_bytes(PLANTED)
            before = sha256_file(planted)
            idle = exam_decision(
                round_num=1,
                alive=True,
                later=False,
                elapsed=3,
                already=False,
                giveup=False,
            )
            if idle != "idle" or sha256_file(planted) != before:
                failures.append("round 1 ran the exam")
            late = exam_decision(
                round_num=2,
                alive=True,
                later=True,
                elapsed=3,
                already=False,
                giveup=False,
            )
            if late != "late":
                failures.append("late write")
            value, stdout = run_exam(exam, proof / "visible.py")
            if value != 1.0 or not stdout.strip():
                failures.append("exam value")
            if "grok" in stdout or "openai" in stdout:
                failures.append("exam called a model")
            body = stdout if stdout.endswith("\n") else stdout + "\n"
            atomic_replace(planted, body.encode("utf-8"))
            after = sha256_file(planted)
            if before == after or planted.read_bytes() == PLANTED:
                failures.append("score did not change")
            second = exam_decision(
                round_num=2,
                alive=True,
                later=False,
                elapsed=3,
                already=True,
                giveup=False,
            )
            held = planted.read_bytes()
            if second != "already" or planted.read_bytes() != held:
                failures.append("exam ran twice")
        else:
            failures.append("bundled exam missing")
    proof_gone = not (root / "proof-copy").exists()
    score_after = score.read_bytes() if score.is_file() else None
    if score_after != score_before:
        failures.append("real score changed")
    if failures or not proof_gone:
        print("SELF_CHECK_FAIL " + "; ".join(failures or ["proof remains"]))
        return 1
    print("SELF_CHECK_OK")
    print(
        "not_llm=1 score_unchanged=1 proof_red=1 proof_green=1 proof_deleted=1 "
        f"ps_used=0 proc_formula=1 proc_create_time_match={proc_match}"
    )
    return 0


def read_pid_record_text(raw: str) -> tuple[int, float] | None:
    with tempfile.TemporaryDirectory(prefix="rsi-exam-pidtext-") as tmp:
        path = Path(tmp) / "serve.pid"
        path.write_text(raw, encoding="utf-8")
        return read_pid_record(path)


def watch() -> int:
    work = Path(os.environ["RSI_EXAM_WORK"]).resolve()
    cfg = Path(os.environ["RSI_EXAM_CFG"]).resolve()
    log_path = Path(os.environ.get("RSI_EXAM_LOG", str(cfg / "delay.log")))
    visible = work / "visible.py"
    prompt = work / "prompts" / "program.md"
    ledger = work / "logs" / "lessons.md"
    exam = cfg / "exam.py"
    score = cfg / "score.txt"
    toml_path = cfg / "agent-runner.toml"
    pid_file = work / "logs" / "serve.pid"
    for path in (visible, prompt, exam, cfg / "sealed.txt", score, toml_path):
        if not path.is_file():
            log(log_path, f"preflight missing={path.name}")
            return 1
    if not exam_source_ok(exam.read_text(encoding="utf-8")):
        log(log_path, "preflight exam_not_local=1")
        return 1
    data = load_toml(toml_path)
    command = data.get("agent", {}).get("command", [])
    if "agent" not in data or "--no-session" in command:
        log(log_path, "preflight agent=0")
        return 1
    if not same_dir(str(data.get("runtime", {}).get("work_dir", "")), work):
        log(log_path, "preflight work_dir_mismatch=1")
        return 1
    if "stop_file" in data.get("runtime", {}) or data.get("runtime", {}).get("max_rounds") != 2:
        log(log_path, "preflight rounds=0")
        return 1
    smoke = prompt_smoke(prompt.read_text(encoding="utf-8"))
    found = leaked(prompt.read_text(encoding="utf-8"), leak_markers(cfg))
    if smoke or found:
        log(log_path, "preflight prompt_leak=1")
        return 1
    boot_visible = sha256_file(visible)
    boot_score = sha256_file(score)
    boot_cmd = sha256_bytes(check_cmd_line(data).encode("utf-8"))
    log(
        log_path,
        "phase=before_serve "
        f"visible_sha256={boot_visible} score_sha256={boot_score} "
        f"check_cmd_sha256={boot_cmd} exam_ran=0 ps_used=0",
    )
    acted: set[int] = set()
    already: set[str] = set()
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
            decision = exam_decision(
                round_num=number,
                alive=alive,
                later=round_started(events, number + 1),
                elapsed=elapsed,
                already="exam" in already,
                giveup=giveup_present(events),
            )
            if decision == "wait":
                continue
            cmd_hash = sha256_bytes(check_cmd_line(load_toml(toml_path)).encode("utf-8"))
            cmd_same = int(cmd_hash == boot_cmd)
            visible_hash = sha256_file(visible)
            end_line = info["end"].get("_line")
            if decision != "write":
                same = int(sha256_file(score) == boot_score)
                changed = int(visible_hash != boot_visible)
                log(
                    log_path,
                    f"round {number} decision={decision} exam_ran=0 "
                    f"score_unchanged={same} visible_sha256={visible_hash} "
                    f"visible_changed={changed} check_cmd_unchanged={cmd_same} "
                    f"end_line={end_line} pid={pid} alive={int(alive)} "
                    f"elapsed={elapsed:.1f} proc_match={int(alive)} ps_used=0",
                )
                acted.add(number)
                continue
            if sha256_file(score) != boot_score or cmd_same != 1:
                log(log_path, f"round {number} decision=refuse exam_ran=0 end_line={end_line}")
                acted.add(number)
                continue
            program_before = prompt.read_bytes()
            ledger_before = ledger.read_bytes() if ledger.exists() else None
            value, stdout = run_exam(exam, visible)
            events = load_events(work / "logs")
            alive, pid, recorded = identity_alive(pid_file)
            elapsed = time.time() - end_ts
            if (
                not alive
                or round_started(events, number + 1)
                or elapsed > DELAY_S
                or value is None
                or not stdout.strip()
            ):
                log(
                    log_path,
                    f"round {number} decision=closed exam_ran=0 reason=gate_after_exam "
                    f"pid={pid} alive={int(alive)} elapsed={elapsed:.1f}",
                )
                acted.add(number)
                continue
            body = stdout if stdout.endswith("\n") else stdout + "\n"
            score_before = sha256_file(score)
            atomic_replace(score, body.encode("utf-8"))
            already.add("exam")
            score_after = sha256_file(score)
            program_same = int(prompt.read_bytes() == program_before)
            if ledger_before is None:
                ledger_same = int(not ledger.exists())
            else:
                ledger_same = int(ledger.exists() and ledger.read_bytes() == ledger_before)
            shown = "none" if recorded is None else repr(recorded)
            log(
                log_path,
                f"round {number} decision=write exam_ran=1 not_llm=1 once=1 in_delay=1 "
                f"score_hash_before={score_before} score_hash_after={score_after} "
                f"hashes_differ={int(score_before != score_after)} exam_value={value} "
                f"visible_sha256={visible_hash} "
                f"visible_changed={int(visible_hash != boot_visible)} "
                f"check_cmd_unchanged={cmd_same} "
                f"prompt_unchanged_by_writer={program_same} "
                f"ledger_untouched={ledger_same} end_line={end_line} "
                f"pid={pid} create_time={shown} elapsed={elapsed:.1f} "
                "proc_match=1 ps_used=0 before_round3_start=1",
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
