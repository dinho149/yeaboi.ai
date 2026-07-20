"""Intake mode menu and offline sub-menu rendering and resolution."""

import logging

from rich.console import Console

from yeaboi.prompts.intake import (
    INTAKE_MODE_MENU,
    INTAKE_MODE_ORDER,
    OFFLINE_SUBMENU,
    OFFLINE_SUBMENU_ORDER,
)

logger = logging.getLogger(__name__)


def _render_intake_mode_menu(console: Console) -> None:
    """Print the intake mode selection menu with numbered options.

    # See docs: "Project Intake Questionnaire" — smart intake
    #
    # Shown once at startup when no CLI flag (--quick) was given.
    # The user picks 1/2 (Smart / Offline) before the conversational opener.
    # Follows the same [N] styling pattern as _render_choice_options().

    Args:
        console: Rich Console instance for output.
    """
    logger.info("intake menu: shown")
    console.print()
    console.print("[bold]How would you like to get started?[/bold]")
    console.print()
    for i, mode_key in enumerate(INTAKE_MODE_ORDER):
        title, description = INTAKE_MODE_MENU[mode_key]
        console.print(f"  [command]\\[{i + 1}][/command] {title}")
        console.print(f"      {description}")
        console.print()


def _resolve_intake_mode(user_input: str) -> str | None:
    """Resolve numeric input to an intake mode key.

    Maps "1" → "smart", "2" → "offline" (indices follow INTAKE_MODE_ORDER).
    Returns None for any other input (invalid selection).

    Args:
        user_input: The raw user input (stripped).

    Returns:
        The mode key string, or None if input is not a valid selection.
    """
    try:
        idx = int(user_input)
    except ValueError:
        return None

    if 1 <= idx <= len(INTAKE_MODE_ORDER):
        logger.info("intake menu: selected %s", INTAKE_MODE_ORDER[idx - 1])
        return INTAKE_MODE_ORDER[idx - 1]
    return None


def _render_offline_submenu(console: Console) -> None:
    """Print the offline questionnaire sub-menu (export / import).

    Shown when the user picks [3] Offline questionnaire from the main menu.
    Same [N] styling pattern as _render_intake_mode_menu().

    Args:
        console: Rich Console instance for output.
    """
    logger.info("offline submenu: shown")
    console.print()
    for i, key in enumerate(OFFLINE_SUBMENU_ORDER):
        title, description = OFFLINE_SUBMENU[key]
        console.print(f"  [command]\\[{i + 1}][/command] {title}")
        console.print(f"      {description}")
        console.print()


def _resolve_offline_choice(user_input: str) -> str | None:
    """Resolve numeric input to an offline sub-menu action.

    Maps "1" → "export", "2" → "import".
    Returns None for any other input (invalid selection).

    Args:
        user_input: The raw user input (stripped).

    Returns:
        The action key string, or None if input is not a valid selection.
    """
    try:
        idx = int(user_input)
    except ValueError:
        return None

    if 1 <= idx <= len(OFFLINE_SUBMENU_ORDER):
        logger.info("offline submenu: selected %s", OFFLINE_SUBMENU_ORDER[idx - 1])
        return OFFLINE_SUBMENU_ORDER[idx - 1]
    return None
