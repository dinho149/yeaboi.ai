"""Calendar tools — bank/public holiday detection by country.

# See docs: "Tools" — tool types, @tool decorator, risk levels
#
# The `holidays` Python package provides bank/public holiday data for 100+
# countries (ISO 3166-1 alpha-2 codes). This tool wraps it so the agent can
# look up how many working days are lost to holidays in the planning window,
# replacing the manual Q27 estimate with data-driven accuracy.
#
# Based on analysis of Capacity_Plan_Template.xlsx — real feature capacity is
# ~24% of gross after deductions (bank holidays, leave, unplanned, onboarding).
#
# Risk level: low — read-only local computation, no network or filesystem access.
"""

import locale
import logging
import os
import platform
import subprocess
from datetime import date, timedelta

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Default subdivision for countries where the base holiday set is incomplete.
# The `holidays` library splits country-level from subdivision-level holidays.
# E.g. GB base has only Good Friday + Christmas — Easter Monday and the August
# bank holiday are England/Wales-specific. Without a subdivision, sprint
# capacity calculations under-count holidays.
#
# When locale auto-detection returns just "GB" (from en_GB), we assume England
# because most UK tech teams are England-based. Users in Scotland/NI can override
# via the choice menu.
_DEFAULT_SUBDIVISIONS: dict[str, str] = {
    "GB": "ENG",  # Easter Monday, August bank holiday
}


def _extract_country_from_locale_string(loc: str) -> str | None:
    """Extract the country code from a locale string like 'en_GB.UTF-8' → 'GB'."""
    import holidays as holidays_lib

    loc = loc.split(".")[0]  # strip encoding
    if "_" not in loc:
        return None
    country = loc.split("_")[1].upper()
    if country in holidays_lib.list_supported_countries():
        return country
    return None


