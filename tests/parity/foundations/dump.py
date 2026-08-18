"""Python-side dumper for the W8 foundations parity gate.

Run as a subprocess — one process per fixture, because ``yeaboi.paths``
resolves its root at import time, which is exactly the behaviour the gate
pins (the Go side resolves once per process; the equivalence only holds if
each fixture gets a fresh import). Prints one canonical JSON document of
every ``paths`` module constant and helper under the environment it was
launched with:

- ``constants`` — every module-level path constant, by its Python name
- ``helpers`` — every zero-argument getter (with its mkdir/permission
  side effects, exercised inside the sandbox the caller pointed HOME at)
- ``keyed_helpers`` — every getter taking a key, over ``KEYED_SAMPLES``
- ``safe_key`` — ``_safe_key`` over ``SAFE_KEY_VECTORS`` (fallback ``"fb"``)

``matrix.py`` builds the launch environment; ``go/internal/home``'s golden
test replays the committed result against the Go port, key by key. W8
phase 3 adds the ``yeaboi __dump-foundations`` twin of this script.

W8 phase 2 extends the dump to ``yeaboi.config``:

- ``config`` — every zero-argument config getter, as JSON-native values
  (tuples become lists, sets become sorted lists, the one raising getter
  becomes ``null``)
- ``config_keyed`` — the key-taking config getters over sample keys
- ``set_key`` — the dotenv writer scenarios: each applies ``set_key`` ops to
  a scratch file and records the resulting text (plus, for the
  ``set_config_value`` choke point, the file's post-write permission bits)

One pinned normalisation: ``build_dump`` sets ``sys.frozen`` before
importing ``yeaboi.config``, which makes python-dotenv's ``find_dotenv()``
walk up from the *working directory* (the sandbox) instead of from the
calling file's directory. That is exactly how python-dotenv behaves in a
frozen executable — which is what ``cmd/yeaboi`` is — and it matches
``fs_policy.py:98``, which already defines the project .env as
``Path.cwd() / ".env"``. It also keeps this gate hermetic: without it, a
developer checkout with a real ``.env`` at the repo root would leak into
every regenerated golden.
"""

from __future__ import annotations

import json
import stat
import sys

# Deliberate traps, mirrored from the W8 spec: unicode-aware lower/strip
# (Turkish İ, NBSP), backslash-to-slash normalisation, ".." that must never
# escape an export root, "..." which is a legitimate name, separators
# collapsing to "-", and emptiness falling back.
SAFE_KEY_VECTORS = [
    "Team/Alpha",
    "İstanbul CI",
    "  padded  ",
    "a\\b\\c",
    "../../../etc/passwd",
    "a//b",
    "",
    "...",
    "PROJ.1",
    ".",
    "a/./b/../c",
    "🚀 Launch/Q3",
    "\u00a0nbsp\u00a0",  # NBSP padding — str.strip() is unicode-aware
    "MiXeD CASE",
    "trailing/",
]

# Two keys per keyed getter: one that exercises _safe_key's separator + case
# + unicode handling, and the empty key that lands on each getter's own
# fallback name ("project" / "engineer" / "report" / "roadmap" / "output" /
# "misc").
KEYED_SAMPLES = ["Team/Alpha", "İZMİR \\ Ops", ""]

CONSTANT_NAMES = [
    "DEFAULT_ROOT_DIR",
    "ROOT_DIR",
    "LEGACY_ROOT_DIR",
    "DATA_DIR",
    "DB_PATH",
    "STATES_DIR",
    "PROJECTS_FILE",
    "REPORTING_THEMES_FILE",
    "REPORTING_PREFS_FILE",
    "VOICE_INSTALL_FILE",
    "LEGACY_DB_PATH",
    "LEGACY_STATES_DIR",
    "LEGACY_PROJECTS_FILE",
    "EXPORTS_DIR",
    "ANALYSIS_EXPORTS_DIR",
    "PLANNING_EXPORTS_DIR",
    "STANDUP_EXPORTS_DIR",
    "RETRO_EXPORTS_DIR",
    "POKER_EXPORTS_DIR",
    "PERFORMANCE_EXPORTS_DIR",
    "REPORTING_EXPORTS_DIR",
    "ROADMAP_EXPORTS_DIR",
    "ANONYMIZE_EXPORTS_DIR",
    "AGENTWATCH_EXPORTS_DIR",
    "SHIP_DIR",
    "SHIP_WORKTREES_DIR",
    "SHIP_WORKTREE_REGISTRY",
    "SHIP_BUDGET_FILE",
    "SHIP_BUDGET_LOCK",
    "SHIP_BUDGET_RECEIPTS",
    "LOGS_DIR",
    "TUI_LOGS_DIR",
    "STANDUP_LOGS_DIR",
    "RETRO_LOGS_DIR",
    "POKER_LOGS_DIR",
    "PERFORMANCE_LOGS_DIR",
    "REPORTING_LOGS_DIR",
    "ROADMAP_LOGS_DIR",
    "ANALYSIS_LOGS_DIR",
    "PLANNING_LOGS_DIR",
    "MCP_LOGS_DIR",
    "AGENTWATCH_LOGS_DIR",
    "SHIP_LOGS_DIR",
    "CEREMONIES_LOGS_DIR",
    "LEGACY_TUI_LOG",
    "SCRUM_DOCS_DIR",
    "ENV_FILE",
    "REPL_HISTORY",
    "BIN_DIR",
    "ATTACHMENTS_DIR",
    "TRANSCRIPTS_DIR",
]

