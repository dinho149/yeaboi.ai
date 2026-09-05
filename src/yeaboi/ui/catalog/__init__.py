"""The integrations catalog browser — Settings ▸ Catalog, behind Enter.

Settings itself stays "hidden until connected": a user who has connected
nothing reads no vendor name there unasked. This page is the other side of that
bargain — it renders the WHOLE roster, because pressing "browse" is the ask.

One page, four states in one runner: browsing the family-grouped list, choosing
an auth method, typing a field, and watching a verify probe. Connector rows run
the same auth-method-first add flow the CLI's ``connections add`` owns; a
``managed_by == "credentials"`` row points at Credentials/setup instead, which
keeps every credential write on the path that already guards it.
"""

from __future__ import annotations

import logging
import textwrap
import time

from rich.columns import Columns
from rich.console import Console, Group
from rich.text import Text

from yeaboi.ui.shared._components import (
    PAD,
    SETTINGS_THEME,
    build_action_buttons,
    build_page_panel,
    build_scrollbar,
    calc_viewport,
    settings_title,
)
from yeaboi.ui.shared._input import set_text_entry

logger = logging.getLogger(__name__)

_THEME = SETTINGS_THEME

#: One connector's contribution to the list: head line + summary line.
_ROW_LINES = 2


def visible_rows(payload: dict, filter_text: str) -> list[dict]:
    """The catalog rows that survive the incremental filter, in payload order."""
    needle = filter_text.strip().lower()
    if not needle:
        return list(payload["connectors"])
    return [
        row
        for row in payload["connectors"]
        if needle in row["label"].lower() or needle in row["summary"].lower() or needle in row["family_label"].lower()
    ]


def _list_lines(rows: list[dict], selected: int) -> tuple[list[Text], list[int]]:
    """The browse list as lines, plus each selectable row's head-line index."""
    lines: list[Text] = []
    heads: list[int] = []
    family = None
    for i, row in enumerate(rows):
        if row["family_label"] != family:
            if family is not None:
                lines.append(Text(""))
            family = row["family_label"]
            header = Text(justify="left", no_wrap=True, overflow="ellipsis")
            header.append(f"{PAD}{family}", style=f"bold {_THEME.accent_bright}")
            lines.append(header)
        heads.append(len(lines))
        picked = i == selected
        head = Text(justify="left", no_wrap=True, overflow="ellipsis")
        head.append(f"{PAD}{'▸ ' if picked else '  '}", style=_THEME.accent_bright)
        head.append(f"{row['glyph']} {row['label']}", style=f"bold {row['accent'] or _THEME.value}")
        if row["connected"]:
            head.append("  ● connected", style=_THEME.good)
        if row["read_only"]:
            head.append("  read-only", style=_THEME.muted)
        if row["managed_by"] == "credentials":
            head.append("  via Credentials", style=_THEME.dim)
        lines.append(head)
        summary = Text(justify="left", no_wrap=True, overflow="ellipsis")
        summary.append(f"{PAD}    {row['summary']}", style=_THEME.muted)
        lines.append(summary)
    return lines, heads


