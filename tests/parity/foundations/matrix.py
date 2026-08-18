"""Environment fixture matrix for the W8 foundations parity gate.

Each fixture is one launch environment for ``dump.py`` (and, from W8 phase 3
on, for ``yeaboi __dump-foundations``). Values are templates: ``{tmp}`` is
substituted with the per-run sandbox directory, and the committed goldens
store the template form back, so they are hermetic — independent of where the
sandbox lives on any given machine. ``HOME`` always points inside the sandbox
(``{tmp}/home`` unless a fixture overrides it), so no fixture can read or
write the real user home.

The traps, per the W8 spec: ``~`` expansion (bare and with a tail), pathlib's
lexical normalisation (repeated slashes, ``.`` dropped, ``..`` kept — never
resolved), a relative ``YEABOI_HOME``, ``str.strip()``'s unicode whitespace,
``HOME`` itself needing normalisation, and NOT XDG — ``~/.yeaboi`` plus
``expanduser`` only, no ``$VAR`` expansion anywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
GOLDENS_DIR = HERE.parent / "goldens" / "foundations"
DUMP_SCRIPT = HERE / "dump.py"
TMP_TOKEN = "{tmp}"


@dataclass(frozen=True)
class Fixture:
    """One launch environment. ``env`` overlays the base ``HOME={tmp}/home``;
    ``YEABOI_HOME`` is unset unless a fixture sets it. ``files`` are written
    into the sandbox before the dump runs (paths relative to ``{tmp}``,
    ``/``-separated) — the project ``.env`` lives at ``.env``, the user
    config at ``home/.yeaboi/.env``, the AWS config at ``home/.aws/config``."""

    name: str
    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)


FIXTURES = [
    # The bootstrap default: no YEABOI_HOME, everything under ~/.yeaboi.
    Fixture("default"),
    # The straightforward relocation.
    Fixture("home-absolute", {"YEABOI_HOME": "{tmp}/custom-home"}),
    # ~ with a tail — expanduser against HOME, spaces preserved.
    Fixture("home-tilde", {"YEABOI_HOME": "~/data dir/yeaboi"}),
    # Bare ~ — the root IS the home directory (ENV_FILE still lands in ~/.yeaboi).
    Fixture("home-tilde-bare", {"YEABOI_HOME": "~"}),
    # pathlib drops the trailing slash.
    Fixture("home-trailing-slash", {"YEABOI_HOME": "{tmp}/custom-home/"}),
    # pathlib collapses repeated slashes (a leading "//" would survive; an
    # interior one never does).
    Fixture("home-double-slashes", {"YEABOI_HOME": "{tmp}//custom//deep"}),
    # "." components drop, ".." stays — normalisation is lexical, never resolved.
    Fixture("home-dot-segments", {"YEABOI_HOME": "{tmp}/./custom/../elsewhere"}),
    # A relative YEABOI_HOME stays relative (resolved against the cwd only by
    # the filesystem calls, never in the strings).
    Fixture("home-relative", {"YEABOI_HOME": "rel/yeaboi-home"}),
    # Whitespace-only strips to empty and falls back to the default root.
    Fixture("home-whitespace-only", {"YEABOI_HOME": "   "}),
    # str.strip() strips unicode whitespace (NBSP, em-space), not just ASCII.
    Fixture("home-unicode-whitespace-padding", {"YEABOI_HOME": "\u00a0{tmp}/nbsp-home\u2003"}),
    # Non-ASCII path components pass through untouched.
    Fixture("home-unicode", {"YEABOI_HOME": "{tmp}/données/yeaboi-путь"}),
    # Path.home() normalises too: a trailing slash on HOME itself
    # (posixpath.expanduser rstrips it)...
    Fixture("home-env-trailing-slash", {"HOME": "{tmp}/home2/"}),
    # ...and an interior double slash (pathlib's parse collapses it).
    Fixture("home-env-double-slash", {"HOME": "{tmp}//home3"}),
    # ------------------------------------------------------------------
    # W8 phase 2 — the config surface. The traps, per the spec: the TWO
    # truthy conventions, clamps only after a successful int parse, CSV
    # dedup (and the recipient list that deliberately doesn't), the
    # fallback chains, invalid values falling to defaults, the nasty
    # dotenv corpus, and the AWS-profile autodetect.
    # ------------------------------------------------------------------
    # Everything unset: every getter's default, every fallback's terminus.
    Fixture("config-defaults"),
    # The opt-out gates (`!= "false"`) vs the opt-in flags (`in {1,true,yes,on}`),
    # plus the invalid values that must fall back rather than raise.
    Fixture(
        "config-truthy-and-invalid",
        {
            "TIPS_ENABLED": "  FALSE  ",
            "BETA_NOTICES_ENABLED": "no",  # opt-out gate: only "false" disables
            "DUCK_ENABLED": "False",
            "MUSIC_ENABLED": " TRUE ",
            "MUSIC_CHANNEL": " 7 ",
            "YEABOI_NO_TUNNEL": "Yes",
            "TEAM_ANALYSIS_JIRA_DEV_LINKS": "on",
            "TEAM_ANALYSIS_AZDO_BRANCH_SEARCH": "off",  # opt-in flag: "off" stays false
            "VOICE_INSTALL_OFFER": "Off",
            "VOICE_EXTRA_INSTALLED": "yes",
            "YEABOI_FORCE_VOICE_OFFER": "0",  # falsy force falls through to the offer flag
            "LOG_LEVEL": "verbose",
            "YEABOI_LAST_CATEGORY": "AGENTS",
            "YEABOI_AC_FORMAT": "Given/When/Then",
            "LANGSMITH_TRACING": "TRUE",
            "LANGSMITH_API_KEY": "ls-key",
            "BETA_NOTICES_ACK": " retro , poker ,,retro",
            "YEABOI_FORCE_BETA_NOTICE": "poker, roadmap",
        },
    ),
    # Every clamped knob out of range on one side or the other; whitespace
    # int() tolerates, "5.0" it does not, and clamps never touch a default.
    Fixture(
        "config-int-clamps",
        {
            "TEAM_ANALYSIS_ENRICHMENT_TIMEOUT_SECONDS": "5",
            "TEAM_ANALYSIS_LLM_TARGET_SECONDS": "999999",
            "TEAM_ANALYSIS_LLM_MAX_CONCURRENCY": "  3 ",
            "TEAM_ANALYSIS_DOC_REQUEST_TIMEOUT_SECONDS": "5.0",
            "TEAM_ANALYSIS_DOC_MAX_CONCURRENCY": "-4",
            "TEAM_ANALYSIS_CODE_MAX_CONCURRENCY": "100",
            "TEAM_ANALYSIS_TRACKER_MAX_CONCURRENCY": "0",
            "TEAM_ANALYSIS_MAX_CHANGE_LOOKUPS": "49",
            "TEAM_ANALYSIS_AZDO_PR_SEARCH_MAX_REPOS": "51",
            "TEAM_ANALYSIS_AZDO_PR_SEARCH_PRS_PER_REPO": "5",
            "RETRO_PORT": "70000",  # deliberately unclamped in the product
            "POKER_PORT": "",
            "TUNNEL_TIMEOUT_MINUTES": "-10",
            "STANDUP_SMTP_PORT": "",  # `or "587"`: empty falls to the default
            "SESSION_PRUNE_DAYS": "-5",
            "OLLAMA_NUM_CTX": "lots",
            "MUSIC_CHANNEL": "9.5",
        },
    ),
    # CSV getters dedup order-preservingly; the recipient list keeps dupes.
    Fixture(
        "config-csv-and-lists",
        {
            "ANONYMIZE_MASK_TERMS": " YouLend, YL ,,Acme , YL ",
            "YEABOI_ALLOWED_PATHS": "/a/b, /c ,/a/b,,/d ",
            "TEAM_ANALYSIS_GITHUB_OWNERS": "acme, acme ,octo",
            "STANDUP_EMAIL_RECIPIENTS": " a@b.com ,, c@d.com , a@b.com ",
            "TEAM_ANALYSIS_AZDO_REPO_ALLOWLIST": "Repo-A, repo-b ,REPO-A",
        },
    ),
    # Every cross-getter fallback chain at once, each taking its fallback arm.
    Fixture(
        "config-fallback-chains",
        {
            "JIRA_BASE_URL": "https://acme.atlassian.net",
            "JIRA_EMAIL": "lead@acme.dev",
            "JIRA_API_TOKEN": "jt-1",
            "AZURE_DEVOPS_PROJECT": "Contoso",
            "AZURE_DEVOPS_ORG_URL": "  dev.azure.com/contoso//  ",
            "STANDUP_GITHUB_REPO": "acme/widgets",
            "STANDUP_SMTP_USER": "bot@acme.dev",
            "NOTION_ROOT_PAGE_ID": "root-page-1",
            "CONFLUENCE_SPACE_KEY": "ENG",
            "AWS_DEFAULT_REGION": "eu-west-1",
            "AWS_PROFILE": "explicit-profile",
            "LLM_PROVIDER": "OpenAI",  # lowercased; no OPENAI_API_KEY → not configured
            "OLLAMA_BASE_URL": "http://box:11434///",
        },
    ),
    # Provider states the fallback fixture doesn't reach, and the proxy order.
    Fixture(
        "config-provider-states",
        {
            "LLM_PROVIDER": "ollama",
            "VOICE_MODEL": "small",
            "VOICE_DEVICE": "  Shure MV7  ",
            "https_proxy": "http://lower:3128",
            "all_proxy": "socks5://lower:1080",
            "STANDUP_USER_NAME": "   ",  # whitespace-only falls back to "Me"
            "TEAM_ANALYSIS_FAST_MODEL": " fast-1 ",
            "LLM_MODEL": "",
        },
    ),
    # The nasty project-.env corpus: quoting, escapes, export, comments,
    # interpolation (override=False — the launch env wins), a value-less
    # key, an unparseable line, CRLF. The launch env deliberately collides
    # on JIRA_EMAIL to pin the env-beats-file rule.
    Fixture(
        "config-dotenv-project",
        {"JIRA_EMAIL": "env-wins@acme.dev"},
        files={
            ".env": (
                "# project corpus\n"
                "JIRA_EMAIL=file-loses@acme.dev\n"
                "JIRA_BASE_URL='https://sq.atlassian.net'\n"
                'SLACK_WEBHOOK_URL="https://hooks.example/x\\ny"\n'
                "export STANDUP_GITHUB_REPO=acme/exported\n"
                "STANDUP_USER_NAME=  spaced name   # trailing comment\n"
                "NOTION_TOKEN='it\\'s quoted'\n"
                "VOICE_MODEL\n"
                "=unparseable junk\n"
                "GOOGLE_API_KEY=${JIRA_EMAIL}\n"
                "OPENAI_API_KEY=${MISSING_VAR:-fallback-key}\n"
                "GITHUB_TOKEN=${MISSING_VAR}\n"
                "AZURE_DEVOPS_PROJECT=${ODD:VALUE}\n"
                "STANDUP_SMTP_HOST=smtp.crlf.example\r\n"
                "STANDUP_SMTP_USER=tab\tuser\n"
            ),
        },
    ),
    # Project vs user vs env precedence, and user-file interpolation seeing
    # the project layer through os.environ (override=False lookup order).
    Fixture(
        "config-dotenv-user-precedence",
        {"CONFLUENCE_SPACE_KEY": "ENV-SPACE"},
        files={
            ".env": "LLM_MODEL=from-project\nJIRA_EMAIL=proj@acme.dev\n",
            "home/.yeaboi/.env": (
                "LLM_MODEL=from-user\n"
                "JIRA_BASE_URL='https://user.atlassian.net'\n"
                "JIRA_EMAIL=user@acme.dev\n"
                "CONFLUENCE_SPACE_KEY=USER-SPACE\n"
                "OPENAI_API_KEY=sk-user\n"
                "STANDUP_SMTP_SENDER=${JIRA_EMAIL:-none}\n"
            ),
        },
    ),
    # ~/.aws/config autodetect: the first `[profile ...]` with a role_arn or
    # credential_source wins; [default] and plain profiles don't count.
    Fixture(
        "config-aws-profile-autodetect",
        {"LLM_PROVIDER": "bedrock"},
        files={
            "home/.aws/config": (
                "# global comment\n"
                "[default]\n"
                "region = us-east-1\n"
                "\n"
                "[profile plain]\n"
                "region = eu-west-1\n"
                "\n"
                "[profile  assumed ]\n"
                "role_arn = arn:aws:iam::123456789012:role/dev\n"
                "source_profile = plain\n"
                "\n"
                "[profile second]\n"
                "credential_source = Ec2InstanceMetadata\n"
            ),
        },
    ),
    # An unparseable AWS config (duplicate section) must mean "no profile",
    # never a crash — config.py's bare except is the contract.
    Fixture(
        "config-aws-profile-invalid",
        {"LLM_PROVIDER": "bedrock"},
        files={
            "home/.aws/config": ("[profile dup]\nrole_arn = arn:x\n[profile dup]\nrole_arn = arn:y\n"),
        },
    ),
]

# Every environment variable yeaboi.config reads (the proxy getters read
# both case conventions), plus python-dotenv's own kill switch. launch_env
# strips these from the inherited environment so a developer's real keys,
# proxies and preferences can never leak into a dump — for the config
# getters the fixture's ``env``/``files`` are the *whole* environment. The
# freeze test scans config.py's source and fails when this list and the
# module's os.getenv reads diverge.
CONFIG_ENV_VARS = (
    "ALL_PROXY",
    "ANONYMIZE_MASK_TERMS",
    "ANTHROPIC_API_KEY",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_REGION",
    "AZURE_DEVOPS_ORG_URL",
    "AZURE_DEVOPS_PROJECT",
    "AZURE_DEVOPS_TEAM",
    "AZURE_DEVOPS_TOKEN",
    "BETA_NOTICES_ACK",
    "BETA_NOTICES_ENABLED",
    "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_BASE_URL",
    "CONFLUENCE_EMAIL",
    "CONFLUENCE_EXPORT_PARENT_PAGE_ID",
    "CONFLUENCE_SPACE_KEY",
    "DUCK_ENABLED",
    "GITHUB_TOKEN",
    "GOOGLE_API_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "JIRA_API_TOKEN",
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_PROJECT_KEY",
    "LANGSMITH_API_KEY",
    "LANGSMITH_TRACING",
    "LLM_MODEL",
    "LLM_PROVIDER",
    "LOG_LEVEL",
    "MUSIC_CHANNEL",
    "MUSIC_ENABLED",
    "NOTION_EXPORT_PARENT_PAGE_ID",
    "NOTION_ROOT_PAGE_ID",
    "NOTION_TOKEN",
    "OLLAMA_BASE_URL",
    "OLLAMA_NUM_CTX",
    "OPENAI_API_KEY",
    "PERFORMANCE_FRAMEWORK_PATH",
    "POKER_PORT",
    "PYTHON_DOTENV_DISABLED",
    "RETRO_PORT",
    "SESSION_PRUNE_DAYS",
    "SLACK_WEBHOOK_URL",
    "STANDUP_EMAIL_RECIPIENTS",
    "STANDUP_GITHUB_REPO",
    "STANDUP_SMTP_HOST",
    "STANDUP_SMTP_PASSWORD",
    "STANDUP_SMTP_PORT",
    "STANDUP_SMTP_SENDER",
    "STANDUP_SMTP_USER",
    "STANDUP_USER_NAME",
    "TEAM_ANALYSIS_AZDO_PROJECTS",
    "TEAM_ANALYSIS_AZDO_PR_SEARCH_MAX_REPOS",
    "TEAM_ANALYSIS_AZDO_PR_SEARCH_PRS_PER_REPO",
    "TEAM_ANALYSIS_AZDO_REPO_ALLOWLIST",
    "TEAM_ANALYSIS_CODE_MAX_CONCURRENCY",
    "TEAM_ANALYSIS_CONFLUENCE_SPACES",
    "TEAM_ANALYSIS_GITHUB_OWNERS",
    "TEAM_ANALYSIS_NOTION_ROOTS",
    "TEAM_ANALYSIS_DOC_MAX_CONCURRENCY",
    "TEAM_ANALYSIS_DOC_REQUEST_TIMEOUT_SECONDS",
    "TEAM_ANALYSIS_ENRICHMENT_TIMEOUT_SECONDS",
    "TEAM_ANALYSIS_FAST_MODEL",
    "TEAM_ANALYSIS_JIRA_DEV_LINKS",
    "TEAM_ANALYSIS_AZDO_BRANCH_SEARCH",
    "TEAM_ANALYSIS_LLM_MAX_CONCURRENCY",
    "TEAM_ANALYSIS_LLM_TARGET_SECONDS",
    "TEAM_ANALYSIS_MAX_CHANGE_LOOKUPS",
    "TEAM_ANALYSIS_TRACKER_MAX_CONCURRENCY",
    "TIPS_ENABLED",
    "TUNNEL_TIMEOUT_MINUTES",
    "VOICE_DEVICE",
    "VOICE_EXTRA_INSTALLED",
    "VOICE_INSTALL_OFFER",
    "VOICE_MODEL",
    "YEABOI_AC_FORMAT",
    "YEABOI_ALLOWED_PATHS",
    "YEABOI_FORCE_BETA_NOTICE",
    "YEABOI_FORCE_VOICE_OFFER",
    "YEABOI_LAST_CATEGORY",
    "YEABOI_NO_TUNNEL",
    "all_proxy",
    "http_proxy",
    "https_proxy",
)


def template_env(fixture: Fixture) -> dict[str, str]:
    """The fixture's full launch-environment template (base + overlay)."""
    return {"HOME": "{tmp}/home", **fixture.env}


