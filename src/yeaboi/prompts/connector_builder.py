"""Prompt factory for drafting a custom connection from a plain description.

# See docs: "Prompt Construction" — ARC framework (Action, Requirements, Context)
#
# The model's job is deliberately small: identity (key, label, family), look
# (glyph, accent), and HTTP shape (auth scheme, probe path, optional events
# mapping). It is never asked for env names, verify wiring or credential
# values — those are derived server-side, so a draft cannot aim a stored
# credential anywhere. The runtime validator judges whatever comes back.
"""

from __future__ import annotations

from yeaboi.connectors.spec import FAMILIES
from yeaboi.connectors.validation import AUTH_SCHEMES
from yeaboi.ops.events import EVENT_KINDS


def create_connector_builder_prompt(description: str) -> str:
    """Build the one-shot prompt turning a service description into a draft."""
    families = ", ".join(FAMILIES)
    schemes = ", ".join(AUTH_SCHEMES)
    kinds = ", ".join(EVENT_KINDS)
    return f"""You are configuring a read-only integration for a scrum-master tool.
The user describes a service they want to connect; you propose the connection descriptor.

USER'S DESCRIPTION:
{description[:2000]}

Respond with ONLY a JSON object (no code fences, no commentary) with these keys:
- "key": a slug starting with "custom_" (e.g. "custom_statuspage")
- "label": the service's proper name
- "family": one of: {families}
- "summary": one line (max 90 chars) saying what connecting it gives the user
- "detail": 1-3 sentences on what is read; include what is never read or written
- "docs_url": the https URL of the service's API-token documentation, or ""
- "glyph": one emoji that suits the service
- "accent": the service's brand colour as "rgb(r,g,b)"
- "kind": "api" — or "mcp" when the description is a remote MCP (Model Context Protocol)
  server endpoint rather than a REST API
- "auth_scheme": one of: {schemes}
- "header_name": the auth header's name, ONLY when auth_scheme is "header", else ""
- "probe_path": a cheap authenticated GET path (starting "/") that proves the credential works
- "probe_ok_status": the status that GET returns on success (usually 200)
- "events": null, or — only when the service has a list endpoint whose rows are
  incidents/alerts/errors/deploys — an object with:
  - "path": the GET path of that endpoint (starting "/")
  - "items_key": dot path to the row array inside the response ("" when the body IS the array)
  - "kind": one of: {kinds}
  - "title_path": dot path to a row's name (required)
  - "ref_path", "severity_path", "status_path", "url_path", "started_at_path", "service_path":
    dot paths into a row, "" when the field does not exist

Rules:
- Propose only reads. If the service is write-oriented, set "events" to null.
- Paths are paths, never full URLs, and never contain "..".
- When unsure about the events endpoint, set "events" to null rather than guessing.
- For kind "mcp": keep "auth_scheme" "bearer", "header_name" "", "probe_path" "/",
  "probe_ok_status" 200, and "events" null — the server URL and token are entered
  afterwards as credentials.
"""
