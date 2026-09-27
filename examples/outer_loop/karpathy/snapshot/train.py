"""Print one finite float. Larger is better. Exit 0 is not the keep."""

FLOOR = 10
RIDGE = 0


def metric() -> float:
    return float(FLOOR + RIDGE)


if __name__ == "__main__":
    print(f"{metric():.1f}")