ZERO_ARG_HELPERS = [
    "get_db_path",
    "get_reporting_themes_path",
    "get_reporting_prefs_path",
    "get_voice_install_path",
    "get_tui_log_path",
    "get_analysis_log_dir",
    "get_planning_log_dir",
    "get_standup_log_dir",
    "get_retro_log_dir",
    "get_poker_log_dir",
    "get_performance_log_dir",
    "get_reporting_log_dir",
    "get_roadmap_log_dir",
    "get_mcp_log_dir",
    "get_agentwatch_log_dir",
    "get_ceremonies_log_dir",
    "get_ship_log_dir",
    "get_ship_dir",
    "get_bin_dir",
    "get_transcripts_dir",
]

# Every zero-argument public getter in yeaboi.config, in the module's own
# order. The parity freeze test discovers config's callables and fails when
# this list and the module diverge (setters and the named side-effect
# helpers excepted), so a new getter cannot land without joining the dump.
CONFIG_GETTERS = [
    "get_config_dir",
    "get_config_file",
    "get_sessions_db",
    "get_anthropic_api_key",
    "is_langsmith_enabled",
    "is_tips_enabled",
    "is_beta_notice_enabled",
    "get_last_category",
    "is_duck_enabled",
    "is_music_enabled",
    "get_music_channel",
    "beta_notices_acked",
    "is_voice_install_offer_enabled",
    "voice_extra_was_installed",
    "detect_proxy",
    "get_github_token",
    "get_azure_devops_token",
    "get_azure_devops_org_url",
    "get_azure_devops_project",
    "get_azure_devops_team",
    "get_jira_base_url",
    "get_jira_email",
    "get_jira_token",
    "get_jira_project_key",
    "get_ac_format",
    "get_confluence_base_url",
    "get_confluence_email",
    "get_confluence_token",
    "get_confluence_space_key",
    "get_anonymize_mask_terms",
    "get_notion_token",
    "get_notion_root_page_id",
    "get_data_dir",
    "get_allowed_paths",
    "get_notion_export_parent_page_id",
    "get_confluence_export_parent_page_id",
    "get_standup_github_repo",
    "get_team_analysis_github_owners",
    "get_team_analysis_azdo_projects",
    "get_team_analysis_confluence_spaces",
    "get_team_analysis_notion_roots",
    "get_team_analysis_enrichment_timeout_seconds",
    "get_team_analysis_fast_model",
    "get_team_analysis_llm_target_seconds",
    "get_team_analysis_llm_max_concurrency",
    "get_team_analysis_doc_request_timeout_seconds",
    "get_team_analysis_doc_max_concurrency",
    "get_team_analysis_code_max_concurrency",
    "get_team_analysis_tracker_max_concurrency",
    "get_team_analysis_max_change_lookups",
    "get_retro_server_port",
    "get_poker_server_port",
    "tunnels_disabled",
    "get_tunnel_timeout_minutes",
    "get_slack_webhook_url",
    "get_smtp_host",
    "get_smtp_port",
    "get_smtp_user",
    "get_smtp_password",
    "get_smtp_sender",
    "get_standup_email_recipients",
    "get_standup_user_name",
    "get_performance_framework_path",
    "get_llm_provider",
    "get_llm_model",
    "get_bedrock_region",
    "get_aws_profile",
    "get_openai_api_key",
    "get_google_api_key",
    "get_ollama_base_url",
    "get_ollama_num_ctx",
    "is_llm_configured",
    "get_voice_model",
    "get_voice_device",
    "get_session_prune_days",
    "get_log_level",
    "is_team_analysis_jira_dev_links_enabled",
    "is_team_analysis_azdo_pr_search_enabled",
    "get_team_analysis_azdo_pr_search_max_repos",
    "get_team_analysis_azdo_pr_search_top",
    "get_team_analysis_azdo_repo_allowlist",
]

# Key-taking config getters, with keys that hit both branches of the
# YEABOI_FORCE_BETA_NOTICE override and the acked-set membership.
CONFIG_KEYED_GETTERS = {
    "is_beta_notice_seen": ["retro", "poker", "roadmap", ""],
}

# Zero-argument config callables the dump deliberately skips.
CONFIG_DUMP_EXEMPT = {
    "load_user_config": "side effect only — the dump itself calls it before reading getters",
    "disable_langsmith_tracing": "mutates os.environ and returns nothing",
    "mark_voice_extra_installed": "setter (writes .env); the write path is pinned by SET_KEY_SCENARIOS",
}

