"""Configuration data for provider selection screens.

# See docs: "Architecture" — constants for the setup wizard UI.
# Defines LLM providers, version control options, and issue tracking fields.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Token help — where to create each credential token and the minimum scope it
# needs. Single source of truth shared by the setup wizard (provider_select
# screens) and the read-only Settings dashboard (mode_select), so the two token
# surfaces stay consistent. Keyed by the token's env var.
#
# Scope text mirrors the per-token comments in .env.example and README.md — keep
# them in sync. The scopes are the minimum the tools/*.py integrations actually
# exercise (e.g. Azure DevOps creates work items, so it needs Work Items R/W).
# ---------------------------------------------------------------------------
TOKEN_HELP: dict[str, dict[str, str]] = {
    "GITHUB_TOKEN": {
        "url": "https://github.com/settings/tokens",
        "scope": "Fine-grained: Contents Read · Issues Read · Metadata Read "
        "(classic: public_repo, or repo for private)",
    },
    "AZURE_DEVOPS_TOKEN": {
        "url": "https://dev.azure.com",
        "scope": "Work Items Read & Write · Project and Team Read · Code Read",
    },
    "JIRA_API_TOKEN": {
        "url": "https://id.atlassian.com/manage-profile/security/api-tokens",
        "scope": "Token inherits your Jira role — account needs browse project, create/edit issues, manage sprints",
    },
    "NOTION_TOKEN": {
        "url": "https://notion.so/my-integrations",
        "scope": "Capabilities: Read + Insert + Update content — then share your pages with the integration",
    },
    "CONFLUENCE_API_TOKEN": {
        "url": "https://id.atlassian.com/manage-profile/security/api-tokens",
        "scope": "Account needs view + create/update pages (and attachments) in the target space",
    },
}

# ---------------------------------------------------------------------------
# Provider definitions (order matters — matches row layout top-to-bottom)
# ---------------------------------------------------------------------------

# Per-provider model presets shown in the model-selection step. Each card's
# models["default"] MUST equal agent/llm.py::_PROVIDER_DEFAULTS[provider_val]
# (a unit test asserts this) so the wizard pre-selects the same model the app
# falls back to when LLM_MODEL is unset. The on-screen list is always
# presets + ["Custom\u2026"]; the "Custom\u2026" entry lets users type any newer model
# id, validated live against their credentials.
_PROVIDER_CARDS: list[dict[str, Any]] = [
    {
        "name": "Anthropic",
        "full_name": "Anthropic (Claude)",
        "env_var": "ANTHROPIC_API_KEY",
        "provider_val": "anthropic",
        "prefix": "sk-ant-",
        "tagline": "Recommended cloud \u00b7 API key required",
        "instructions": "Get yours at: https://console.anthropic.com \u2192 API Keys",
        "color": "rgb(70,100,180)",
        "models": {
            "default": "claude-sonnet-4-6",
            "presets": [
                "claude-sonnet-4-6",
                "claude-opus-4-8",
                "claude-opus-4-6",
                "claude-haiku-4-5",
            ],
        },
    },
    {
        "name": "Gemini",
        "full_name": "Google (Gemini)",
        "env_var": "GOOGLE_API_KEY",
        "provider_val": "google",
        "prefix": "AIza",
        "tagline": "Fast, affordable cloud \u00b7 API key required",
        "instructions": "Get yours at: https://aistudio.google.com \u2192 Get API key",
        "color": "rgb(70,100,180)",
        "models": {
            "default": "gemini-2.5-flash",
            "presets": [
                "gemini-2.5-flash",
                "gemini-3.5-flash",
                "gemini-2.5-pro",
                "gemini-2.5-flash-lite",
            ],
        },
    },
    {
        "name": "OpenAI",
        "full_name": "OpenAI (GPT)",
        "env_var": "OPENAI_API_KEY",
        "provider_val": "openai",
        "prefix": "sk-",
        "tagline": "Cloud \u00b7 API key required",
        "instructions": "Get yours at: https://platform.openai.com \u2192 API keys",
        "color": "rgb(70,100,180)",
        "models": {
            "default": "gpt-4o",
            "presets": [
                "gpt-4o",
                "gpt-4o-mini",
                "gpt-5.6",
                "gpt-5.5",
            ],
        },
    },
    {
        "name": "Bedrock",
        "full_name": "AWS (Bedrock)",
        "env_var": "AWS_REGION",
        "provider_val": "bedrock",
        "prefix": "",
        "tagline": "AWS-hosted · IAM credentials, no API key",
        "instructions": "Uses IAM credentials from instance role, ~/.aws/credentials, or env vars",
        "color": "rgb(70,100,180)",
        "is_region_input": True,
        # Bedrock's real model id is usually auto-detected from OpenClaw and
        # prepended at runtime; the default here keeps parity with _PROVIDER_DEFAULTS.
        "models": {
            "default": "us.anthropic.claude-sonnet-4-6-v1:0",
            "presets": [
                "us.anthropic.claude-sonnet-4-6-v1:0",
            ],
        },
    },
    {
        "name": "Ollama",
        "full_name": "Ollama (Local)",
        "env_var": "OLLAMA_BASE_URL",
        "provider_val": "ollama",
        "prefix": "",
        "tagline": "Free · local · private · no API key",
        "instructions": "Free, private, no API key. Install: https://ollama.com then run: ollama pull qwen3:8b",
        "color": "rgb(70,100,180)",
        # Like Bedrock's is_region_input: the "key" field holds a URL, not a
        # secret — rendered unmasked and pre-filled with default_input.
        "is_base_url_input": True,
        "default_input": "http://localhost:11434",
        "models": {
            "default": "qwen3:8b",
            "presets": [
                "qwen3:8b",
                "qwen3:14b",
                "qwen2.5:14b",
                "llama3.1:8b",
            ],
        },
        # One-line trade-off shown next to each known model in the model list —
        # local models differ hugely in RAM needs and JSON discipline, so the
        # user should pick with eyes open. See README: "Local Mode (Ollama)".
        # Every hint carries the same ~download-size · RAM suffix so no model
        # can be picked without a size warning.
        "model_hints": {
            "qwen3:8b": "best balance · ~5 GB · fine on 16 GB RAM",
            "qwen3:14b": "better plans · ~9 GB · wants 24 GB+ RAM",
            "qwen2.5:14b": "proven JSON discipline · ~9 GB · wants 24 GB+ RAM",
            "llama3.1:8b": "weaker JSON → more fallbacks · ~5 GB · fine on 16 GB RAM",
        },
    },
]

# Version control providers — GitHub only (Azure DevOps PAT is collected
# in the Issue Tracking step alongside org URL and project name).
_VC_OPTIONS: list[dict[str, Any]] = [
    {
        "name": "GitHub",
        "env_var": "GITHUB_TOKEN",
        "prefix": "ghp_",
        "instructions": "Get yours at: https://github.com/settings/tokens",
        "color": "rgb(70,100,180)",
    },
    {
        "name": "Skip",
        "env_var": "",
        "prefix": "",
        "instructions": "",
        "color": "rgb(70,100,180)",
    },
]

# Issue tracking fields — step 4 (Jira)
# Each field carries a "hint": a one-line "where to get it" note shown under the
# focused field, mirroring the LLM/GitHub steps' "Get yours at: …" line.
_ISSUE_TRACKING_FIELDS: list[dict[str, Any]] = [
    {
        "env_var": "JIRA_BASE_URL",
        "label": "Jira Base URL",
        "placeholder": "https://org.atlassian.net",
        "masked": False,
        "required": True,
        "hint": "Your Atlassian site — https://<your-org>.atlassian.net",
    },
    {
        "env_var": "JIRA_EMAIL",
        "label": "Jira Email",
        "placeholder": "you@company.com",
        "masked": False,
        "required": True,
        "hint": "The email you sign in to Atlassian with",
    },
    {
        "env_var": "JIRA_API_TOKEN",
        "label": "Jira API Token",
        "placeholder": "",
        "masked": True,
        "required": True,
        "hint": "Create at: id.atlassian.com/manage-profile/security/api-tokens",
    },
    {
        "env_var": "JIRA_PROJECT_KEY",
        "label": "Project Key",
        "placeholder": "MYPROJ",
        "masked": False,
        "required": True,
        "hint": "The prefix on issue keys — e.g. MYPROJ in MYPROJ-123",
    },
]

# Issue tracking fields — Azure DevOps Boards
_AZDEVOPS_TRACKING_FIELDS: list[dict[str, Any]] = [
    {
        "env_var": "AZURE_DEVOPS_ORG_URL",
        "label": "Organization URL",
        "placeholder": "https://dev.azure.com/myorg",
        "masked": False,
        "required": True,
        "hint": "Your org — https://dev.azure.com/<your-org>",
    },
    {
        "env_var": "AZURE_DEVOPS_PROJECT",
        "label": "Project Name",
        "placeholder": "MyProject",
        "masked": False,
        "required": True,
        "hint": "The project name as it appears in the Azure DevOps URL",
    },
    {
        "env_var": "AZURE_DEVOPS_TOKEN",
        "label": "Personal Access Token",
        "placeholder": "",
        "masked": True,
        "required": True,
        "hint": "Create at: dev.azure.com → User settings → Personal access tokens",
    },
    {
        "env_var": "AZURE_DEVOPS_TEAM",
        "label": "Team Name",
        "placeholder": "MyProject Team",
        "masked": False,
        "required": False,
        "hint": "Optional — defaults to '<Project> Team' if left blank",
    },
]

# Issue tracking provider options — user picks one before seeing fields
_ISSUE_TRACKING_OPTIONS: list[dict[str, Any]] = [
    {"name": "Jira", "fields": _ISSUE_TRACKING_FIELDS},
    {"name": "Azure DevOps Boards", "fields": _AZDEVOPS_TRACKING_FIELDS},
    {"name": "Skip", "fields": []},
]

# ── Docs step (Notion + Confluence) ──────────────────────────────────────────
# Both doc tools live under the "Docs" progress chip (_STEPS[2]). Notion has its
# own integration token; Confluence rides on Jira's Atlassian auth (JIRA_BASE_URL/
# EMAIL/API_TOKEN — see tools/confluence.py) and only adds a space key, so its
# sub-step is only offered when Jira was configured in the Issue Tracking step.

# Notion doc-tool fields. Unlike Confluence, Notion has its own integration token
# and no "space key"; the optional root page/database ID scopes page creation and
# the standup activity feed. Both fields are optional so users without Notion skip past.
_NOTION_FIELDS: list[dict[str, Any]] = [
    {
        "env_var": "NOTION_TOKEN",
        "label": "Notion Integration Token",
        "placeholder": "ntn_… / secret_…",
        "masked": True,
        "required": False,
        "hint": "Create at: notion.so/my-integrations, then share your pages with it",
    },
    {
        "env_var": "NOTION_ROOT_PAGE_ID",
        "label": "Root Page/Database ID (optional)",
        "placeholder": "",
        "masked": False,
        "required": False,
        "hint": "The 32-char id at the end of the page's Notion URL",
    },
    {
        # Optional dedicated exports page — reports created by the Export
        # buttons nest under it as subpages; blank falls back to the Root Page.
        "env_var": "NOTION_EXPORT_PARENT_PAGE_ID",
        "label": "Exports Page ID",
        "placeholder": "",
        "masked": False,
        "required": False,
        "hint": "Reports publish under this page — blank uses the Root Page",
    },
]

# Confluence export-destination field, appended to both Confluence forms below.
# Exports publish into CONFLUENCE_SPACE_KEY; this optional page nests them as
# subpages — blank creates them at the space root.
_CONFLUENCE_EXPORT_FIELDS: list[dict[str, Any]] = [
    {
        "env_var": "CONFLUENCE_EXPORT_PARENT_PAGE_ID",
        "label": "Exports Page ID",
        "placeholder": "",
        "masked": False,
        "required": False,
        "hint": "Reports nest under this page — blank creates them at the space root",
    },
]

# Confluence fields — the Docs step's second sub-tool. Confluence reuses the Jira
# Atlassian credentials (JIRA_BASE_URL/EMAIL/API_TOKEN) collected in the Issue
# Tracking step, so the only field here is the space key. The sub-step is gated on
# Jira being configured (see _phase_confluence / the wizard flow in __init__.py).
_CONFLUENCE_FIELDS: list[dict[str, Any]] = [
    {
        "env_var": "CONFLUENCE_SPACE_KEY",
        "label": "Confluence Space Key",
        "placeholder": "MYSPACE",
        "masked": False,
        "required": False,
        "hint": "The <KEY> in your space URL /wiki/spaces/<KEY> — exports publish here; reuses your Jira login",
    },
    *_CONFLUENCE_EXPORT_FIELDS,
]

# Standalone Confluence fields — used when Jira was NOT configured in the Issue
# Tracking step, so the shared Atlassian creds aren't available to reuse. Confluence
# Cloud shares Atlassian identity with Jira; these CONFLUENCE_* vars win over JIRA_*
# in config.get_confluence_* (with a Jira fallback), so a Confluence-only setup works
# without touching the Jira tools. The space key stays optional (search spans all
# accessible spaces when blank); the login fields are required to build a client.
_CONFLUENCE_STANDALONE_FIELDS: list[dict[str, Any]] = [
    {
        "env_var": "CONFLUENCE_BASE_URL",
        "label": "Atlassian Base URL",
        "placeholder": "https://your-org.atlassian.net",
        "masked": False,
        "required": True,
        "hint": "Your Atlassian site — https://<your-org>.atlassian.net (no trailing slash)",
    },
    {
        "env_var": "CONFLUENCE_EMAIL",
        "label": "Atlassian Email",
        "placeholder": "you@example.com",
        "masked": False,
        "required": True,
        "hint": "The Atlassian account email used for API basic auth",
    },
    {
        "env_var": "CONFLUENCE_API_TOKEN",
        "label": "Atlassian API Token",
        "placeholder": "",
        "masked": True,
        "required": True,
        "hint": "Create at: id.atlassian.com → Security → API tokens",
    },
    {
        # The shared form renderer appends "(optional)" for non-required fields, so the
        # label stays bare (mirrors _CONFLUENCE_FIELDS' space-key label).
        "env_var": "CONFLUENCE_SPACE_KEY",
        "label": "Confluence Space Key",
        "placeholder": "MYSPACE",
        "masked": False,
        "required": False,
        "hint": "The <KEY> in your space URL /wiki/spaces/<KEY> — exports publish here; blank searches all spaces",
    },
    *_CONFLUENCE_EXPORT_FIELDS,
]