def _entry_lines(entry: dict, width: int) -> list[Text]:
    """The add-flow pane: connector detail, then whatever the stage asks for."""
    row = entry["row"]
    lines: list[Text] = []
    head = Text(justify="left", no_wrap=True, overflow="ellipsis")
    head.append(f"{PAD}{row['glyph']} {row['label']}", style=f"bold {row['accent'] or _THEME.value}")
    head.append(f"  {row['family_label']}", style=_THEME.dim)
    lines.append(head)
    for wrapped in textwrap.wrap(row["detail"] or row["summary"], max(20, width - 14)):
        detail = Text(justify="left", no_wrap=True, overflow="ellipsis")
        detail.append(f"{PAD}{wrapped}", style=_THEME.muted)
        lines.append(detail)
    if row["docs_url"]:
        docs = Text(justify="left", no_wrap=True, overflow="ellipsis")
        docs.append(f"{PAD}docs: {row['docs_url']}", style=_THEME.dim)
        lines.append(docs)
    lines.append(Text(""))

    stage = entry["stage"]
    if stage == "method":
        for i, method in enumerate(entry["methods"]):
            picked = i == entry["method_idx"]
            line = Text(justify="left", no_wrap=True, overflow="ellipsis")
            line.append(f"{PAD}{'▸ ' if picked else '  '}", style=_THEME.accent_bright)
            line.append(method.label, style=f"bold {_THEME.value}" if picked else _THEME.value)
            if method.recommended:
                line.append("  recommended", style=_THEME.good)
            lines.append(line)
            summary = Text(justify="left", no_wrap=True, overflow="ellipsis")
            summary.append(f"{PAD}    {method.summary}", style=_THEME.muted)
            lines.append(summary)
            if method.warning:
                warn = Text(justify="left", no_wrap=True, overflow="ellipsis")
                warn.append(f"{PAD}    ⚠  {method.warning}", style=_THEME.warn)
                lines.append(warn)
    elif stage == "field":
        for label, shown in entry["saved"]:
            done = Text(justify="left", no_wrap=True, overflow="ellipsis")
            done.append(f"{PAD}{label}: ", style=_THEME.muted)
            done.append(shown, style=_THEME.good)
            lines.append(done)
        field = entry["fields"][entry["field_idx"]]
        if field.help_scope:
            scope = Text(justify="left", no_wrap=True, overflow="ellipsis")
            scope.append(f"{PAD}{field.help_scope}", style=_THEME.dim)
            lines.append(scope)
        prompt = Text(justify="left", no_wrap=True, overflow="ellipsis")
        optional = "" if field.required else " (optional)"
        default = f" [{field.default}]" if field.default else ""
        prompt.append(f"{PAD}{field.label}{optional}{default}: ", style=_THEME.value)
        # A secret is typed here, so it is drawn as bullets — the value itself
        # must never reach the screen, exactly as the settings rows mask it.
        typed = "•" * len(entry["typed"]) if field.secret else entry["typed"]
        prompt.append(typed, style=_THEME.accent_bright)
        prompt.append("█", style=_THEME.accent_bright)
        lines.append(prompt)
        if entry["notice"]:
            notice = Text(justify="left", no_wrap=True, overflow="ellipsis")
            notice.append(f"{PAD}{entry['notice']}", style=_THEME.warn)
            lines.append(notice)
    elif stage == "verify":
        busy = Text(justify="left", no_wrap=True, overflow="ellipsis")
        busy.append(f"{PAD}verifying…", style=_THEME.accent_bright)
        lines.append(busy)
    return lines


def build_catalog_screen(
    payload: dict,
    selected: int,
    *,
    filter_text: str = "",
    filtering: bool = False,
    width: int,
    height: int,
    scroll_offset: int = 0,
    message: str = "",
    entry: dict | None = None,
) -> object:
    """The catalog page: title, count line, the grouped list (or the add pane).

    Pure and render-only — every fact arrives in ``payload``/``entry``, so the
    same builder draws a live browse and a unit test's snapshot alike.
    """
    rows = visible_rows(payload, filter_text)
    connected = sum(1 for row in payload["connectors"] if row["connected"])
    total = len(payload["connectors"])

    subtitle = Text(justify="left", no_wrap=True, overflow="ellipsis")
    subtitle.append(f"{PAD}Integrations catalog", style=f"bold {_THEME.accent_bright}")
    subtitle.append(f"  {connected} of {total} connected", style=_THEME.muted)
    if filtering or filter_text:
        subtitle.append(f"  /{filter_text}", style=_THEME.warn if filtering else _THEME.dim)
        subtitle.append("█" if filtering else "", style=_THEME.warn)
    if message:
        subtitle.append(f"  {message}", style=_THEME.dim)

    if entry is not None:
        lines = _entry_lines(entry, width)
        actions = ["Back"]
    elif not rows:
        empty = Text(justify="left", no_wrap=True, overflow="ellipsis")
        empty.append(f"{PAD}Nothing matches — Esc clears the filter", style=_THEME.muted)
        lines = [empty]
        actions = ["Back"]
    else:
        lines, _ = _list_lines(rows, selected)
        actions = ["Connect", "Back"]

    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    max_scroll = max(0, len(lines) - viewport_h)
    offset = min(max(0, scroll_offset), max_scroll)
    window = lines[offset : offset + viewport_h]
    window += [Text("")] * (viewport_h - len(window))
    scrollbar = build_scrollbar(viewport_h, len(lines), offset, max_scroll)
    body = Columns([Group(*window), scrollbar], padding=0, expand=False) if scrollbar else Group(*window)

    btn_top, btn_mid, btn_bot = build_action_buttons(actions, 0)
    content = Group(
        Text(""),
        settings_title(width=width),
        Text(""),
        subtitle,
        Text(""),
        body,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )
    return build_page_panel(content, theme=_THEME, height=height)


