# agent_runner/cli/peek_cmd.py — STUB until Task 5.4
def add_parser(sub, parent) -> None:
    for verb in ("peek", "watch"):
        p = sub.add_parser(verb, parents=[parent], help=f"{verb} (stub)")
        p.set_defaults(func=lambda _a, v=verb: print(f"{v}: not implemented yet") or 1)
