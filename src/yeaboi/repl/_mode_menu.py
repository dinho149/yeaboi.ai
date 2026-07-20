"""Top-level agent mode selection menu rendering and resolution.

# See docs: "Architecture" — the CLI layer handles mode selection before
# any REPL loop starts. This module provides stateless render/resolve helpers
# that cli.main() calls directly, mirroring the _intake_menu.py pattern.
#
# Adding a new mode:
#   1. Add an entry to STARTUP_MODE_MENU and STARTUP_MODE_ORDER in prompts/intake.py
#   2. Add the new key to the --mode choices in cli.build_parser()
#   3. Add a dispatch branch in cli.main()
"""

import logging

from rich.console import Console

from yeaboi.prompts.intake import STARTUP_MODE_MENU, STARTUP_MODE_ORDER

logger = logging.getLogger(__name__)


def _render_startup_mode_menu(console: Console) -> None:
    """Print the top-level mode selection menu with numbered options.

    # See docs: "Architecture" — four layers. Mode selection is CLI-layer
    # chrome, shown once before any REPL loop starts.
    #
    # Styled identically to _render_intake_mode_menu() so the two menus feel
    # consistent — same [N] command markup, same two-line item format.

    Args:
        console: Rich Console instance for output.
    """
    logger.info("mode menu: shown")
    console.print()
    console.print("[bold]What would you like to do?[/bold]")
    console.print()
    for i, mode_key in enumerate(STARTUP_MODE_ORDER):
        title, description = STARTUP_MODE_MENU[mode_key]
        console.print(f"  [command]\\[{i + 1}][/command] {title}")
        console.print(f"      {description}")
        console.print()


def _resolve_startup_mode(user_input: str) -> str | None:
    """Resolve numeric input to a startup mode key.

    Maps "1" → "project-planning", "2" → "coming-soon-1", etc.
    Also accepts the exact mode key string (e.g. "project-planning") for
    programmatic use — lets cli.main() use the same resolver for both the
    interactive menu and the --mode flag.
    Returns None for any other input (invalid selection).

    Args:
        user_input: The raw user input (stripped).

    Returns:
        The mode key string, or None if input is not a valid selection.
    """
    try:
        idx = int(user_input)
        if 1 <= idx <= len(STARTUP_MODE_ORDER):
            logger.info("mode menu: selected %s", STARTUP_MODE_ORDER[idx - 1])
            return STARTUP_MODE_ORDER[idx - 1]
        return None
    except ValueError:
        pass

    # Accept exact key match (used by --mode flag and tests)
    if user_input in STARTUP_MODE_ORDER:
        logger.info("mode menu: selected %s", user_input)
        return user_input
    return None
