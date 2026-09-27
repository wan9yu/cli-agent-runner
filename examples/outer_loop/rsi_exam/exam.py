#!/usr/bin/env python3
"""Local holdout scorer. Prints one finite float. Does not call a model."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOKEN = "SEALED_ASSERT_7c1e9a"
SEALED = Path(__file__).resolve().parent / "sealed.txt"


def rows() -> list[tuple[int, int, int]] | None:
    try:
        lines = [
            line.strip() for line in SEALED.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    except OSError:
        return None
    if not lines or lines[0] != TOKEN:
        return None
    parsed: list[tuple[int, int, int]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != 3:
            return None
        try:
            parsed.append((int(parts[0]), int(parts[1]), int(parts[2])))
        except ValueError:
            return None
    return parsed or None


def score(visible: Path) -> float:
    data = rows()
    if not data:
        return 0.0
    try:
        spec = importlib.util.spec_from_file_location("visible_practice", visible)
        if spec is None or spec.loader is None:
            return 0.0
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        predict = module.predict
    except Exception:
        return 0.0
    hits = 0
    for left, right, expected in data:
        try:
            if int(predict(int(left), int(right))) == expected:
                hits += 1
        except Exception:
            continue
    return hits / len(data)


def main() -> int:
    visible = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    value = 0.0 if visible is None else score(visible)
    print(f"{value:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
