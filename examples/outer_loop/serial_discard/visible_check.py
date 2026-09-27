"""Visible check. Local command. Not a model call.

Does not read the attempt directory. Does not read the keep directory.
"""

VALUE = 0.0


def main() -> int:
    print(f"{VALUE:.1f}")
    if VALUE == 1.0:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
