"""Static guard: the R1128 hard wall must stay SIGTERM-first.

``agent_runtime._kill_pgroup`` is the ONE primitive the R1128 timeout, the
round-budget grace-kill, and the cooperative-stop reap all funnel through. The
typed ``cooperative_stop`` signal (v0.3.9 §B) threads a ``first_signal``
parameter into it so ONLY the cooperative-stop call site can send a non-SIGTERM
first signal. This test pins that PROPERTY in the source so a future edit cannot
silently make the hard wall send a cooperative signal:

- every ``_terminate_agent`` call in ``run()`` OUTSIDE an ``except`` handler
  (the R1128 wall + the grace-kill) passes ``first_signal=signal.SIGTERM``
  literally;
- the ``_terminate_agent`` call INSIDE ``run()``'s ``except`` handler (the
  cooperative-stop reap) passes a bound variable, not a hard-coded signal;
- no kill call anywhere hard-codes SIGINT/SIGKILL/SIGSTOP as its first signal;
- ``_kill_pgroup`` sends its FIRST killpg with the ``first_signal`` parameter,
  not a hard-coded ``signal.SIGTERM``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2] / "agent_runner" / "agent_runtime.py"

# Never a valid FIRST signal for any kill call -- these skip the cooperative
# grace (SIGKILL), freeze (SIGSTOP), or would masquerade as the hard wall.
_FORBIDDEN_FIRST_SIGNALS = {"SIGKILL", "SIGSTOP", "SIGINT"}


def _func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in agent_runtime.py")


def _calls_to(scope: ast.AST, callee: str) -> list[ast.Call]:
    out: list[ast.Call] = []
    for node in ast.walk(scope):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == callee
        ):
            out.append(node)
    return out


def _first_signal_kw(call: ast.Call) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "first_signal":
            return kw.value
    return None


def _is_signal_attr(node: ast.expr | None, attr: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Name)
        and node.value.id == "signal"
    )


def _load() -> ast.Module:
    return ast.parse(_RUNTIME.read_text())


def test_hard_wall_terminate_calls_should_pin_sigterm_first() -> None:
    run = _func(_load(), "run")
    except_calls: list[ast.Call] = []
    for node in ast.walk(run):
        if isinstance(node, ast.ExceptHandler):
            except_calls.extend(_calls_to(node, "_terminate_agent"))
    all_calls = _calls_to(run, "_terminate_agent")
    body_calls = [c for c in all_calls if c not in except_calls]

    assert len(body_calls) >= 2, (
        "expected the R1128 wall + grace-kill _terminate_agent call sites in run() body"
    )
    for call in body_calls:
        fs = _first_signal_kw(call)
        assert _is_signal_attr(fs, "SIGTERM"), (
            "a hard-wall _terminate_agent call site must PIN first_signal=signal.SIGTERM literally"
        )

    assert len(except_calls) >= 1, "expected the cooperative-stop reap in run()'s except handler"
    for call in except_calls:
        fs = _first_signal_kw(call)
        assert isinstance(fs, ast.Name), (
            "the cooperative-stop reap must pass the threaded first_signal variable, "
            "not a hard-coded signal"
        )


def test_no_kill_call_should_hard_code_a_forbidden_first_signal() -> None:
    tree = _load()
    for callee in ("_terminate_agent", "_kill_pgroup"):
        for call in _calls_to(tree, callee):
            fs = _first_signal_kw(call)
            for bad in _FORBIDDEN_FIRST_SIGNALS:
                assert not _is_signal_attr(fs, bad), (
                    f"{callee}(...) hard-codes first_signal=signal.{bad} -- forbidden"
                )


def test_kill_pgroup_should_send_first_killpg_with_the_first_signal_parameter() -> None:
    kill_pgroup = _func(_load(), "_kill_pgroup")
    killpg_calls = [
        node
        for node in ast.walk(kill_pgroup)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "killpg"
    ]
    assert killpg_calls, "expected os.killpg calls in _kill_pgroup"
    # The FIRST killpg is the cooperative/hard-wall first signal; it must use the
    # parameter, never a hard-coded signal.SIGTERM.
    first = killpg_calls[0]
    assert (
        len(first.args) >= 2
        and isinstance(first.args[1], ast.Name)
        and (first.args[1].id == "first_signal")
    ), "the first os.killpg must send `first_signal`, not a hard-coded signal"
