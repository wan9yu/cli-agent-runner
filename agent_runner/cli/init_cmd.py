# agent_runner/cli/init_cmd.py — STUB until Task 5.3
def add_parser(sub, parent) -> None:
    p = sub.add_parser("init", parents=[parent], help="Scaffold project (stub)")
    p.set_defaults(func=lambda _a: print("init: not implemented yet") or 1)
