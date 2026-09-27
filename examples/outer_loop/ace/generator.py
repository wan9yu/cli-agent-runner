"""Print one finite float. The check is this process, not an LLM."""

MARK = 0


def metric() -> float:
    return float(MARK)


if __name__ == "__main__":
    print(f"{metric():.1f}")