def _scroll_to(heads: list[int], selected: int, scroll: int, viewport_h: int) -> int:
    """Keep the selected row's two lines inside the viewport."""
    if not heads or not (0 <= selected < len(heads)):
        return scroll
    top, bottom = heads[selected], heads[selected] + _ROW_LINES - 1
    if top < scroll:
        return top
    if bottom >= scroll + viewport_h:
        return bottom - viewport_h + 1
    return scroll


def _mint_external_id(field, connector) -> str | None:
    """AWS's external ID is yeaboi's to mint, not the user's to invent."""
    import os

    from yeaboi.config import apply_config_value

    if field.env != "AWS_EXTERNAL_ID" or os.environ.get(field.env, "").strip():
        return None
    from yeaboi.connectors.aws import new_external_id

    generated = new_external_id()
    apply_config_value(field.env, generated)
    os.environ[field.env] = generated
    logger.info("catalog: minted %s for %s", field.env, connector.key)
    return generated


def _run_verify(console: Console, live, read_key, frame_time, supports_timeout, *, kind: str, render) -> dict:
    """One probe on a worker while the page animates — the house rule."""
    from yeaboi.settings.engine import verify_connection
    from yeaboi.ui.shared._music_bar import duck_working_thread

    out: dict = {}

    def _target() -> None:
        try:
            out["value"] = verify_connection(kind, {})
        except Exception as exc:  # noqa: BLE001 — a traceback here prints through the Live
            logger.warning("catalog: verify %s failed: %s", kind, exc)
            out["value"] = {"ok": False, "message": str(exc)}

    thread = duck_working_thread(_target, name="catalog-verify")
    thread.start()
    while thread.is_alive():
        frame_started = time.monotonic()
        render()
        if supports_timeout:
            read_key(timeout=frame_time)
        spent = time.monotonic() - frame_started
        if spent < frame_time:
            time.sleep(frame_time - spent)
    thread.join()
    return out.get("value") or {"ok": False, "message": "verification did not answer"}


def run_catalog_browser_standalone(console: Console | None = None) -> str | None:
    """Run the browser in a Live of its own — for callers outside mode select.

    The setup wizard's hand-off lands here: by the time it offers the catalog it
    is back in plain-console land, so this owns the screen for the browse and
    hands it back after.
    """
    import inspect

    from yeaboi.connectors.engine import list_connections
    from yeaboi.ui.shared._input import read_key
    from yeaboi.ui.shared._music_bar import make_live

    console = console or Console()
    supports_timeout = "timeout" in inspect.signature(read_key).parameters
    frame_time = 1 / 30
    w, h = console.size
    seed = build_catalog_screen(
        list_connections(connected_only=False, include_legacy=True), 0, width=w, height=max(10, h - 1)
    )
    with make_live(seed, console=console, refresh_per_second=30, screen=True) as live:
        return run_catalog_browser(console, live, read_key, frame_time, supports_timeout)


