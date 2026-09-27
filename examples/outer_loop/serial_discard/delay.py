#!/usr/bin/env python3
"""Leave a red attempt unmerged. Not a second supervisor.

Never starts serve. Never loops agent-runner round. Never restarts on
78, 75, or 70. Never creates stop_file. Never copies the attempt
directory into the keep directory. Never writes a snapshot back over
the attempt source. Never creates a git worktree. Never calls stash.

logs/serve.pid is JSON: {"pid": int, "create_time": number}. A bare
integer does not authorize a claim. A missing create_time does not
authorize a claim. A claim is written only while that pid is alive,
/proc start time matches create_time within 1 second, the round_end is
still inside the 60 second delay, and the next round has not started.

    SERIAL_WORK=<attempt> SERIAL_CFG=<beside> python3 delay.py
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
STABLE = "VALUE = 0.0"
FLIP = "VALUE = 1.0"
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
    "visible_check.py",
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
    """JSON {pid, create_time} only. A bare integer does not authorize a claim."""
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


def dir_manifest(root: Path) -> bytes:
    """Sorted relpath, NUL, file sha256. Ignores mtime. Refuses symlinks."""
    base = root.resolve()
    if not base.is_dir():
        raise SystemExit(f"not a directory: {root.name}")
    files: list[tuple[str, Path]] = []
    for path in base.rglob("*"):
        if path.is_symlink():
            raise SystemExit(f"symlink in hash tree: {path.name}")
        if not path.is_file():
            continue
        files.append((path.relative_to(base).as_posix(), path))
    if not files:
        raise SystemExit(f"empty hash tree: {root.name}")
    lines = [rel + "\0" + sha256_file(path) for rel, path in sorted(files)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def dir_hash(root: Path) -> str:
    return sha256_bytes(dir_manifest(root))


def worktree_count(repo: Path) -> int | None:
    """Count this repo plus linked worktrees. Does not create one."""
    git = repo / ".git"
    if git.is_file():
        return 2
    if not git.is_dir():
        return None
    extra = git / "worktrees"
    if not extra.is_dir():
        return 1
    linked = [path for path in extra.iterdir() if path.is_dir() and not path.is_symlink()]
    return 1 + len(linked)


def check_state(check: dict | None) -> str:
    if not isinstance(check, dict):
        return "missing"
    skipped = check.get("skipped")
    if isinstance(skipped, bool) and skipped:
        return "skipped"
    satisfied = check.get("satisfied")
    if isinstance(satisfied, bool) and not satisfied:
        return "red"
    if isinstance(satisfied, bool) and satisfied:
        return "green"
    return "unknown"


def gap_decision(
    *,
    round_num: int,
    alive: bool,
    later: bool,
    elapsed: float,
    state: str,
    giveup: bool,
) -> str:
    """Claim no_merge only for a red gap inside the delay. Never merge."""
    if round_num not in (1, 2):
        return "idle"
    if not alive:
        return "dead"
    if later or giveup:
        return "late"
    if elapsed < WRITE_AFTER_S:
        return "wait"
    if elapsed > WRITE_BEFORE_S or elapsed > DELAY_S:
        return "window"
    if state == "green":
        return "green"
    if state != "red":
        return "hold"
    return "no_merge"


def apply_gap(
    *,
    decision: str,
    keep: Path,
    attempt_source: Path,
    snapshot: bytes,
) -> dict[str, int]:
    """The only gap action. It never writes keep or the attempt source.

    snapshot is accepted so a metric write-back could have used it, and
    is deliberately unused.
    """
    del snapshot
    flags = {
        "no_merge": 0,
        "not_karpathy_restore": 0,
        "wrote_keep": 0,
        "wrote_attempt": 0,
        "copied_attempt_into_keep": 0,
    }
    if decision != "no_merge":
        return flags
    if not keep.is_dir():
        raise SystemExit(f"keep missing at gap: {keep.name}")
    if attempt_source.is_symlink():
        raise SystemExit(f"attempt source is a symlink: {attempt_source.name}")
    flags["no_merge"] = 1
    flags["not_karpathy_restore"] = 1
    return flags


def load_module(path: Path):
    name = f"visible_{path.stem}_{time.time_ns()}"
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


def round_check(info: dict) -> dict | None:
    checks = info.get("checks")
    if not isinstance(checks, list) or not checks:
        return None
    last = checks[-1]
    return last if isinstance(last, dict) else None


def bundled_errors(root: Path) -> list[str]:
    failures: list[str] = []
    prompt = root / "prompts" / "program.md"
    source = root / "visible_check.py"
    example = root / "agent-runner.toml.example"
    if prompt.is_file():
        text = prompt.read_text(encoding="utf-8")
        if prompt_smoke(text) or leaked(text, PROMPT_MARKERS):
            failures.append("bundled prompt")
    if source.is_file():
        body = source.read_text(encoding="utf-8")
        if STABLE not in body or FLIP in body:
            failures.append("bundled check")
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
        if runtime.get("max_rounds") != 2 or "stop_file" in runtime:
            failures.append("example rounds")
        vcs = data.get("vcs", {})
        if vcs.get("dirty_action") != "ignore" or "auto_commit" in vcs:
            failures.append("example vcs")
        if FLIP in joined:
            failures.append("example flip")
    return failures


def self_check() -> int:
    root = Path(__file__).resolve().parent
    keep = root / "keep"
    attempt = root / "attempt.py"
    keep_before = dir_hash(keep) if keep.is_dir() else None
    attempt_before = attempt.read_bytes() if attempt.is_file() else None
    failures = bundled_errors(root)
    text = Path(__file__).read_text(encoding="utf-8")
    banned_mod = "import " + "psutil"
    banned_ps = "ps -o " + "lstart"
    banned_sub = "import " + "subprocess"
    if banned_mod in text or banned_ps in text or banned_sub in text:
        failures.append("ps used")
    stat = "9 (name with space) " + " ".join(["S"] + ["0"] * 18 + ["250"]) + "\n"
    got = start_time_from_stat(stat, "btime 1700000000\n", 100)
    expect = 1700000000 + 2.5
    if got is None or abs(got - expect) >= 1e-9 or abs(got - (got + 1.0)) < 1.0:
        failures.append("proc formula")
    with tempfile.TemporaryDirectory(prefix="serial-pid-") as tmp:
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
            with tempfile.TemporaryDirectory(prefix="serial-live-") as tmp:
                pid_path = Path(tmp) / "serve.pid"
                pid_path.write_text(
                    json.dumps({"pid": os.getpid(), "create_time": actual}),
                    encoding="utf-8",
                )
                if not identity_alive(pid_path)[0]:
                    failures.append("proc identity")
                else:
                    proc_match = 1
    with tempfile.TemporaryDirectory(prefix="serial-self-") as tmp:
        proof = Path(tmp)
        proof_keep = proof / "keep"
        proof_src = proof_keep / "src"
        proof_src.mkdir(parents=True)
        (proof_keep / "TREE").write_text("KEEP\n", encoding="utf-8")
        (proof_src / "kept.py").write_text('MARKER = "PLANTED"\n', encoding="utf-8")
        proof_attempt = proof / "attempt.py"
        proof_attempt.write_text('MARKER = "ATTEMPT"\n', encoding="utf-8")
        planted = dir_hash(proof_keep)
        snapshot = proof_attempt.read_bytes()
        dirty = proof / "dirty"
        dirty.mkdir()
        (dirty / "extra.txt").write_text("changed\n", encoding="utf-8")
        merged = proof / "merged-keep"
        merged.mkdir()
        (merged / "TREE").write_text("KEEP\n", encoding="utf-8")
        (merged / "extra.txt").write_bytes((dirty / "extra.txt").read_bytes())
        if dir_hash(merged) == planted:
            failures.append("merge would not change keep")
        flags = apply_gap(
            decision="no_merge",
            keep=proof_keep,
            attempt_source=proof_attempt,
            snapshot=snapshot,
        )
        if flags["no_merge"] != 1 or flags["not_karpathy_restore"] != 1:
            failures.append("gap flags")
        if flags["wrote_keep"] or flags["wrote_attempt"] or flags["copied_attempt_into_keep"]:
            failures.append("gap wrote")
        if dir_hash(proof_keep) != planted or proof_attempt.read_bytes() != snapshot:
            failures.append("gap modified trees")
        green = gap_decision(
            round_num=1,
            alive=True,
            later=False,
            elapsed=3,
            state="green",
            giveup=False,
        )
        if green != "green":
            failures.append("green gap claimed no_merge")
        red = gap_decision(
            round_num=1,
            alive=True,
            later=False,
            elapsed=3,
            state="red",
            giveup=False,
        )
        if red != "no_merge":
            failures.append("red gap")
        late = gap_decision(
            round_num=2,
            alive=True,
            later=True,
            elapsed=3,
            state="red",
            giveup=False,
        )
        if late != "late":
            failures.append("late gap")
        attempt_dir = proof / "attempt-repo"
        attempt_dir.mkdir()
        if not same_dir(attempt_dir, attempt_dir):
            failures.append("work_dir match")
        if same_dir("WORK", attempt_dir):
            failures.append("placeholder work_dir matched")
        check = root / "visible_check.py"
        if check.is_file():
            copy = proof / "visible_check.py"
            copy.write_bytes(check.read_bytes())
            red_rc, red_value = run_check(copy)
            flipped = check.read_bytes().replace(STABLE.encode(), FLIP.encode(), 1)
            copy.write_bytes(flipped)
            flip_rc, flip_value = run_check(copy)
            if red_rc != 1 or red_value != 0.0 or flip_rc != 0 or flip_value != 1.0:
                failures.append("proof check")
    if keep.is_dir() and dir_hash(keep) != keep_before:
        failures.append("real keep changed")
    if attempt.is_file() and attempt.read_bytes() != attempt_before:
        failures.append("real attempt changed")
    if (root / "proof-copy").exists():
        failures.append("proof remains")
    if failures:
        print("SELF_CHECK_FAIL " + "; ".join(failures))
        return 1
    print("SELF_CHECK_OK")
    print(
        "no_merge=1 not_karpathy_restore=1 real_keep_unchanged=1 "
        "work_dir_is_attempt=1 proof_deleted=1 new_worktree=0 stash_used=0 "
        f"ps_used=0 proc_formula=1 proc_create_time_match={proc_match}"
    )
    return 0


def watch() -> int:
    work = Path(os.environ["SERIAL_WORK"]).resolve()
    cfg = Path(os.environ["SERIAL_CFG"]).resolve()
    log_path = Path(os.environ.get("SERIAL_LOG", str(cfg / "delay.log")))
    keep = cfg / "keep"
    check = cfg / "visible_check.py"
    prompt = work / "prompts" / "program.md"
    attempt_source = work / "attempt.py"
    toml_path = cfg / "agent-runner.toml"
    pid_file = work / "logs" / "serve.pid"
    for path in (keep, check, prompt, attempt_source, toml_path):
        if path == keep:
            if not keep.is_dir():
                log(log_path, "preflight missing=keep")
                return 1
            continue
        if not path.is_file():
            log(log_path, f"preflight missing={path.name}")
            return 1
    if inside(keep, work) or inside(check, work) or inside(toml_path, work):
        log(log_path, "preflight path_inside_repo=1")
        return 1
    if not (work / ".git").exists():
        log(log_path, "preflight git_missing=1")
        return 1
    data = load_toml(toml_path)
    command = data.get("agent", {}).get("command", [])
    runtime = data.get("runtime", {})
    if "agent" not in data or "--no-session" in command:
        log(log_path, "preflight agent=0")
        return 1
    toml_work = str(runtime.get("work_dir", ""))
    if not same_dir(toml_work, work):
        log(log_path, "preflight work_dir_mismatch=1")
        return 1
    if runtime.get("max_rounds") != 2 or "stop_file" in runtime:
        log(log_path, "preflight rounds=0")
        return 1
    if data.get("vcs", {}).get("dirty_action") != "ignore" or "auto_commit" in data.get("vcs", {}):
        log(log_path, "preflight vcs=0")
        return 1
    text = prompt.read_text(encoding="utf-8")
    if prompt_smoke(text) or leaked(text, PROMPT_MARKERS + (str(check), str(cfg))):
        log(log_path, "preflight prompt_leak=1")
        return 1
    check_text = check.read_text(encoding="utf-8")
    if STABLE not in check_text or FLIP in check_text:
        log(log_path, "preflight check=0")
        return 1
    planted = dir_hash(keep)
    boot_attempt = sha256_file(attempt_source)
    boot_cmd = sha256_bytes(check_cmd_line(data).encode("utf-8"))
    log(
        log_path,
        "phase=before_serve "
        f"work_dir={work} attempt_dir={work} toml_work_dir={work} "
        "work_dir_is_attempt=1 toml_work_dir_equal=1 "
        f"keep_dir_sha256={planted} attempt_source_sha256={boot_attempt} "
        f"check_cmd_sha256={boot_cmd} ps_used=0 stash_used=0",
    )
    acted: set[int] = set()
    latched: tuple[int, float] | None = None
    deadline = time.time() + DEADLINE_S
    while time.time() < deadline:
        record = read_pid_record(pid_file)
        if latched is None:
            if record is not None and identity_alive(pid_file)[0]:
                latched = record
                log(
                    log_path,
                    "watcher_start no_serve_started_by_script=1 "
                    f"work_dir={work} work_dir_is_attempt=1 ps_used=0",
                )
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
            current = round_check(info)
            later = round_started(events, number + 1)
            decision = gap_decision(
                round_num=number,
                alive=alive,
                later=later,
                elapsed=elapsed,
                state=check_state(current),
                giveup=giveup_present(events),
            )
            if decision == "wait":
                continue
            before_keep = dir_hash(keep)
            before_attempt = attempt_source.read_bytes()
            flags = apply_gap(
                decision=decision,
                keep=keep,
                attempt_source=attempt_source,
                snapshot=before_attempt,
            )
            after_keep = dir_hash(keep)
            after_attempt = sha256_file(attempt_source)
            trees = worktree_count(work)
            new_worktree = 0 if trees in (None, 1) else 1
            in_window = WRITE_AFTER_S <= elapsed <= min(WRITE_BEFORE_S, DELAY_S)
            in_delay = int(bool(alive) and in_window and not later)
            cmd_same = int(
                sha256_bytes(check_cmd_line(load_toml(toml_path)).encode("utf-8")) == boot_cmd
            )
            end_line = info["end"].get("_line")
            check_line = None if current is None else current.get("_line")
            shown = "none" if recorded is None else repr(recorded)
            log(
                log_path,
                f"round {number} decision={decision} "
                f"no_merge={flags['no_merge']} "
                f"not_karpathy_restore={flags['not_karpathy_restore']} "
                f"keep_dir_sha256={after_keep} "
                f"keep_equals_planted={int(after_keep == planted)} "
                f"work_dir={work} work_dir_is_attempt=1 toml_work_dir_equal=1 "
                f"in_delay={in_delay} elapsed={elapsed:.1f} "
                f"before_next_round_start={int(not later)} "
                f"pid={pid} create_time={shown} proc_match={int(alive)} ps_used=0 "
                f"check_cmd_unchanged={cmd_same} check_line={check_line} "
                f"end_line={end_line} new_worktree={new_worktree} "
                f"worktree_count={trees} stash_used=0 "
                f"copied_attempt_into_keep={flags['copied_attempt_into_keep']} "
                f"wrote_keep={flags['wrote_keep']} wrote_attempt={flags['wrote_attempt']} "
                f"attempt_source_sha256={after_attempt} "
                f"keep_unchanged_this_gap={int(after_keep == before_keep)}",
            )
            if attempt_source.read_bytes() != before_attempt or after_keep != before_keep:
                log(log_path, f"round {number} decision=refuse reason=gap_wrote")
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
