# agent_runner/cli/serve_cmd.py — STUB until Task 5.4
def add_parser(sub, parent) -> None:
    p = sub.add_parser("serve", parents=[parent], help="Serve (stub)")
    p.set_defaults(func=lambda _a: print("serve: not implemented yet") or 1)