def run_catalog_browser(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> str | None:
    """Browse the whole catalog and connect from it. Returns a status line, or None.

    The connect flow mirrors ``yeaboi connections add``: the auth method first
    (it decides which fields are even asked), then only that method's fields,
    each persisted through ``apply_config_value`` so masking and the 0600 file
    apply, then an inline verify.
    """
    import os

    from yeaboi.config import apply_config_value
    from yeaboi.connectors import registry
    from yeaboi.connectors.engine import list_connections

    payload = list_connections(connected_only=False, include_legacy=True)
    selected, scroll, filter_text, filtering = 0, 0, "", False
    message = ""
    logger.info("catalog: browser opened (%d entries)", len(payload["connectors"]))

    def _render(entry: dict | None = None) -> None:
        w, h = console.size
        live.update(
            build_catalog_screen(
                payload,
                selected,
                filter_text=filter_text,
                filtering=filtering,
                width=w,
                height=max(10, h - 1),
                scroll_offset=scroll,
                message=message,
                entry=entry,
            )
        )

    def _connect(row: dict) -> str | None:
        """The add flow for one connector row. Returns a status line, or None on Esc."""
        nonlocal payload
        connector = registry.by_key(row["key"])
        if connector is None:
            return None
        entry: dict = {"row": row, "stage": "method", "methods": list(connector.auth_methods), "method_idx": 0}
        entry.update({"fields": [], "field_idx": 0, "typed": "", "notice": "", "saved": []})

        method = connector.default_method
        if connector.auth_methods:
            recommended = next((i for i, m in enumerate(entry["methods"]) if m.recommended), 0)
            entry["method_idx"] = recommended
            while True:
                _render(entry)
                k = read_key()
                if k == "esc":
                    return None
                if k == "up":
                    entry["method_idx"] = (entry["method_idx"] - 1) % len(entry["methods"])
                elif k in ("down", "tab"):
                    entry["method_idx"] = (entry["method_idx"] + 1) % len(entry["methods"])
                elif k in ("enter", " "):
                    method = entry["methods"][entry["method_idx"]]
                    apply_config_value(connector.auth_env, method.key)
                    os.environ[connector.auth_env] = method.key
                    logger.info("catalog: %s connects via %s", connector.key, method.key)
                    break

        # A sign-in's fields are minted by the flow, never typed here.
        fields = [
            f
            for f in (connector.fields_for(method.key) if method else connector.fields)
            if not (connector.auth_env and f.env == connector.auth_env) and f.action != "signin"
        ]
        entry.update({"stage": "field", "fields": fields})
        set_text_entry(True)
        try:
            i = 0
            while i < len(entry["fields"]):
                field = entry["fields"][i]
                minted = _mint_external_id(field, connector)
                if minted is not None:
                    # Shown once, for pasting into the role's trust policy.
                    entry["saved"].append((f"{field.label} (paste into the trust policy)", minted))
                    i += 1
                    entry["field_idx"] = min(i, len(entry["fields"]) - 1)
                    continue
                entry["field_idx"] = i
                _render(entry)
                k = read_key()
                if k == "esc":
                    return None
                if k == "enter":
                    value = entry["typed"].strip() or field.default
                    if not value:
                        if field.required:
                            entry["notice"] = f"{connector.label} needs {field.label}"
                            continue
                        entry["saved"].append((field.label, "skipped"))
                    else:
                        apply_config_value(field.env, value)
                        os.environ[field.env] = value
                        entry["saved"].append((field.label, "•" * 8 if field.secret else value))
                        logger.info("catalog: saved %s for %s", field.env, connector.key)
                    entry.update({"typed": "", "notice": ""})
                    i += 1
                elif k == "backspace":
                    entry["typed"] = entry["typed"][:-1]
                elif k == "clear":
                    entry["typed"] = ""
                elif isinstance(k, str) and len(k) == 1 and k.isprintable():
                    entry["typed"] += k
        finally:
            set_text_entry(False)

        if not row["verify_kind"]:
            payload = list_connections(connected_only=False, include_legacy=True)
            return f"{connector.label} saved"
        entry["stage"] = "verify"
        outcome = _run_verify(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            kind=row["verify_kind"],
            render=lambda: _render(entry),
        )
        payload = list_connections(connected_only=False, include_legacy=True)
        state = "verified" if outcome.get("ok") else "verify failed"
        # The probe's message is data, never markup — it reaches the screen
        # through Text.append only.
        return f"{connector.label} {state} — {outcome.get('message', '')}"

    while True:
        rows = visible_rows(payload, filter_text)
        selected = min(selected, max(0, len(rows) - 1))
        viewport_h = calc_viewport(max(10, console.size[1] - 1), header_h=6, action_h=4)
        _, heads = _list_lines(rows, selected)
        scroll = _scroll_to(heads, selected, scroll, viewport_h)
        _render()
        k = read_key()
        if filtering:
            if k in ("enter", "esc"):
                if k == "esc":
                    filter_text = ""
                filtering = False
                set_text_entry(False)
            elif k == "backspace":
                filter_text = filter_text[:-1]
            elif isinstance(k, str) and len(k) == 1 and k.isprintable():
                filter_text += k
                selected = 0
            continue
        if k in ("esc", "q"):
            logger.info("catalog: browser closed")
            return message or None
        if k == "/":
            filtering, message = True, ""
            set_text_entry(True)
        elif k == "up":
            selected = max(0, selected - 1)
        elif k in ("down", "tab"):
            selected = min(max(0, len(rows) - 1), selected + 1)
        elif k in ("enter", " ") and rows:
            row = rows[selected]
            if row["managed_by"] == "credentials":
                message = f"{row['label']} is set up under Settings ▸ Credentials or the setup wizard"
                logger.info("catalog: %s pointed at credentials", row["key"])
            else:
                message = _connect(row) or message
