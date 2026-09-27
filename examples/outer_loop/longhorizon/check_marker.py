"""Print 1.0 and exit 0 only when marker.txt is a regular file. Else print 0.0 and exit 1."""

from pathlib import Path

marker = Path(__file__).resolve().parent / "marker.txt"
if marker.is_file():
    print("1.0")
    raise SystemExit(0)
print("0.0")
raise SystemExit(1)
