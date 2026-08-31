"""hooks._cap_redacted: redact secrets BEFORE truncating, so a secret straddling
the head/tail cut can never survive as a partial fragment in events.jsonl."""

from __future__ import annotations

from agent_runner.hooks import _MAX_ERROR_MSG_BYTES, _cap_redacted, _summarize_error


def test_secret_straddling_the_truncation_cut_is_never_emitted() -> None:
    # sk-ant-<16+ chars> is one of the anchored patterns, but the anchor regex
    # requires >= 16 trailing chars to match. Position the secret so the 1024-byte
    # head cut lands just 12 chars into its payload: truncate-then-redact would
    # scan only that 12-char remnant (below the regex's 16-char minimum) and miss
    # it entirely, leaking "sk-ant-AAAAAAAAAAAA" raw. A leading space gives the
    # anchor its required non-alnum boundary so the *full*, pre-truncation token
    # (well over the minimum) is what redact-then-truncate actually matches.
    secret = "sk-ant-" + "A" * 60
    tb = ("x" * 1004) + " " + secret + ("y" * 3000)
    payload_start = tb.index(secret) + len("sk-ant-")
    assert 1024 - payload_start == 12  # sanity: cut lands 12 chars into the "A" payload
    out = _summarize_error(RuntimeError("boom"), tb=tb)
    assert secret not in out["traceback"]
    assert "AAAAAAAAAA" not in out["traceback"]  # no surviving fragment of the key


def test_error_message_is_capped() -> None:
    huge = RuntimeError("z" * 10000)
    out = _summarize_error(huge, tb="short")
    assert len(out["error_message"]) <= _MAX_ERROR_MSG_BYTES + len("\n... [truncated] ...\n")


def test_cap_redacted_short_text_passthrough() -> None:
    assert _cap_redacted("hello", 100) == "hello"
