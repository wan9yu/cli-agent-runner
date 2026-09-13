from __future__ import annotations

import pytest

from agent_runner._plugin_checksum import compute_plugin_checksum, verify_pin


def test_compute_checksum_should_return_sha256_prefixed_hex_when_module_resolvable():
    digest = compute_plugin_checksum("agent_runner._plugin_checksum")

    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_verify_pin_should_return_unpinned_when_name_absent():
    assert verify_pin("acme", "agent_runner._plugin_checksum", {}) == "unpinned"


def test_verify_pin_should_return_verified_when_pin_matches():
    real = compute_plugin_checksum("agent_runner._plugin_checksum")

    verdict = verify_pin("self", "agent_runner._plugin_checksum", {"self": real})

    assert verdict == "verified"


def test_verify_pin_should_return_mismatch_when_pin_wrong():
    verdict = verify_pin("self", "agent_runner._plugin_checksum", {"self": "sha256:" + "0" * 64})

    assert verdict == "mismatch"


def test_compute_checksum_should_raise_when_module_unresolvable():
    with pytest.raises((ModuleNotFoundError, OSError, ValueError)):
        compute_plugin_checksum("agent_runner._does_not_exist_xyz")
