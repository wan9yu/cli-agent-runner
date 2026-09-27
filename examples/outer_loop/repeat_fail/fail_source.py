"""Stable red check. Local command. Not a model call."""

VALUE = 0.25


def main() -> int:
    print(f"{VALUE:.2f}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
