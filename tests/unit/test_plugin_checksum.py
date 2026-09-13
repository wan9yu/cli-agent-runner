from __future__ import annotations

import pytest

from agent_runner._plugin_checksum import compute_plugin_checksum, verify_pin


def test_compute_checksum_should_return_sha256_prefixed_hex_when_module_resolvable():
    digest = compute_plugin_checksum("agent_runner._plugin_checksum")

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_verify_pin_should_return_unpinned_and_no_digest_when_name_absent():
    verdict, actual = verify_pin("acme", "agent_runner._plugin_checksum", {})

    assert verdict == "unpinned"
    assert actual is None


def test_verify_pin_should_return_verified_and_the_digest_when_pin_matches():
    real = compute_plugin_checksum("agent_runner._plugin_checksum")

    verdict, actual = verify_pin("self", "agent_runner._plugin_checksum", {"self": real})

    assert verdict == "verified"
    assert actual == real


def test_verify_pin_should_return_mismatch_and_the_computed_digest_when_pin_wrong():
    real = compute_plugin_checksum("agent_runner._plugin_checksum")

    verdict, actual = verify_pin(
        "self", "agent_runner._plugin_checksum", {"self": "sha256:" + "0" * 64}
    )

    assert verdict == "mismatch"
    assert actual == real


def test_compute_checksum_should_raise_when_module_unresolvable():
    with pytest.raises((ModuleNotFoundError, OSError, ValueError)):
        compute_plugin_checksum("agent_runner._does_not_exist_xyz")
