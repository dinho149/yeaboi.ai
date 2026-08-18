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

W8 phase 5 extends it to the logfile surface (``yeaboi.logging_setup`` +
``yeaboi.redaction``):

- ``logfile.configured_level`` / ``logfile.apply_level`` — level resolution
- ``logfile.redact`` / ``logfile.log_safe`` — [input, output] vector pairs
  (the value-based layer reads the fixture's env, so outputs differ by
  fixture on purpose)
- ``logfile.format`` — ``RedactingFormatter`` lines under pinned record
  times (TZ=UTC comes from the launch env)
- ``logfile.files`` / ``modes`` / ``registry`` — the scripted registry +
  rotation scenario's on-disk outcome under the sandbox's logs dir

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

# ---------------------------------------------------------------------------
# W8 phase 5 — the logfile surface (logging_setup.py + redaction.py).
# ---------------------------------------------------------------------------

# apply_level resolves via getattr(logging, level.upper(), WARNING) — the
# vectors pin the aliases the logging module happens to export (WARN, FATAL,
# NOTSET), the invalid names that must fall back, and the fact that nothing
# strips whitespace on the way in. Keep every vector resolving to an int:
# a name that resolves to a non-level module attribute would crash setLevel
# in the product too, so it is not a behaviour to pin here.
LOG_LEVEL_VECTORS = [
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "debug",
    "Error",
    "warn",
    "fatal",
    "notset",
    "VERBOSE",
    "",
    " warning ",
]

# redact() inputs, exercising every token-pattern family, the value-based
# layer (via env vars each fixture sets), the unicode \s and \w semantics
# the Go port must reproduce under RE2, and the positional preference of
# Python's single-alternation scan (the ftp:// vector: the ghp_ token and
# the URL-credential pattern both match at the same offset, and the token
# wins because it is listed first).
LOG_REDACT_VECTORS = [
    "plain text with no secrets at all, port 8080 true",
    "anthropic sk-ant-api03-AbCdEf123456 trailing",
    "unicode tail sk-ant-abcé2345678é9 done",
    "openai sk-abcdefghijklmnopqrst123 x",
    "github ghp_ABCDEFGHIJKLMNOPQRST clean",
    "fine ghp_short7 stays",
    "pat github_pat_11ABCDEFGHIJKLMNOPQRSTUV done",
    "slack xoxb-1234567890-abc token",
    "google AIzaSyA-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa end",
    "aws AKIAIOSFODNN7EXAMPLE key",
    "atlassian ATATT3xFfGF0T-abc+def/ghi=jkl end",
    "notion ntn_abcdefghijklmnopqrst123 page",
    "notion secret_abcdefghijklmnopqrstuvwxyz1234 page",
    "webhook hooks.slack.com/services/T0AA/B0BB/curly done",
    "webhook at https://hooks.slack.com/services/T0AA/B0BB/curly end",
    "auth Bearer abcdef1234567890XYZ sent",
    "auth bEaReR\u00a0abcdef1234567890XYZ sent",  # NBSP separator — Python \\s is unicode-aware
    "auth Basic dGVzdDp0ZXN0cGFzcw== ok",
    "auth Bearer short1 ok",
    "url https://svc:AKCp8fffffff@nexus.corp/simple pinned",
    "url https://svc:pw@nexus.corp/simple short-password",
    "url ftp://ghp_ABCDEFGHIJKLMNOPQRST:pw12@x positional-preference",
    "no scheme svc:longpassword@host untouched",
    "value hush-hush-value-123 and hush-hush-value-123-extended overlap",
    "value fallback-key from the project env",
    "mail env-wins@acme.dev interpolated into GOOGLE_API_KEY",
    "tiny short-secret value stays: tiny",
]

