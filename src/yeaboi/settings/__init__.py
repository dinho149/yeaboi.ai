"""Headless settings service — masked reads and allowlisted writes over config.

The desktop app (and any future surface) edits ~/.yeaboi/.env only through
:mod:`yeaboi.settings.engine`; nothing here renders, and no secret ever leaves
unmasked. The TUI keeps its own screens over the same ``config.py`` writers.
"""

from yeaboi.settings.engine import (
    SettingsSnapshot,
    SettingValue,
    SettingWrite,
    discover_models,
    get_settings,
    provider_catalog,
    set_allowed_paths,
    set_data_dir,
    set_setting,
    verify_provider,
)

__all__ = [
    "SettingValue",
    "SettingWrite",
    "SettingsSnapshot",
    "discover_models",
    "get_settings",
    "provider_catalog",
    "set_allowed_paths",
    "set_data_dir",
    "set_setting",
    "verify_provider",
]
