#!/usr/bin/env python3
"""Synthetic pre-OOM growth child for agent-runner's gated
real-cgroup e2e test (tests/e2e/test_e2e_pre_oom.py). Paced growth is
REQUIRED -- see that module's docstring for why an unpaced loop would
prove nothing.
"""

from __future__ import annotations

import time

_CHUNK_BYTES = 8 * 1024 * 1024
_PAGE_BYTES = 4096
_PACE_S = 2.5


def main() -> None:
    chunks: list[bytearray] = []
    while True:
        block = bytearray(_CHUNK_BYTES)
        for i in range(0, _CHUNK_BYTES, _PAGE_BYTES):
            block[i] = 1  # touch every page -- an untouched bytearray is virtual-only
        chunks.append(block)
        time.sleep(_PACE_S)


if __name__ == "__main__":
    main()