# log_safe() inputs: newline/CR/tab collapse, control-char removal, the
# 200-code-point truncation (sliced by characters, never bytes — hence the
# multi-byte run), and the fact that log_safe does NOT redact.
LOG_SAFE_VECTORS = [
    "clean value",
    "crlf\r\ninjected\tline",
    "controls \x00\x01 kept? \x7f end",
    "vertical\x0btab\x0cformfeed",
    "nbsp\u00a0stays",  # NBSP is not collapsed and not a control char — it survives
    "x" * 200,
    "y" * 201,
    "é" * 230 + "tail",
    "secret ghp_ABCDEFGHIJKLMNOPQRST inside",
]

# RedactingFormatter vectors: levelname %-7s padding (CRITICAL overflows it,
# unpadded), dotted logger names, redaction of the assembled line, and a
# literal %s that must survive (args is None, so no interpolation).
LOG_FORMAT_VECTORS = [
    {"name": "yeaboi.cli", "level": "INFO", "msg": "startup complete", "created": 1755500000},
    {"name": "yeaboi.agent.llm", "level": "DEBUG", "msg": "prompt cached", "created": 1755500001},
    {"name": "yeaboi.tools.github", "level": "WARNING", "msg": "retrying", "created": 1755503661},
    {
        "name": "yeaboi.retro",
        "level": "ERROR",
        "msg": "boom with ghp_ABCDEFGHIJKLMNOPQRST token",
        "created": 1755589199,
    },
    {"name": "yeaboi", "level": "CRITICAL", "msg": "unicode café \U0001f680 %s literal", "created": 1755589200},
]

# The rotation corpus: maxBytes=192 makes every semantic visible in a few
# lines — CPython's rollover check compares stream.tell() (bytes) plus
# len(formatted + "\n") (characters), so the é-run drifts the two apart;
# the 300-char line forces roll-then-write-oversize; nine records cycle the
# backups past backupCount so the oldest file drops.
LOG_ROTATION_MAX_BYTES = 192
LOG_ROTATION_MSGS = [
    "first line of the rotation corpus",
    "second line, a bit longer than the first one is",
    "unicode " + "é" * 29 + " run",
    "fourth line arrives after the unicode run",
    "fifth line pushes the byte count over",
    "token ghp_ABCDEFGHIJKLMNOPQRST rides along",
    "x" * 300,
    "small after the oversize one",
    "closing line of the rotation corpus",
]

_LOG_TS = 1755500000  # 2026-08-18 06:53:20 UTC — every scenario timestamp offsets from here


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


