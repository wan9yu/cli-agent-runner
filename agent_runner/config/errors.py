"""The one exception config validation ever raises."""

from __future__ import annotations


class ConfigError(ValueError):
    """Raised when a config TOML contains a removed or invalid field.

    Subclasses ValueError: pre-0.2.2 callers catching ValueError from
    load_config keep working. tests/invariants/test_config_error_consistency.py
    pins both the subclass relationship and the absence of bare ValueError here.
    """
