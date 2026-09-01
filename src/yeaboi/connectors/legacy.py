"""The built-in integrations, as catalog entries.

Display-only descriptors for the eight integrations that predate the connector
layer. They reuse :class:`~yeaboi.connectors.spec.Connector` so every surface
shapes their rows the same way, but they are deliberately NOT in
``registry._CONNECTORS``: their settings fields, verify wiring and secret
masking still live where they always have (``settings/engine.py``,
``config.py``), and deriving both halves at once is the migration the engine's
own comments defer. An entry here says *what exists and whether it is
connected* — configuring it stays a Credentials/setup job, which is what the
``managed_by`` wire key tells a surface.

Deleting an entry from here as its vendor graduates into ``_CONNECTORS`` is the
intended direction of travel.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from yeaboi.connectors.spec import Connector, ConnectorField

#: Legacy entries whose key is also a ``verify_connection`` kind. Slack and
#: Azure DevOps have no probe on that wire today.
LEGACY_VERIFY_KINDS: frozenset[str] = frozenset({"github", "jira", "confluence", "notion", "elevenlabs", "tavus"})

#: Keys where ANY one of the named envs counts as connected — Slack posts with
#: a webhook or reads with a bot token, and either alone is a working channel.
_ANY_OF: dict[str, tuple[str, ...]] = {"slack": ("SLACK_WEBHOOK_URL", "SLACK_BOT_TOKEN")}

LEGACY: tuple[Connector, ...] = (
    Connector(
        key="github",
        label="GitHub",
        family="code",
        section="github",
        summary="Repos, issues and READMEs, grounding planning and powering Ship mode",
        detail=(
            "yeaboi reads repositories, issues and READMEs to ground planning in "
            "the code you actually have, and Ship mode opens pull requests you "
            "approve. Set up under Settings ▸ Credentials or the setup wizard."
        ),
        docs_url="https://github.com/settings/tokens",
        glyph="\U0001f419",  # 🐙 — the octocat
        accent="rgb(110,118,129)",
        fields=(
            ConnectorField(
                env="GITHUB_TOKEN",
                label="Personal Access Token",
                secret=True,
                help_url="https://github.com/settings/tokens",
                help_scope="'repo' scope + 'Configure SSO' if the org uses SSO (fine-grained: org-approved)",
            ),
        ),
    ),
    Connector(
        key="jira",
        label="Jira",
        family="delivery",
        section="jira",
        summary="The tracker sprint plans sync to — epics, stories and sprints",
        detail=(
            "yeaboi reads your board to ground planning and writes the epics, "
            "stories and sprints an approved sprint plan creates. Set up under "
            "Settings ▸ Credentials or the setup wizard."
        ),
        docs_url="https://id.atlassian.com/manage-profile/security/api-tokens",
        glyph="\U0001f3ab",  # 🎫 — the ticket
        accent="rgb(38,132,255)",
        fields=(
            ConnectorField(env="JIRA_BASE_URL", label="Base URL", placeholder="https://your-org.atlassian.net"),
            ConnectorField(env="JIRA_EMAIL", label="Email"),
            ConnectorField(
                env="JIRA_API_TOKEN",
                label="API Token",
                secret=True,
                help_url="https://id.atlassian.com/manage-profile/security/api-tokens",
                help_scope=(
                    "Token inherits your Jira role — account needs browse project, create/edit issues, manage sprints"
                ),
            ),
        ),
    ),
    Connector(
        key="azdevops",
        label="Azure DevOps Boards",
        family="delivery",
        section="azure",
        summary="The tracker sprint plans sync to — epics, stories and iterations",
        detail=(
            "yeaboi reads your board to ground planning and writes the epics, "
            "stories and iterations an approved sprint plan creates. Set up under "
            "Settings ▸ Credentials or the setup wizard."
        ),
        docs_url="https://dev.azure.com",
        glyph="\U0001f4cc",  # 📌 — boards
        accent="rgb(0,90,158)",
        fields=(
            ConnectorField(env="AZURE_DEVOPS_ORG_URL", label="Org URL", placeholder="https://dev.azure.com/your-org"),
            ConnectorField(
                env="AZURE_DEVOPS_TOKEN",
                label="Personal Access Token",
                secret=True,
                help_url="https://dev.azure.com",
                help_scope="Work Items Read & Write · Project and Team Read · Code Read",
            ),
        ),
    ),
    Connector(
        key="confluence",
        label="Confluence",
        family="docs",
        section="jira",
        summary="Docs export into the space your team already reads",
        detail=(
            "yeaboi publishes the documents you approve into one Confluence "
            "space, reusing your Jira Atlassian identity when its own is not "
            "set. Set up under Settings ▸ Credentials or the setup wizard."
        ),
        docs_url="https://id.atlassian.com/manage-profile/security/api-tokens",
        glyph="\U0001f30a",  # 🌊 — the confluence
        accent="rgb(76,154,255)",
        fields=(
            ConnectorField(
                env="CONFLUENCE_BASE_URL",
                label="Base URL",
                fallback_env="JIRA_BASE_URL",
                placeholder="https://your-org.atlassian.net",
            ),
            ConnectorField(env="CONFLUENCE_EMAIL", label="Email", fallback_env="JIRA_EMAIL"),
            ConnectorField(
                env="CONFLUENCE_API_TOKEN",
                label="API Token",
                secret=True,
                fallback_env="JIRA_API_TOKEN",
                help_url="https://id.atlassian.com/manage-profile/security/api-tokens",
                help_scope="Account needs view + create/update pages (and attachments) in the target space",
            ),
            ConnectorField(env="CONFLUENCE_SPACE_KEY", label="Space Key", placeholder="TEAM"),
        ),
    ),
    Connector(
        key="notion",
        label="Notion",
        family="docs",
        section="notion",
        summary="Docs search and export against your workspace",
        detail=(
            "yeaboi searches and reads the pages you share with its integration "
            "and publishes the documents you approve. Set up under Settings ▸ "
            "Credentials or the setup wizard."
        ),
        docs_url="https://notion.so/my-integrations",
        glyph="\U0001f5d2️",  # 🗒️ — the page
        accent="rgb(55,53,47)",
        fields=(
            ConnectorField(
                env="NOTION_TOKEN",
                label="Integration Token",
                secret=True,
                help_url="https://notion.so/my-integrations",
                help_scope="Capabilities: Read + Insert + Update content — then share your pages with the integration",
            ),
        ),
    ),
    Connector(
        key="slack",
        label="Slack",
        family="chat",
        section="slack",
        summary="Where ceremonies deliver — standups, retros and reports in a channel",
        detail=(
            "yeaboi posts ceremony output to one channel through a webhook, and "
            "a bot token additionally lets it read replies to its own anchors. "
            "Either credential alone is a working channel. Set up under "
            "Settings ▸ Credentials."
        ),
        docs_url="https://api.slack.com/apps",
        glyph="✳️",  # the pinwheel logo shape
        accent="rgb(74,21,75)",
        fields=(
            ConnectorField(env="SLACK_WEBHOOK_URL", label="Webhook URL", secret=True, required=False),
            ConnectorField(
                env="SLACK_BOT_TOKEN",
                label="Bot Token",
                secret=True,
                required=False,
                help_url="https://api.slack.com/apps",
                help_scope="Bot: chat:write · channels:history · reactions:read · users:read — then /invite it",
            ),
        ),
        connected_when=("SLACK_WEBHOOK_URL",),  # see _ANY_OF — either credential counts
    ),
    Connector(
        key="elevenlabs",
        label="ElevenLabs",
        family="media",
        section="voice",
        summary="The duck's spoken voice in ceremonies and calls",
        detail=(
            "yeaboi sends only the lines it is about to speak for synthesis, and "
            "nothing else it knows. Set up under Settings ▸ System ▸ Voice."
        ),
        docs_url="https://elevenlabs.io/app/settings/api-keys",
        glyph="\U0001f5e3️",  # 🗣️ — spoken voice
        accent="rgb(28,28,28)",
        fields=(
            ConnectorField(
                env="ELEVENLABS_API_KEY",
                label="API Key",
                secret=True,
                help_url="https://elevenlabs.io/app/settings/api-keys",
                help_scope="A default key works — restrict to 'Text to Speech' if scoping",
            ),
        ),
    ),
    Connector(
        key="tavus",
        label="Tavus",
        family="media",
        section="voice",
        summary="Avatar video for desktop calls",
        detail=(
            "yeaboi requests avatar video for the calls you start and sends only "
            "the lines being spoken. Set up under Settings ▸ System ▸ Voice."
        ),
        docs_url="https://platform.tavus.io",
        glyph="\U0001f3ac",  # 🎬 — avatar video
        accent="rgb(255,79,100)",
        fields=(
            ConnectorField(
                env="TAVUS_API_KEY",
                label="API Key",
                secret=True,
                help_url="https://platform.tavus.io",
                help_scope="API key from the Tavus portal — powers avatar video in desktop calls",
            ),
        ),
    ),
)


def by_key(key: str) -> Connector | None:
    return next((c for c in LEGACY if c.key == key), None)


def all_envs() -> tuple[str, ...]:
    """Every env a legacy entry reads, in descriptor order."""
    return tuple(f.env for c in LEGACY for f in c.fields)


def is_connected(entry: Connector, values: Mapping[str, str] | None = None) -> bool:
    """Whether a legacy entry's credentials are present.

    Unlike ``registry.is_connected`` this honours ``fallback_env`` (Confluence
    reads Jira's Atlassian identity) and the one either-credential rule
    (:data:`_ANY_OF`) — both facts the legacy config getters already implement,
    restated over the same env vars.
    """
    read = os.environ if values is None else values

    def present(env: str) -> bool:
        return bool(str(read.get(env, "") or "").strip())

    any_of = _ANY_OF.get(entry.key)
    if any_of:
        return any(present(env) for env in any_of)

    fallbacks = {f.env: f.fallback_env for f in entry.fields}
    required = tuple(f.env for f in entry.fields if f.required)
    return all(present(env) or (fallbacks.get(env) and present(fallbacks[env])) for env in required)