def _detect_country_from_locale() -> str | None:
    """Infer ISO 3166-1 alpha-2 country code from the system locale.

    Tries multiple sources in order:
      1. Python locale.getlocale() — e.g. "en_GB.UTF-8"
      2. LANG environment variable — e.g. "en_GB.UTF-8"
      3. macOS AppleLocale (via `defaults read`) — e.g. "en_GB"

    Returns None if no source has a recognisable country code.
    This is purely local — no network call, no IP lookup.
    """
    # 1. Python locale module
    try:
        loc = locale.getlocale()[0] or ""
        logger.debug("Locale detection source 1 (getlocale): %r", loc)
        result = _extract_country_from_locale_string(loc)
        if result:
            logger.debug("Country detected from getlocale: %s", result)
            return result
    except (ValueError, AttributeError):
        pass

    # 2. LANG environment variable — often set even when Python locale returns "C"
    lang = os.environ.get("LANG", "")
    logger.debug("Locale detection source 2 (LANG env): %r", lang)
    result = _extract_country_from_locale_string(lang)
    if result:
        logger.debug("Country detected from LANG env: %s", result)
        return result

    # 3. macOS: read AppleLocale from user defaults (e.g. "en_GB")
    if platform.system() == "Darwin":
        try:
            logger.debug("Locale detection source 3: reading macOS AppleLocale")
            apple_locale = subprocess.run(
                ["defaults", "read", "NSGlobalDomain", "AppleLocale"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if apple_locale.returncode == 0:
                raw = apple_locale.stdout.strip()
                logger.debug("macOS AppleLocale returned: %r", raw)
                result = _extract_country_from_locale_string(raw)
                if result:
                    logger.debug("Country detected from AppleLocale: %s", result)
                    return result
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            logger.debug("macOS AppleLocale read failed")

    logger.debug("Country auto-detection failed — no source returned a valid code")
    return None


@tool
def detect_bank_holidays(
    country_code: str = "",
    sprint_length_weeks: int = 2,
    num_sprints: int = 1,
    start_date: str = "",
) -> str:
    """Detect bank/public holidays that fall within a sprint planning window.

    Returns the holidays (with dates and names) that fall on weekdays within the
    planning window, plus a count. Use this to auto-populate capacity deductions
    instead of asking the user to count holidays manually.

    country_code: ISO 3166-1 alpha-2 country code (e.g. "GB", "US", "DE", "IN").
        Also accepts 3-letter codes (e.g. "GBR") and some common aliases.
        When empty, auto-detects from the system locale (e.g. en_GB → GB).
    sprint_length_weeks: Length of each sprint in weeks (default: 2).
    num_sprints: Number of sprints in the planning window (default: 1).
    start_date: Start date in YYYY-MM-DD format (default: today).
    """
    import holidays as holidays_lib

    logger.debug(
        "detect_bank_holidays called: country=%r, weeks=%d, sprints=%d, start=%r",
        country_code,
        sprint_length_weeks,
        num_sprints,
        start_date,
    )

    # Parse start date
    if start_date and start_date.strip():
        try:
            start = date.fromisoformat(start_date.strip())
        except ValueError:
            return f"Error: invalid date format '{start_date}'. Use YYYY-MM-DD."
    else:
        start = date.today()

    # Resolve country code — explicit or auto-detected from locale
    code = country_code.strip().upper() if country_code and country_code.strip() else ""
    auto_detected = False
    if not code:
        detected = _detect_country_from_locale()
        if detected:
            code = detected
            auto_detected = True
        else:
            return (
                "Error: no country_code provided and could not auto-detect from system locale.\n"
                "Please provide an ISO 3166-1 alpha-2 code (e.g. GB, US, DE, IN, AU)."
            )

    supported = holidays_lib.list_supported_countries()
    if code not in supported:
        # Try common name→code mapping for user-friendliness
        suggestions = [k for k in supported if code in k]
        hint = f" Did you mean one of: {', '.join(suggestions[:5])}?" if suggestions else ""
        return (
            f"Error: '{code}' is not a supported country code.{hint}\n"
            f"Use an ISO 3166-1 alpha-2 code (e.g. GB, US, DE, IN, AU).\n"
            f"Full list: https://python-holidays.readthedocs.io/en/latest/index.html"
        )

    # Calculate planning window
    total_days = sprint_length_weeks * num_sprints * 7
    end = start + timedelta(days=total_days)

    # Collect years that span the planning window
    years = set()
    for year in range(start.year, end.year + 1):
        years.add(year)

    # Use default subdivision for countries where the base set is incomplete.
    # E.g. GB base omits Easter Monday and August bank holiday — these are
    # England/Wales-specific. Most UK tech teams are England-based, so 'ENG'
    # is a sensible default when locale only says 'en_GB'.
    subdiv = _DEFAULT_SUBDIVISIONS.get(code)

    # Get holidays for the country across all relevant years
    country_holidays = holidays_lib.country_holidays(code, years=years, subdiv=subdiv)

    # Filter to holidays within the planning window that fall on weekdays (Mon-Fri)
    window_holidays = []
    for d, name in sorted(country_holidays.items()):
        if start <= d < end and d.weekday() < 5:  # Mon=0, Fri=4
            window_holidays.append((d, name))

    # Build response — the holidays package doesn't expose human-readable country names,
    # so we use a small lookup for common ones and fall back to just the code.
    country_names = {
        "GB": "United Kingdom",
        "US": "United States",
        "DE": "Germany",
        "FR": "France",
        "IN": "India",
        "AU": "Australia",
        "CA": "Canada",
        "NL": "Netherlands",
        "ES": "Spain",
        "IT": "Italy",
        "JP": "Japan",
        "BR": "Brazil",
        "IE": "Ireland",
        "SE": "Sweden",
        "NO": "Norway",
        "DK": "Denmark",
        "FI": "Finland",
        "PL": "Poland",
        "AT": "Austria",
        "CH": "Switzerland",
        "NZ": "New Zealand",
        "SG": "Singapore",
        "ZA": "South Africa",
        "MX": "Mexico",
        "KR": "South Korea",
        "PT": "Portugal",
    }
    country_name = country_names.get(code, code)
    if auto_detected:
        logger.debug("Country auto-detected as %s from system locale", code)
    logger.debug("Holiday window: %s to %s (%d holidays found)", start, end, len(window_holidays))
    auto_note = " (auto-detected from system locale)" if auto_detected else ""
    lines = [
        f"Bank holidays in {country_name} ({code}){auto_note}",
        f"Planning window: {start.isoformat()} to {end.isoformat()} ({total_days} days, {num_sprints} sprint(s))",
        "",
    ]

    if window_holidays:
        lines.append(f"**{len(window_holidays)} bank holiday(s) on weekdays:**")
        for d, name in window_holidays:
            day_name = d.strftime("%A")
            lines.append(f"  - {d.isoformat()} ({day_name}): {name}")
    else:
        lines.append("No bank holidays fall on weekdays in this planning window.")

    lines.append("")
    lines.append(f"Total working days lost to bank holidays: **{len(window_holidays)}**")

    return "\n".join(lines)


def get_bank_holidays_structured(
    country_code: str = "",
    sprint_length_weeks: int = 2,
    num_sprints: int = 1,
    start_date: str = "",
) -> list[dict]:
    """Return structured bank holiday data for the capacity_check node.

    Non-tool helper called directly by the capacity_check node. Returns a list
    of dicts with date, name, and weekday for each holiday in the planning window.
    The existing detect_bank_holidays @tool stays as-is for LLM use.

    Args:
        country_code: ISO 3166-1 alpha-2 country code. Auto-detects from locale if empty.
        sprint_length_weeks: Length of each sprint in weeks.
        num_sprints: Number of sprints in the planning window.
        start_date: Start date in YYYY-MM-DD format (default: today).

    Returns:
        A list of dicts: [{"date": date, "name": str, "weekday": str}, ...].
        Empty list if country_code is invalid or not auto-detectable.
    """
    import holidays as holidays_lib

    if start_date and start_date.strip():
        try:
            start = date.fromisoformat(start_date.strip())
        except ValueError:
            return []
    else:
        start = date.today()

    code = country_code.strip().upper() if country_code and country_code.strip() else ""
    if not code:
        detected = _detect_country_from_locale()
        if detected:
            code = detected
        else:
            return []

    supported = holidays_lib.list_supported_countries()
    if code not in supported:
        return []

    total_days = sprint_length_weeks * num_sprints * 7
    end = start + timedelta(days=total_days)

    years = set(range(start.year, end.year + 1))
    subdiv = _DEFAULT_SUBDIVISIONS.get(code)
    country_holidays = holidays_lib.country_holidays(code, years=years, subdiv=subdiv)

    result = []
    for d, name in sorted(country_holidays.items()):
        if start <= d < end and d.weekday() < 5:
            result.append({"date": d, "name": name, "weekday": d.strftime("%A")})

    logger.debug("get_bank_holidays_structured returning %d holidays for %s", len(result), code)
    return result
