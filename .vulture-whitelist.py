# Known false positives for vulture dead-code scan.
# Add entries here when vulture flags real-but-indirectly-used code.
# Format: assignment to name; vulture treats this as "used".

from agent_runner import __version__  # noqa: F401  — exposed via package metadata
from agent_runner.events import KNOWN_EVENT_KINDS  # noqa: F401  — read by invariant tests