# dotenv-writer scenarios: each starts from `initial` (None = missing file),
# applies set_key ops in order, and records the file's final text. The
# `via_config` scenario routes through config.set_config_value — the choke
# point every setter shares — and records the 0600 hardening too.
SET_KEY_SCENARIOS = [
    {"name": "create-missing", "initial": None, "ops": [["FOO", "bar"]]},
    {
        "name": "replace-preserving",
        "initial": "# comment\nFOO=old\nOTHER=1\nexport FOO=older\n=junk\nBARE\n",
        "ops": [["FOO", "new"]],
    },
    {"name": "append-after-newline-less-tail", "initial": "A=1", "ops": [["B", "2"]]},
    {
        "name": "quote-escaping",
        "initial": None,
        "ops": [["Q", "it's got 'quotes'"], ["NL", "line1\nline2"], ["UNI", "café 🚀"], ["EMPTY", ""]],
    },
    {"name": "replace-then-append", "initial": "KEEP='x'\n", "ops": [["KEEP", "y"], ["NEW", "z"]]},
]

KEYED_HELPERS = [
    "get_analysis_export_dir",
    "get_planning_export_dir",
    "get_standup_export_dir",
    "get_retro_export_dir",
    "get_poker_export_dir",
    "get_performance_export_dir",
    "get_reporting_export_dir",
    "get_roadmap_export_dir",
    "get_anonymize_export_dir",
    "get_agentwatch_export_dir",
    "get_attachments_dir",
]


def _jsonable(value):
    """Config getters return Paths, tuples, sets and (bool, str) pairs; the
    golden holds JSON, so everything canonicalises (sets sorted)."""
    if isinstance(value, frozenset | set):
        return sorted(value)
    if isinstance(value, tuple | list):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, str | bool | int | float):
        return value
    return str(value)  # pathlib.Path


def _config_dump(config) -> dict:
    out = {}
    for name in CONFIG_GETTERS:
        try:
            value = getattr(config, name)()
        except OSError:
            # get_anthropic_api_key is the one getter that raises when its
            # key is missing; the Go twin returns an error → null here.
            value = None
        out[name] = _jsonable(value)
    return out


def _config_keyed_dump(config) -> dict:
    return {
        name: {key: _jsonable(getattr(config, name)(key)) for key in keys}
        for name, keys in CONFIG_KEYED_GETTERS.items()
    }


def _set_key_dump(config) -> dict:
    """Run SET_KEY_SCENARIOS against scratch files, plus the
    set_config_value choke point against the real (sandboxed) config file —
    which appends to whatever the fixture's user .env already holds, so the
    rewrite path is exercised over each fixture's corpus too."""
    from pathlib import Path

    from dotenv import set_key

    scratch = Path.cwd() / "setkey-scratch"
    scratch.mkdir(exist_ok=True)
    out = {}
    for scenario in SET_KEY_SCENARIOS:
        path = scratch / f"{scenario['name']}.env"
        if scenario["initial"] is not None:
            path.write_text(scenario["initial"], encoding="utf-8")
        for key, value in scenario["ops"]:
            set_key(str(path), key, value)
        # initial + ops ride along so the Go golden test replays without
        # importing this file (the same reason the golden carries env/files).
        out[scenario["name"]] = {
            "initial": scenario["initial"],
            "ops": [list(op) for op in scenario["ops"]],
            "text": path.read_text(encoding="utf-8"),
        }

    ops = [["YEABOI_SET_KEY_PROBE", "via config"]]
    for key, value in ops:
        config_file = config.set_config_value(key, value)
    out["via-config-choke-point"] = {
        "ops": ops,
        "text": config_file.read_text(encoding="utf-8"),
        "mode": oct(stat.S_IMODE(config_file.stat().st_mode)),
    }
    return out


def build_dump() -> dict:
    """Import yeaboi.paths (resolving the root from this process's env) and
    yeaboi.config (loading the sandbox's project .env, then its user .env)
    and dump both surfaces."""
    # Frozen semantics for find_dotenv — see the module docstring. Set before
    # anything imports yeaboi.config, and the *paths helpers* already do:
    # get_db_path lazily imports config.restrict_permissions, which fires
    # config's module-level load_dotenv() mid-helper-dump.
    sys.frozen = True  # type: ignore[attr-defined]

    import yeaboi.paths as paths

    dump = {
        "constants": {name: str(getattr(paths, name)) for name in CONSTANT_NAMES},
        "helpers": {name: str(getattr(paths, name)()) for name in ZERO_ARG_HELPERS},
        "keyed_helpers": {
            name: {key: str(getattr(paths, name)(key)) for key in KEYED_SAMPLES} for name in KEYED_HELPERS
        },
        "safe_key": {key: paths._safe_key(key, "fb") for key in SAFE_KEY_VECTORS},
    }

    import yeaboi.config as config

    config.load_user_config()
    dump["config"] = _config_dump(config)
    dump["config_keyed"] = _config_keyed_dump(config)
    dump["set_key"] = _set_key_dump(config)
    return dump


if __name__ == "__main__":
    json.dump(build_dump(), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