def _logfile_dump(paths) -> dict:
    """W8 phase 5: dump the logging_setup + redaction surface.

    Everything here is deterministic under the fixture env: record times are
    pinned (TZ=UTC comes from the launch env), the registry scenario and the
    rotation corpus write real files inside the sandbox, and the walk at the
    end freezes their names, contents and permission bits. The
    ``logging_setup`` module's own logger is disabled first — its
    attach/apply debug lines carry wall-clock timestamps, which would break
    the golden under a DEBUG fixture.
    """
    import logging
    import time

    from yeaboi import logging_setup
    from yeaboi.config import restrict_permissions
    from yeaboi.redaction import RedactingFormatter, log_safe, redact

    time.tzset()  # the launch env pins TZ=UTC; make sure strftime saw it
    logging.getLogger("yeaboi.logging_setup").disabled = True

    out = {
        "configured_level": logging_setup._level(),
        "apply_level": {v: getattr(logging, v.upper(), logging.WARNING) for v in LOG_LEVEL_VECTORS},
        "redact": [[v, redact(v)] for v in LOG_REDACT_VECTORS],
        "log_safe": [[v, log_safe(v)] for v in LOG_SAFE_VECTORS],
    }

    def record(name: str, level: int, msg: str, created: int) -> logging.LogRecord:
        rec = logging.LogRecord(name, level, "dump.py", 0, msg, None, None)
        rec.created = created
        rec.msecs = 0.0
        return rec

    formatter = RedactingFormatter(logging_setup.LOG_FORMAT, datefmt=logging_setup.DATE_FORMAT)
    out["format"] = [
        formatter.format(record(v["name"], getattr(logging, v["level"]), v["msg"], v["created"]))
        for v in LOG_FORMAT_VECTORS
    ]

    # --- rotation corpus: a small-maxBytes handler, driven directly -------
    import os

    rot_dir = paths.LOGS_DIR / "rotation"
    rot_dir.mkdir(parents=True, exist_ok=True)
    restrict_permissions(rot_dir, mode=0o700)
    rot = logging_setup._SecureRotatingFileHandler(
        rot_dir / "rot.log", maxBytes=LOG_ROTATION_MAX_BYTES, backupCount=3, encoding="utf-8"
    )
    rot.setFormatter(RedactingFormatter(logging_setup.LOG_FORMAT, datefmt=logging_setup.DATE_FORMAT))
    for i, msg in enumerate(LOG_ROTATION_MSGS):
        rot.emit(record("yeaboi.rot", logging.INFO, msg, _LOG_TS + 100 + i))
    rot.flush()
    rot.close()

    # --- registry scenario: the public logging_setup API, scripted --------
    def emit(name: str, level: int, msg: str, offset: int) -> None:
        lg = logging.getLogger("yeaboi")
        if lg.isEnabledFor(level):
            lg.handle(record(name, level, msg, _LOG_TS + offset))

    logging_setup.configure_logging()
    emit("yeaboi.cli", logging.INFO, "startup complete", 0)
    emit("yeaboi.agent.llm", logging.DEBUG, "prompt tokens: 512", 1)
    emit("yeaboi.tools.github", logging.ERROR, "auth failed: token ghp_abcdefghij0123456789 rejected", 2)
    logging_setup.attach_mode_handler("retro")
    logging_setup.attach_mode_handler("retro")  # idempotent page re-entry
    emit("yeaboi.retro.engine", logging.WARNING, "card parse fallback used", 3)
    logging_setup.attach_session_log("sess-alpha")
    emit("yeaboi.agent.nodes", logging.ERROR, "plan node failed", 4)
    logging_setup.attach_session_log("sess-beta")  # replaces sess-alpha
    emit("yeaboi.agent.nodes", logging.CRITICAL, "graph aborted", 5)
    logging_setup.apply_level("debug")
    emit("yeaboi.agent.llm", logging.DEBUG, "cache hit", 6)
    # _attach resets the namespace logger (and only the new handler) to the
    # env level — the quirk the next two emits pin.
    logging_setup.attach_mode_handler("poker")
    emit("yeaboi.poker.engine", logging.DEBUG, "vote recorded", 7)
    emit("yeaboi.poker.engine", logging.ERROR, "sync failed", 8)
    logging_setup.detach("retro")
    emit("yeaboi.cli", logging.WARNING, "shutting down", 9)
    emit("yeaboi.tools.notion", logging.ERROR, "post failed for token " + os.environ.get("NOTION_TOKEN", "<unset>"), 10)
    out["registry"] = sorted(logging_setup._handlers)
    for key in list(logging_setup._handlers):
        logging_setup.detach(key)

    files: dict[str, str] = {}
    modes: dict[str, str] = {}
    for p in sorted(paths.LOGS_DIR.rglob("*")):
        if p.is_file():
            rel = p.relative_to(paths.LOGS_DIR).as_posix()
            files[rel] = p.read_text(encoding="utf-8")
            modes[rel] = oct(stat.S_IMODE(p.stat().st_mode))
    # Only the dirs the scenario itself hardened — everything else's mode is
    # umask-dependent and would make the golden machine-specific.
    for rel in ("tui", "retro", "poker", "planning", "rotation"):
        modes[rel + "/"] = oct(stat.S_IMODE((paths.LOGS_DIR / rel).stat().st_mode))
    out["files"] = files
    out["modes"] = modes
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
    dump["logfile"] = _logfile_dump(paths)
    return dump


if __name__ == "__main__":
    json.dump(build_dump(), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