def realized_env(fixture: Fixture, tmp: Path) -> dict[str, str]:
    """The template with ``{tmp}`` substituted for this run's sandbox."""
    return {k: v.replace(TMP_TOKEN, str(tmp)) for k, v in template_env(fixture).items()}


def launch_env(fixture: Fixture, tmp: Path) -> dict[str, str]:
    """A full subprocess environment: the parent's, minus any real
    YEABOI_HOME/HOME leakage and minus every config-read variable (see
    CONFIG_ENV_VARS — a developer's real credentials, proxies and
    preferences must never reach a dump), plus the fixture's realized
    variables."""
    env = dict(os.environ)
    env.pop("YEABOI_HOME", None)
    env.pop("HOME", None)
    for name in CONFIG_ENV_VARS:
        env.pop(name, None)
    env.update(realized_env(fixture, tmp))
    return env


def write_files(fixture: Fixture, tmp: Path) -> None:
    """Materialise the fixture's files inside the sandbox."""
    for rel, content in fixture.files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def run_dump(fixture: Fixture, tmp: Path) -> dict:
    """Run dump.py in a fresh interpreter under the fixture's environment.

    ``cwd=tmp`` so a relative-root fixture's mkdirs land in the sandbox — the
    Go golden test chdirs the same way before calling the helpers. The
    realized HOME is created up front: config's mkdirs are non-recursive
    (``get_config_dir`` mirrors a home directory that always exists), and
    the Go golden test pre-creates it the same way.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    env = launch_env(fixture, tmp)
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    write_files(fixture, tmp)
    out = subprocess.run(
        [sys.executable, str(DUMP_SCRIPT)],
        cwd=tmp,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def normalize(dump: dict, tmp: Path) -> dict:
    """Substitute this run's sandbox path back to ``{tmp}`` throughout."""
    return json.loads(json.dumps(dump, ensure_ascii=False).replace(str(tmp), TMP_TOKEN))


def golden_for(fixture: Fixture, tmp: Path) -> dict:
    """What the committed golden must contain: the template env and files
    (so the Go side can rebuild the fixture without importing this file) +
    the dump."""
    return {
        "env": template_env(fixture),
        "files": dict(fixture.files),
        "dump": normalize(run_dump(fixture, tmp), tmp),
    }


def golden_path(fixture: Fixture) -> Path:
    return GOLDENS_DIR / f"{fixture.name}.json"


def render_golden(golden: dict) -> str:
    return json.dumps(golden, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
