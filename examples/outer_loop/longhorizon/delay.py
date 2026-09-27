"""Outer delay writer for the LongHorizon example. Not a second supervisor.

Run it beside `agent-runner serve`. It never starts serve, never restarts
on 78/75/70, and never creates stop_file.

`logs/serve.pid` is JSON: {"pid": int, "create_time": number}. A bare
integer is only the legacy form. Requiring all-digit text skips every write.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

WORK = Path(os.environ["LH_WORK"])
EVENTS = WORK / "logs"
PROMPT = WORK / "prompts" / "main.md"
PID_FILE = WORK / "logs" / "serve.pid"
SNAPSHOT = Path(os.environ["LH_SNAPSHOT"])
STATE = Path(os.environ["LH_STATE"])
INSTRUCTION = "Instruction: Create marker.txt in the repository root."


def pid_alive() -> bool:
    try:
        data = json.loads(PID_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    pid = data.get("pid") if isinstance(data, dict) else data
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def goal_checks(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("event") or ev.get("kind")
        if kind == "goal_check":
            out.append(ev)
    return out


def latest_checks() -> list[dict]:
    files = sorted(EVENTS.glob("events-*.jsonl"))
    if not files:
        return []
    return goal_checks(files[-1].read_text())


def replace_instruction(line: str) -> None:
    rows = PROMPT.read_text().splitlines()
    if not rows or not rows[-1].startswith("Instruction:"):
        raise SystemExit("instruction line missing")
    if rows[-1] == line:
        return
    rows[-1] = line
    PROMPT.write_text("\n".join(rows) + "\n")


def append_once(token: str) -> None:
    needle = f"value={token}\n"
    current = STATE.read_text() if STATE.exists() else ""
    if needle in current:
        return
    with STATE.open("a", encoding="utf-8") as fh:
        fh.write(needle)


def is_satisfied(ev: dict) -> bool:
    raw = ev.get("satisfied")
    return isinstance(raw, bool) and raw


def main() -> None:
    seen = 0
    appended = False
    while pid_alive():
        checks = latest_checks()
        if len(checks) > seen:
            ev = checks[-1]
            seen = len(checks)
            if not is_satisfied(ev):
                SNAPSHOT.write_text("# snapshot\n\nretry\n", encoding="utf-8")
                replace_instruction(INSTRUCTION)
            elif not appended:
                token = ev.get("value")
                if token is None:
                    raise SystemExit("green goal_check has no value")
                append_once(str(token))
                SNAPSHOT.write_text(
                    f"# snapshot\n\ndone\nvalue={token}\n",
                    encoding="utf-8",
                )
                appended = True
        time.sleep(1)


if __name__ == "__main__":
    main()
