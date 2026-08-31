"""What one integration *is*, as data.

Every fact a surface needs about a connector lives on the descriptor: which env
vars it reads, which of them are secret, how it verifies, which settings section
it renders under, and the glyph/accent it wears. The registries that used to
hold those facts separately — the settings fields, the verify table, the secret
lists, the TUI section builders — derive from here instead of restating it.

Stdlib-only at import time: ``settings/engine.py`` imports this at module scope,
and that module is on the startup path for every surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: An accent in the same notation ``_MODE_CARDS`` already uses, so the terminal
#: has one colour vocabulary rather than two.
ACCENT_RE = re.compile(r"^rgb\((\d{1,3}),(\d{1,3}),(\d{1,3})\)$")

#: The families a connector can belong to, and the glyph one falls back to when
#: a vendor mark cannot be shipped. The terminal ALWAYS uses the family glyph —
#: a logo is not a thing a terminal can draw.
FAMILIES: dict[str, str] = {
    "observability": "\U0001f4c8",  # 📈
    "incidents": "\U0001f6a8",  # 🚨
    "errors": "\U0001f41e",  # 🐞
    "cloud": "☁️",  # ☁️
    "delivery": "\U0001f4cb",  # 📋
    "docs": "\U0001f4d8",  # 📘
    "code": "\U0001f500",  # 🔀
    "chat": "\U0001f4ac",  # 💬
    "media": "\U0001f3a4",  # 🎤
}

#: Render order for the catalog. Families the user is most likely to be adding
#: come first; the four legacy families trail, because they are already set up.
FAMILY_ORDER: tuple[str, ...] = (
    "observability",
    "incidents",
    "errors",
    "cloud",
    "delivery",
    "code",
    "docs",
    "chat",
    "media",
)

FAMILY_LABELS: dict[str, str] = {
    "observability": "Observability",
    "incidents": "Incidents & on-call",
    "errors": "Error tracking",
    "cloud": "Cloud",
    "delivery": "Delivery tracking",
    "code": "Code",
    "docs": "Docs",
    "chat": "Chat",
    "media": "Voice & video",
}


@dataclass(frozen=True)
class AuthMethod:
    """One way to authenticate to a connector, and how honest it can be.

    Cloud providers are the reason this exists: "connected" is not one set of
    credentials there, and the difference between the ways in is a difference in
    what yeaboi can promise. A method that yeaboi cannot bound says so on itself
    rather than in documentation nobody reads at the moment of choosing.
    """

    key: str
    label: str
    #: One line: what this method actually is.
    summary: str
    recommended: bool = False
    #: Why this one is not recommended. Required on every method that is not.
    warning: str = ""
    #: Where the least-privilege snippet lives.
    setup_url: str = ""
    #: Which of the connector's fields this method needs. The method selector
    #: itself is never listed — it is required whatever is chosen.
    envs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorField:
    """One env var a connector reads, and how a surface should treat it."""

    env: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    hint: str = ""
    # Where the user creates this credential, and what scope it needs. Absorbs
    # ui/provider_select/_constants.py::TOKEN_HELP.
    help_url: str = ""
    help_scope: str = ""
    # The name this field takes in a verify_connection() request. Empty means
    # the field is configuration the probe does not need.
    verify_arg: str = ""
    # The name this field takes when the probe reads it from the SAVED value
    # rather than the request. Any field that determines a host belongs here:
    # pairing a caller-supplied host with a stored token exfiltrates it.
    env_arg: str = ""
    # Where the value comes from when this env is unset — Confluence reading
    # Jira's Atlassian identity, declared rather than special-cased.
    fallback_env: str = ""
    # Which auth method this field belongs to. Empty means every method needs
    # it — a region, a project id, the method selector itself.
    auth_method: str = ""
    choices: tuple[str, ...] = ()
    default: str = ""


@dataclass(frozen=True)
class Connector:
    """One integration, as every surface sees it.

    ``key`` is the identity (and the ``verify_connection`` kind). ``section`` is
    the settings-section string it renders under, and is deliberately separate:
    Azure DevOps is keyed ``azdevops`` but has always rendered under ``azure``,
    and that section name is mirrored into the desktop's route manifest.
    """

    key: str
    label: str
    family: str
    section: str
    fields: tuple[ConnectorField, ...]
    #: One line, shown wherever the connector is listed: what yeaboi reads and
    #: what that feeds. "Observability" names a category, not a reason.
    summary: str = ""
    #: The paragraph behind it — what is read, what is never read, what changes
    #: once it is connected. Shown when the catalog entry is opened.
    detail: str = ""
    #: ``provider_verification`` function name; empty when nothing can be probed.
    verify: str = ""
    #: The name of a module-level ``fetch(window_start, window_end)`` in this
    #: connector's own module, returning ``tuple[OpsEvent, ...]``. Empty when
    #: the connector can be verified but has nothing to gather yet.
    fetch: str = ""
    #: Where that function lives, when it is not ``yeaboi.connectors.<key>`` —
    #: user-defined connectors share one generic driver module rather than
    #: shipping code, and this is the seam that reaches it.
    fetch_module: str = ""
    docs_url: str = ""
    #: Envs that must ALL be set for the connector to count as connected.
    #: Empty means "every required field".
    connected_when: tuple[str, ...] = ()
    #: Terminal identity. ``glyph`` defaults to the family mark.
    glyph: str = ""
    accent: str = ""
    #: The ways in, when there is more than one. Empty means the connector has
    #: exactly one and needs no chooser.
    auth_methods: tuple[AuthMethod, ...] = ()
    #: The env holding the chosen method's key. An ordinary required choice
    #: field, and required on purpose: choosing IS the configuration, so a
    #: connector whose recommended method needs no credential of its own cannot
    #: report itself connected on a machine that has set nothing.
    auth_env: str = ""
    #: Read-only connectors gather data and never write to the vendor.
    read_only: bool = True

    @property
    def mark(self) -> str:
        """The glyph to draw — the connector's own, else its family's."""
        return self.glyph or FAMILIES.get(self.family, "")

    @property
    def required_envs(self) -> tuple[str, ...]:
        """The envs that decide whether this connector is connected."""
        if self.connected_when:
            return self.connected_when
        return tuple(f.env for f in self.fields if f.required)

    @property
    def secret_envs(self) -> tuple[str, ...]:
        return tuple(f.env for f in self.fields if f.secret)

    def method(self, key: str) -> AuthMethod | None:
        return next((m for m in self.auth_methods if m.key == key), None)

    @property
    def default_method(self) -> AuthMethod | None:
        """The method a surface offers first — the recommended one."""
        if not self.auth_methods:
            return None
        return next((m for m in self.auth_methods if m.recommended), self.auth_methods[0])

    def fields_for(self, method_key: str) -> tuple[ConnectorField, ...]:
        """The fields that matter under one auth method, in descriptor order.

        A field with no ``auth_method`` belongs to all of them. Deciding WHICH
        method is a caller's job — this file reads no environment.
        """
        if not self.auth_methods:
            return self.fields
        return tuple(f for f in self.fields if not f.auth_method or f.auth_method == method_key)

    def envs_for(self, method_key: str) -> tuple[str, ...]:
        """The envs that decide connectedness under one auth method."""
        if not self.auth_methods:
            return self.required_envs
        return tuple(f.env for f in self.fields_for(method_key) if f.required)
