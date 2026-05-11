# agent_runner/cli/monitor_cmd.py — STUB until Task 5.4
def add_parser(sub, parent) -> None:
    p = sub.add_parser("monitor", parents=[parent], help="Monitor (stub)")
    p.set_defaults(func=lambda _a: print("monitor: not implemented yet") or 1)
