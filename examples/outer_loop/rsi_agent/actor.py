"""Print one finite float. The check is this process, not an LLM."""

MARK_A = 0
MARK_B = 0


def metric() -> float:
    return float(MARK_A + 10 * MARK_B)


if __name__ == "__main__":
    print(f"{metric():.1f}")
