"""Validation and API verification for provider setup.

# See docs: "Architecture" — verification layer for the setup wizard.
# Handles format validation of API keys and live verification via API calls.
"""

from __future__ import annotations

import logging
from typing import Any

from yeaboi.llm_providers import OPENAI_COMPATIBLE

logger = logging.getLogger(__name__)

# Shared Ollama failure copy — the same situation is reachable from both
# _verify_api_key and _verify_model, and the messages must stay identical so
# tests (and users retrying) see one consistent instruction.
_OLLAMA_PKG_MISSING = (
    "Ollama support isn't installed — run: uv sync --extra ollama (or: pip install langchain-ollama), then retry"
)

# The two messages that mean the provider positively *rejected* the credential,
# as named constants so `credential_verdict` cannot drift from the branches that
# produce them. Everything else `_verify_api_key` can return — a timeout, a
# proxy, an unexpected status — means "could not tell", which is a different
# thing and must not be reported to the user as an expired key.
INVALID_KEY = "Invalid API key"
KEY_LACKS_PERMISSIONS = "Key lacks permissions"
_DEFINITE_REJECTIONS = frozenset({INVALID_KEY, KEY_LACKS_PERMISSIONS})

# What an OpenAI-wire vendor answers a bad key with on GET /models. 400 belongs
# here because xAI uses it for "Incorrect API key provided" — and a GET with no
# body has nothing else it could be complaining about.
_WIRE_KEY_REJECTED = frozenset({400, 401, 403})


def _connection_error(exc: Exception) -> str:
    """ "Connection error: …" with any credential scrubbed out of it.

    A transport exception quotes the request it failed on, and Google's carries
    the API key in the URL — so the raw text is a secret-bearing string that
    both the wizard (which renders it) and the credential gate (which renders
    and logs it) would otherwise pass straight through.
    """
    from yeaboi.redaction import redact  # lazy: keep module import-light

    return redact(f"Connection error: {exc}")


def _ollama_unreachable_message() -> str:
    """'Can't reach Ollama' copy that distinguishes not-installed from not-running.

    Reached from both _verify_api_key and _verify_model — keep the branching in
    one place so the two paths stay consistent. Both variants keep the literal
    ``ollama serve`` so "start the server" is always the final step.
    """
    from yeaboi.ollama_control import is_ollama_installed  # lazy: keep module import-light

    if is_ollama_installed():
        return "Ollama is installed but not running — start it with: ollama serve"
    return (
        "Ollama isn't installed — get it at https://ollama.com "
        "(or: brew install ollama), then start it with: ollama serve"
    )


def _validate_key(provider: dict[str, Any], value: str) -> tuple[str, str]:
    """Realtime format validation of an API key (or region for Bedrock).

    Returns (status, hint_message) where status is one of:
    - "empty": no input yet
    - "bad_prefix": wrong prefix
    - "too_short": right prefix but too short
    - "valid_format": passes format checks (needs live verification)
    """
    # Bedrock uses a region name, not an API key
    if provider.get("is_region_input"):
        if not value:
            return "empty", ""
        # Basic region format check: e.g. us-east-1, eu-west-2
        if "-" in value and len(value) >= 7:
            return "valid_format", "Press Enter to verify \u2014 edit region or confirm"
        return "too_short", "Enter an AWS region (e.g. us-east-1, eu-west-2)"

    # Ollama uses a local server URL, not an API key
    if provider.get("is_base_url_input"):
        if not value:
            return "empty", ""
        if value.startswith(("http://", "https://")):
            return "valid_format", "Press Enter to verify \u2014 Ollama must be running"
        return "bad_prefix", "Enter a URL (e.g. http://localhost:11434)"

    prefix = provider["prefix"]
    name = provider["full_name"]

    if not value:
        return "empty", ""

    # Vendors with no prefix to check (Mistral, Z.ai) get a looser floor —
    # there is nothing else to validate on, and a false 'too short' blocks a
    # perfectly good key.
    min_lengths = {"sk-ant-": 40, "sk-": 30, "AIza": 30, "xai-": 40}
    min_len = min_lengths.get(prefix, 20)

    if not value.startswith(prefix):
        return "bad_prefix", f"Expected prefix: {prefix}..."

    if len(value) < min_len:
        return "too_short", f"Too short \u2014 {name} keys are typically {min_len}+ chars"

    return "valid_format", "Format looks good \u2014 press Enter to verify"


def _verify_api_key(provider: dict[str, Any], api_key: str) -> tuple[bool, str]:
    """Make a lightweight API call to verify the key actually works.

    Returns (success, message).
    """
    provider_val = provider["provider_val"]

    try:
        if provider_val == "anthropic":
            import httpx

            # Ping the provider's own default model so this can't drift onto a
            # retired model id (a retired/unknown model returns 404, not 401 —
            # the API checks the key first, then the model).
            verify_model = (provider.get("models") or {}).get("default") or "claude-sonnet-4-6"
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": verify_model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10,
            )
            if resp.status_code in (200, 201):
                return True, "Key verified"
            if resp.status_code == 401:
                return False, INVALID_KEY
            if resp.status_code == 403:
                return False, KEY_LACKS_PERMISSIONS
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "openai":
            import httpx

            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Key verified"
            if resp.status_code == 401:
                return False, INVALID_KEY
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "google":
            import httpx

            resp = httpx.get(
                f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Key verified"
            if resp.status_code in (400, 401, 403):
                return False, INVALID_KEY
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "bedrock":
            # Bedrock verification — api_key is actually the region name.
            # Uses IAM credentials from instance role, ~/.aws/credentials, or env vars.
            # Auto-detects the AWS profile from ~/.aws/config (e.g. Lightsail's
            # [profile assumed] with credential_source=Ec2InstanceMetadata).
            import boto3

            from yeaboi.config import get_aws_profile

            profile = get_aws_profile()
            session = boto3.Session(profile_name=profile, region_name=api_key)
            client = session.client("bedrock", region_name=api_key)
            resp = client.list_foundation_models(byOutputModality="TEXT")
            if resp.get("modelSummaries") is not None:
                return True, "AWS credentials verified"
            return False, "Unexpected response from Bedrock"

        elif provider_val == "ollama":
            # Ollama verification — api_key is the local server base URL.
            # langchain-ollama is an optional extra and everything below uses
            # raw httpx, so without this guard setup would finish green and the
            # first real LLM call would crash with an ImportError.
            import importlib.util

            if importlib.util.find_spec("langchain_ollama") is None:
                return False, _OLLAMA_PKG_MISSING

            # /api/tags lists installed models; a 200 proves the server is up.
            import httpx

            resp = httpx.get(f"{api_key.rstrip('/')}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = resp.json().get("models") or []
                if models:
                    return True, "Ollama server verified"
                return True, "Server verified — no models installed yet; run: ollama pull qwen3:8b"
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val in OPENAI_COMPATIBLE:
            # Every OpenAI-wire vendor answers GET /models behind the same bearer,
            # so one branch verifies all of them against their own base URL.
            status, _ids = _openai_wire_models(provider_val, api_key, timeout=10)
            if status == 200:
                return True, "Key verified"
            if status in _WIRE_KEY_REJECTED:
                return False, INVALID_KEY
            return False, f"Unexpected response: {status}"

    except Exception as e:
        err_str = str(e)
        if provider_val == "ollama":
            return False, _ollama_unreachable_message()
        if "NoCredentialsError" in type(e).__name__ or "NoCredentialsError" in err_str:
            return False, "No AWS credentials found \u2014 configure IAM role, ~/.aws/credentials, or env vars"
        if "InvalidIdentityToken" in err_str or "AccessDenied" in err_str or "403" in err_str:
            return False, "AWS credentials lack Bedrock permissions"
        return False, _connection_error(e)

    return False, "Unknown provider"


def log_category(message: str) -> str:
    """A fixed-vocabulary label for a verification message, safe to log.

    Every branch returns a literal, so the credential cannot reach a log line
    even in principle — the message itself quotes the request it failed on, and
    a value that never carries a secret is a stronger guarantee than one that
    has been scrubbed. The message still reaches the *screen*, redacted, where
    its detail is what makes the failure actionable.
    """
    if message == INVALID_KEY:
        return "invalid key"
    if message == KEY_LACKS_PERMISSIONS:
        return "key lacks permissions"
    if message.startswith("Connection error"):
        return "connection error"
    if message.startswith("Unexpected response"):
        return "unexpected status"
    if "ollama" in message.lower():
        return "ollama unavailable"
    if "AWS" in message:
        return "aws credentials"
    return "verification failed"


def credential_verdict(provider: dict[str, Any], credential: str) -> tuple[str, str]:
    """``(verdict, message)`` where verdict is "ok", "rejected" or "inconclusive".

    The setup wizard only needs pass/fail, because a person is sitting there
    reading the message. A gate that *blocks* on the answer needs the third
    state: "Connection error" covers a captive wifi, a proxy and a DNS failure
    as well as a dead key, and telling someone on a train that their
    credentials expired is worse than saying nothing (the rule
    :func:`yeaboi.auth_state.probe_subscription_token` already follows).

    Ollama is the one provider whose failures are all definite: it is a local
    server that either answers or does not, with no network in between, and
    both of its messages name the fix.
    """
    ok, message = _verify_api_key(provider, credential)
    if ok:
        return "ok", message
    if provider.get("provider_val") == "ollama":
        return "rejected", message
    return ("rejected" if message in _DEFINITE_REJECTIONS else "inconclusive"), message


def _verify_model(provider: dict[str, Any], api_key: str, model: str) -> tuple[bool, str]:
    """Make a lightweight API call to verify the chosen model is usable by the key.

    Mirrors _verify_api_key's structure but exercises the *specific* model so we
    can confirm the user's credentials can actually run it (e.g. a newly released
    model typed via the Custom… entry). For Bedrock, ``api_key`` is the region.

    Returns (success, message).
    """
    provider_val = provider["provider_val"]

    try:
        if provider_val == "anthropic":
            import httpx

            # Cheapest possible ping against the target model. No thinking/sampling
            # params so we never hit model-family parameter constraints.
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10,
            )
            if resp.status_code in (200, 201):
                return True, "Model verified"
            if resp.status_code == 404:
                return False, "Model not found or not available for this key"
            if resp.status_code == 400:
                # A 400 often means the model id is unknown/unavailable — surface detail.
                detail = _extract_error_message(resp)
                return False, detail or "Model not accepted"
            if resp.status_code == 401:
                return False, INVALID_KEY
            if resp.status_code == 403:
                return False, "Key lacks access to this model"
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "openai":
            import httpx

            resp = httpx.get(
                f"https://api.openai.com/v1/models/{model}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Model verified"
            if resp.status_code == 404:
                return False, "Unknown model for this account"
            if resp.status_code == 401:
                return False, INVALID_KEY
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "google":
            import httpx

            # Google model ids are used bare in the path (e.g. gemini-2.0-flash).
            resp = httpx.get(
                f"https://generativelanguage.googleapis.com/v1/models/{model}?key={api_key}",
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Model verified"
            if resp.status_code == 404:
                return False, "Unknown model"
            if resp.status_code in (400, 401, 403):
                return False, INVALID_KEY
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "bedrock":
            # api_key is the AWS region. Inference-profile ids (leading us./eu./
            # global.) are NOT returned by list_foundation_models, so soft-accept
            # those once the region resolves.
            if model.split(".", 1)[0] in ("us", "eu", "global", "apac"):
                return True, "Inference profile accepted (region verified)"

            import boto3

            from yeaboi.config import get_aws_profile

            profile = get_aws_profile()
            session = boto3.Session(profile_name=profile, region_name=api_key)
            client = session.client("bedrock", region_name=api_key)
            resp = client.list_foundation_models(byOutputModality="TEXT")
            model_ids = {m.get("modelId", "") for m in resp.get("modelSummaries") or []}
            if model in model_ids:
                return True, "Model verified"
            return False, "Model not available in this region"

        elif provider_val == "ollama":
            # api_key is the local server base URL. A model is usable iff it's
            # been pulled — match names with and without the ":latest" suffix.
            import httpx

            resp = httpx.get(f"{api_key.rstrip('/')}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False, f"Unexpected response: {resp.status_code}"
            names = {m.get("name", "") for m in resp.json().get("models") or []}
            candidates = {model, f"{model}:latest", model.removesuffix(":latest")}
            if names & candidates:
                return True, "Model verified"
            return False, f"Model not pulled — run: ollama pull {model}"

        elif provider_val in OPENAI_COMPATIBLE:
            # GET /models/{id} is not universal across these vendors, so check
            # membership in the list instead. A vendor that lists nothing is
            # soft-accepted (same call the wizard would otherwise block on),
            # mirroring how Bedrock soft-accepts inference profiles.
            status, ids = _openai_wire_models(provider_val, api_key, timeout=10)
            if status in _WIRE_KEY_REJECTED:
                return False, INVALID_KEY
            if status != 200:
                return False, f"Unexpected response: {status}"
            if not ids:
                return True, "Key verified — provider does not list models"
            if model in ids:
                return True, "Model verified"
            return False, "Unknown model for this account"

    except Exception as e:
        err_str = str(e)
        if provider_val == "ollama":
            return False, _ollama_unreachable_message()
        if "NoCredentialsError" in type(e).__name__ or "NoCredentialsError" in err_str:
            return False, "No AWS credentials found — configure IAM role, ~/.aws/credentials, or env vars"
        if "InvalidIdentityToken" in err_str or "AccessDenied" in err_str or "403" in err_str:
            return False, "AWS credentials lack Bedrock permissions"
        return False, _connection_error(e)

    return False, "Unknown provider"


def _extract_error_message(resp: Any) -> str:
    """Best-effort extraction of a human-readable error message from a JSON response."""
    try:
        data = resp.json()
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message", "")).strip()
        if isinstance(err, str):
            return err.strip()
    except Exception:
        pass
    return ""


# OpenAI's /v1/models list is noisy (embeddings, TTS, image, moderation, …).
# Keep only chat/reasoning families; substring match on the id is enough.
_OPENAI_NON_CHAT = (
    "embedding",
    "whisper",
    "tts",
    "audio",
    "realtime",
    "transcribe",
    "image",
    "dall-e",
    "moderation",
    "search",
    "codex",
    "computer-use",
)


def _is_non_chat(model_id: str) -> bool:
    """Whether an id names an embedding/audio/image/moderation model."""
    low = model_id.lower()
    return any(x in low for x in _OPENAI_NON_CHAT)


def _filter_openai_chat_models(entries: list[tuple[str, int]]) -> list[str]:
    """Newest-first chat/reasoning model ids from OpenAI's raw (id, created) list."""
    entries = sorted(entries, key=lambda t: t[1], reverse=True)
    keep: list[str] = []
    seen: set[str] = set()
    for mid, _created in entries:
        low = mid.lower()
        if _is_non_chat(mid):
            continue
        if low.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")) and mid not in seen:
            seen.add(mid)
            keep.append(mid)
    return keep


def _openai_wire_models(provider_val: str, api_key: str, timeout: float = 8) -> tuple[int, list[str]]:
    """``(status, model ids newest-first)`` from an OpenAI-wire vendor's /models.

    The status travels with the ids because the three callers need to tell a
    rejected key from a vendor that simply lists nothing. A non-200 yields an
    empty list; transport failures raise, and every caller already wraps this.
    """
    import httpx

    from yeaboi.llm_providers import base_url_for

    resp = httpx.get(
        f"{base_url_for(provider_val).rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return resp.status_code, []
    data = resp.json().get("data") or []
    entries = [(m["id"], int(m.get("created", 0) or 0)) for m in data if isinstance(m, dict) and m.get("id")]
    entries.sort(key=lambda t: t[1], reverse=True)
    return 200, [mid for mid, _ in entries]


def fetch_available_models(provider: dict[str, Any], api_key: str) -> list[str]:
    """Ask the provider which models this key can actually use (newest-first).

    This is the authoritative, always-current source — a hardcoded list is only
    a snapshot that goes stale when the provider retires a model. Returns [] on
    any failure (offline, timeout, unexpected shape, non-200) so callers fall
    back to the seed presets. Never raises. Bedrock is intentionally excluded —
    it authenticates with IAM credentials, not an API key.
    """
    provider_val = provider.get("provider_val")
    try:
        import httpx

        if provider_val == "anthropic":
            resp = httpx.get(
                "https://api.anthropic.com/v1/models?limit=100",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                timeout=8,
            )
            if resp.status_code != 200:
                return []
            # Models API returns newest-first; every id is messages-capable.
            data = resp.json().get("data") or []
            return [m["id"] for m in data if isinstance(m, dict) and m.get("id")]

        if provider_val == "openai":
            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=8,
            )
            if resp.status_code != 200:
                return []
            data = resp.json().get("data") or []
            entries = [(m["id"], int(m.get("created", 0))) for m in data if isinstance(m, dict) and m.get("id")]
            return _filter_openai_chat_models(entries)

        if provider_val == "ollama":
            # api_key carries the base URL (same repurposing as Bedrock's region).
            # /api/tags lists pulled models; sort newest-modified first so the
            # model the user just pulled tops the list.
            resp = httpx.get(f"{api_key.rstrip('/')}/api/tags", timeout=5)
            if resp.status_code != 200:
                return []
            models = [m for m in resp.json().get("models") or [] if isinstance(m, dict) and m.get("name")]
            models.sort(key=lambda m: str(m.get("modified_at", "")), reverse=True)
            return [m["name"] for m in models]

        if provider_val == "google":
            resp = httpx.get(
                f"https://generativelanguage.googleapis.com/v1/models?key={api_key}&pageSize=200",
                timeout=8,
            )
            if resp.status_code != 200:
                return []
            # supportedGenerationMethods is the provider's own capability flag —
            # keep only models that can actually generate chat content.
            out: list[str] = []
            for m in resp.json().get("models") or []:
                if not isinstance(m, dict):
                    continue
                name = m.get("name", "")
                methods = m.get("supportedGenerationMethods") or []
                if "generateContent" in methods and name.startswith("models/"):
                    mid = name[len("models/") :]
                    if "embedding" not in mid and "aqa" not in mid:
                        out.append(mid)
            return out

        if provider_val in OPENAI_COMPATIBLE:
            # Deliberately not _filter_openai_chat_models: its allowlist of
            # gpt-/o1/o3 prefixes would discard every id these vendors return.
            # Exclude the non-chat families by substring instead.
            _status, ids = _openai_wire_models(provider_val, api_key)
            return [m for m in ids if not _is_non_chat(m)]
    except Exception:
        return []
    return []


def pull_ollama_model(base_url: str, model: str, on_progress: Any, cancel_event: Any = None) -> tuple[bool, str]:
    """Download *model* onto the Ollama server, streaming progress.

    Uses the server's HTTP API (POST /api/pull) rather than shelling out to the
    ``ollama`` binary — the server may be remote or containerised with no CLI on
    this machine's PATH. The response is a stream of JSON lines
    ({status, total, completed}); each is folded into
    ``on_progress(status_text, fraction_or_None)``. A set ``cancel_event``
    (threading.Event) aborts between chunks — Ollama keeps partial layers, so a
    cancelled pull resumes where it left off next time.

    Returns (success, message). Never raises.
    """
    logger.info("Pulling Ollama model '%s'", model)
    try:
        import json as _json

        import httpx

        with httpx.stream(
            "POST",
            f"{base_url.rstrip('/')}/api/pull",
            json={"model": model},
            # Model downloads run for many minutes — no read timeout.
            timeout=httpx.Timeout(10, read=None),
        ) as resp:
            if resp.status_code != 200:
                return False, f"Unexpected response: {resp.status_code}"
            for line in resp.iter_lines():
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("Ollama pull cancelled for '%s'", model)
                    return False, "Download cancelled — partial layers are kept, pulling again resumes"
                if not line:
                    continue
                try:
                    event = _json.loads(line)
                except ValueError:
                    continue
                if event.get("error"):
                    logger.warning("Ollama pull failed for '%s': %s", model, event["error"])
                    return False, str(event["error"])
                total = event.get("total") or 0
                completed = event.get("completed") or 0
                fraction = (completed / total) if total else None
                on_progress(str(event.get("status", "")), fraction)
        logger.info("Ollama model '%s' pulled", model)
        return True, "Model downloaded"
    except Exception as e:
        logger.warning("Ollama pull error for '%s': %s", model, e)
        return False, f"Download failed: {e}"


def _verify_vc_token(vc: dict[str, Any], token: str) -> tuple[bool, str]:
    """Verify a version control PAT token with a lightweight API call."""
    env_var = vc["env_var"]
    try:
        import httpx

        if env_var == "GITHUB_TOKEN":
            resp = httpx.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                return True, "Token verified"
            if resp.status_code == 401:
                return False, "Invalid token"
            if resp.status_code == 403:
                return False, "Token lacks permissions"
            return False, f"Unexpected response: {resp.status_code}"

        elif env_var == "AZURE_DEVOPS_TOKEN":
            # Azure DevOps PAT — org-scoped PATs return 401 on global endpoints
            # (app.vssps.visualstudio.com) and only work against their org URL.
            # Since we don't know the org URL at the VC step, we accept the token
            # on format alone. Real verification happens at the Issue Tracking step
            # where the user provides the org URL.
            if len(token) >= 20:
                return True, "Token accepted — will verify with org URL"
            return False, "Token too short"

    except Exception as e:
        return False, _connection_error(e)

    return False, "Unknown provider"


def _verify_jira(base_url: str, email: str, token: str) -> tuple[bool, str]:
    """Verify Jira credentials with a lightweight API call."""
    try:
        import httpx

        url = f"{base_url.rstrip('/')}/rest/api/3/myself"
        import base64

        b64 = base64.b64encode(f"{email}:{token}".encode()).decode()
        resp = httpx.get(
            url,
            headers={"Authorization": f"Basic {b64}", "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "Jira verified"
        if resp.status_code in (401, 403):
            return False, "Invalid Jira credentials"
        return False, f"Unexpected response: {resp.status_code}"
    except Exception as e:
        return False, _connection_error(e)


def _verify_confluence(base_url: str, email: str, token: str, space_key: str) -> tuple[bool, str]:
    """Verify a Confluence space is reachable with the Jira Atlassian credentials.

    Confluence Cloud shares the Atlassian account auth used for Jira (base URL +
    email + API token — see tools/confluence.py); the space key is the only extra
    input. Hits GET /wiki/rest/api/space/{key} — 200 confirms the space exists and
    the credentials can read it. Mirrors _verify_jira's basic-auth pattern.
    """
    logger.info("Verifying Confluence space '%s'", space_key)
    try:
        import base64

        import httpx

        b64 = base64.b64encode(f"{email}:{token}".encode()).decode()
        url = f"{base_url.rstrip('/')}/wiki/rest/api/space/{space_key}"
        resp = httpx.get(
            url,
            headers={"Authorization": f"Basic {b64}", "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("Confluence space '%s' verified", space_key)
            return True, "Confluence verified"
        if resp.status_code in (401, 403):
            logger.warning("Confluence auth failed for space '%s' (%s)", space_key, resp.status_code)
            return False, "Invalid Atlassian credentials"
        if resp.status_code == 404:
            logger.warning("Confluence space '%s' not found", space_key)
            return False, f"Space '{space_key}' not found"
        return False, f"Unexpected response: {resp.status_code}"
    except Exception as e:
        logger.warning("Confluence verification error for space '%s': %s", space_key, e)
        return False, _connection_error(e)


def _verify_notion(token: str) -> tuple[bool, str]:
    """Verify a Notion integration token with a lightweight API call.

    Hits GET /v1/users/me — the cheapest authenticated endpoint. Notion requires
    the Notion-Version header on every request.
    """
    try:
        import httpx

        resp = httpx.get(
            "https://api.notion.com/v1/users/me",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "Notion verified"
        if resp.status_code == 401:
            return False, "Invalid Notion token"
        if resp.status_code == 403:
            return False, "Token lacks access — share pages with the integration"
        return False, f"Unexpected response: {resp.status_code}"
    except Exception as e:
        return False, _connection_error(e)


def _verify_datadog(token: str, app_key: str, site: str = "") -> tuple[bool, str]:
    """Verify a Datadog API key and application key against their site.

    Two calls, because the two credentials fail in different places: the API key
    is rejected by /v1/validate, while a bad *application* key only shows up on
    a call that authorises something. Reporting either as "invalid Datadog
    credentials" would send the user to re-cut the wrong one.
    """
    from yeaboi.connectors.datadog import api_base
    from yeaboi.connectors.http import probe_status

    base = api_base(site)
    status, message = probe_status(
        f"{base}/api/v1/validate",
        headers={"DD-API-KEY": token},
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, INVALID_KEY
    if status != 200:
        return False, f"Unexpected response: {status}"

    # The API key is good. Now the application key, on the cheapest endpoint
    # that actually requires one.
    status, message = probe_status(
        f"{base}/api/v1/monitor?page_size=1",
        headers={"DD-API-KEY": token, "DD-APPLICATION-KEY": app_key},
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, "API key verified, but the application key was rejected — check it has monitors_read"
    if status != 200:
        return False, f"Unexpected response: {status}"
    return True, "Datadog verified"


def _verify_grafana(base_url: str, token: str) -> tuple[bool, str]:
    """Verify a Grafana service-account token against GET /api/org.

    The host is the user's, so the request goes through the connector HTTP
    guard: https only, and never an address on this machine or a private range.
    """
    from yeaboi.connectors.http import probe_status

    status, message = probe_status(
        f"{base_url.rstrip('/')}/api/org",
        headers={"Authorization": f"Bearer {token}"},
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, INVALID_KEY
    if status == 404:
        return False, "Reached the host, but it does not look like a Grafana API — check the base URL"
    if status != 200:
        return False, f"Unexpected response: {status}"
    return True, "Grafana verified"


def _verify_pagerduty(token: str) -> tuple[bool, str]:
    """Verify a PagerDuty REST API key against GET /abilities — no scope needed."""
    from yeaboi.connectors.http import probe_status

    status, message = probe_status(
        "https://api.pagerduty.com/abilities",
        headers={
            "Authorization": f"Token token={token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        },
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, INVALID_KEY
    if status != 200:
        return False, f"Unexpected response: {status}"
    return True, "PagerDuty verified"


def _verify_aws(auth_method: str = "", role_arn: str = "", external_id: str = "", region: str = "") -> tuple[bool, str]:
    """Verify AWS access, and say whose identity yeaboi ended up as.

    Under ``assume_role`` this proves the whole guarantee at once: the assume
    call succeeds, and the read-only session policy it carried is what the
    subsequent DescribeAlarms runs under. Under ambient it reports the caller
    ARN, because a user pointing yeaboi at whatever this machine happens to be
    should see what that turned out to mean.
    """
    from yeaboi.connectors import aws

    if not aws.installed():
        return False, aws.PKG_MISSING
    if auth_method == "assume_role" and not (role_arn and external_id):
        return False, "Assuming a role needs both a role ARN and an external ID"

    try:
        import boto3

        caller = boto3.client("sts", region_name=region or "us-east-1").get_caller_identity()
    except Exception as exc:
        return False, _connection_error(exc)

    if auth_method != "assume_role":
        arn = str(caller.get("Arn") or "this machine's identity")
        return True, f"AWS verified as {arn} — yeaboi cannot bound what this identity may do"

    try:
        alarms = aws.client("cloudwatch").describe_alarms(MaxRecords=1)
    except Exception as exc:
        return False, _connection_error(exc)
    count = len(alarms.get("MetricAlarms") or []) + len(alarms.get("CompositeAlarms") or [])
    scope = "read-only session" if count or alarms is not None else "session"
    return True, f"AWS verified — assumed {role_arn.rsplit('/', 1)[-1]} under a {scope}"


def _verify_gcp(auth_method: str = "", project_id: str = "", service_account: str = "") -> tuple[bool, str]:
    """Verify Google Cloud access, and name the identity the token carries."""
    from yeaboi.connectors import gcp
    from yeaboi.connectors.http import probe_status

    if not gcp.installed():
        return False, gcp.PKG_MISSING
    if not project_id:
        return False, "Google Cloud verification needs a project id"
    if auth_method == "impersonate" and not service_account:
        return False, "Impersonation needs the service account to impersonate"

    try:
        token = gcp.access_token()
    except Exception as exc:
        return False, _connection_error(exc)

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    status, message = probe_status(
        gcp.group_stats_url(project_id, now - timedelta(hours=1), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, "Token minted, but it was refused — the account needs roles/errorreporting.viewer"
    if status != 200:
        return False, f"Unexpected response: {status}"
    if auth_method != "impersonate":
        return True, f"Google Cloud verified on {project_id} — yeaboi cannot bound what this identity may do"
    return True, f"Google Cloud verified on {project_id} as {service_account}"


def _verify_azure_cloud(
    tenant_id: str = "", client_id: str = "", client_secret: str = "", subscription_id: str = ""
) -> tuple[bool, str]:
    """Verify an Azure app registration and its Monitoring Reader assignment.

    Two failures worth telling apart: the app registration itself being wrong
    (the token never issues) and the role assignment being missing (the token
    issues and ARM refuses it). Reporting either as "invalid Azure credentials"
    sends the user to re-cut the wrong thing.
    """
    from datetime import datetime, timedelta, timezone

    from yeaboi.connectors import azure_cloud
    from yeaboi.connectors.http import probe_status

    missing = [
        name
        for name, value in (
            ("tenant id", tenant_id),
            ("client id", client_id),
            ("client secret", client_secret),
            ("subscription id", subscription_id),
        )
        if not value
    ]
    if missing:
        return False, f"Azure verification needs the {', '.join(missing)}"

    try:
        token = azure_cloud.access_token()
    except Exception as exc:
        return False, str(exc) if type(exc).__name__ == "FetchError" else _connection_error(exc)

    now = datetime.now(timezone.utc)
    status, message = probe_status(
        azure_cloud.alerts_url(subscription_id, now - timedelta(hours=1), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, "App registration verified, but ARM refused it — it needs Monitoring Reader on that subscription"
    if status != 200:
        return False, f"Unexpected response: {status}"
    return True, "Microsoft Azure verified — Monitoring Reader on that subscription"


def _verify_incidentio(token: str) -> tuple[bool, str]:
    """Verify an incident.io API key, and report the roles it carries.

    ``/v1/identity`` names the key's roles, so the result can say what the key
    may do rather than only that it authenticated — a key holding a write role
    still verifies, but the user is told before they trust it.
    """
    from yeaboi.connectors.http import UnsafeUrlError, get_json
    from yeaboi.connectors.incidentio import API_BASE

    try:
        resp = get_json(f"{API_BASE}/v1/identity", headers={"Authorization": f"Bearer {token}"})
    except UnsafeUrlError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, _connection_error(exc)
    if resp.status_code in (401, 403):
        return False, INVALID_KEY
    if resp.status_code != 200:
        return False, f"Unexpected response: {resp.status_code}"
    try:
        roles = (resp.json() or {}).get("identity", {}).get("roles") or []
    except Exception:
        roles = []
    named = ", ".join(str(r) for r in roles if r)
    return True, f"incident.io verified \u2014 key roles: {named}" if named else "incident.io verified"


def _verify_sentry(token: str, org: str, base_url: str = "") -> tuple[bool, str]:
    """Verify a Sentry auth token against its organisation.

    The org is a user-supplied path segment, so it is quoted rather than
    interpolated — a slug with a slash in it must not reach another endpoint.
    """
    from urllib.parse import quote

    from yeaboi.connectors.http import probe_status
    from yeaboi.connectors.sentry import api_base

    slug = quote(org.strip().strip("/"), safe="")
    if not slug:
        return False, "Sentry needs an organisation slug"
    status, message = probe_status(
        f"{api_base(base_url)}/api/0/organizations/{slug}/",
        headers={"Authorization": f"Bearer {token}"},
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, INVALID_KEY
    if status == 404:
        return False, f"Token accepted, but no organisation named {org!r} — check the slug, not the display name"
    if status != 200:
        return False, f"Unexpected response: {status}"
    return True, "Sentry verified"


def _verify_gitlab(token: str, base_url: str = "") -> tuple[bool, str]:
    """Verify a GitLab access token against GET /api/v4/user.

    The host may be the user's own install, so the request goes through the
    connector HTTP guard: https only, never an address on this machine or a
    private range.
    """
    from yeaboi.connectors.gitlab import api_base
    from yeaboi.connectors.http import probe_status

    status, message = probe_status(
        f"{api_base(base_url)}/api/v4/user",
        headers={"PRIVATE-TOKEN": token},
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, INVALID_KEY
    if status == 404:
        return False, "Reached the host, but it does not look like a GitLab API — check the base URL"
    if status != 200:
        return False, f"Unexpected response: {status}"
    return True, "GitLab verified"


def _verify_bitbucket(email: str, token: str, workspace: str) -> tuple[bool, str]:
    """Verify an Atlassian API token can read one Bitbucket workspace.

    The workspace is a user-supplied path segment, so it is quoted rather than
    interpolated — an ID with a slash in it must not reach another endpoint.
    """
    from urllib.parse import quote

    from yeaboi.connectors.bitbucket import basic_auth
    from yeaboi.connectors.http import probe_status

    slug = quote(workspace.strip().strip("/"), safe="")
    if not slug:
        return False, "Bitbucket needs a workspace ID"
    status, message = probe_status(
        f"https://api.bitbucket.org/2.0/workspaces/{slug}",
        headers={"Authorization": f"Basic {basic_auth(email, token)}"},
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, INVALID_KEY
    if status == 404:
        return False, f"Credentials accepted, but no workspace named {workspace!r} — check the ID, not the display name"
    if status != 200:
        return False, f"Unexpected response: {status}"
    return True, "Bitbucket verified"


def _verify_linear(token: str) -> tuple[bool, str]:
    """Verify a Linear API key with the cheapest authenticated query — the viewer.

    Linear is GraphQL-only, so this is the one probe that POSTs. The host is
    fixed, the query is a constant, and nothing from the response beyond the
    presence of a viewer id is read.
    """
    try:
        import httpx

        resp = httpx.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": token, "Content-Type": "application/json"},
            json={"query": "{ viewer { id } }"},
            timeout=10,
        )
        if resp.status_code in (400, 401, 403):
            return False, INVALID_KEY
        if resp.status_code != 200:
            return False, f"Unexpected response: {resp.status_code}"
        body = resp.json() if resp.content else {}
        if isinstance(body, dict) and body.get("errors"):
            return False, INVALID_KEY
        return True, "Linear verified"
    except Exception as e:
        return False, _connection_error(e)


def _verify_trello(api_key: str, token: str) -> tuple[bool, str]:
    """Verify a Trello key/token pair against GET /1/members/me.

    Trello authenticates on the query string, so the URL carries both
    credentials — it must never be logged or echoed, and the failure messages
    here are constants for that reason.
    """
    from urllib.parse import urlencode

    try:
        import httpx

        creds = urlencode({"key": api_key, "token": token})
        resp = httpx.get(f"https://api.trello.com/1/members/me?{creds}", timeout=10)
        if resp.status_code in (401, 403):
            return False, INVALID_KEY
        if resp.status_code != 200:
            return False, f"Unexpected response: {resp.status_code}"
        return True, "Trello verified"
    except Exception as e:
        return False, _connection_error(e)


def _verify_custom_api(
    key: str = "", base_url: str = "", token: str = "", username: str = "", password: str = ""
) -> tuple[bool, str]:
    """Verify one user-created API connection against its declared probe.

    The host is the user's own, so the request goes through the connector HTTP
    guard: https only, never an address on this machine or a private range.
    Auth is built from the descriptor's scheme; nothing about the request shape
    comes from the caller beyond the credential values themselves.
    """
    from yeaboi.connectors.custom import auth_headers, spec_by_key
    from yeaboi.connectors.http import probe_status

    spec = spec_by_key(key)
    if spec is None:
        return False, f"No custom connection named {key!r}"
    values = {
        f"{spec.env_stem}_TOKEN": token,
        f"{spec.env_stem}_USERNAME": username,
        f"{spec.env_stem}_PASSWORD": password,
    }
    status, message = probe_status(
        f"{base_url.rstrip('/')}{spec.probe_path}",
        headers=auth_headers(spec, values),
    )
    if status == 0:
        return False, message
    if status in (401, 403):
        return False, INVALID_KEY
    if status == 404:
        return False, "Reached the host, but the probe path does not exist — check the connection's probe"
    if status != spec.probe_ok_status:
        return False, f"Unexpected response: {status} (expected {spec.probe_ok_status})"
    return True, f"{spec.label} verified"


def _verify_elevenlabs(token: str) -> tuple[bool, str]:
    """Verify an ElevenLabs API key against GET /v1/user — the cheapest authenticated endpoint."""
    try:
        import httpx

        resp = httpx.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": token}, timeout=10)
        if resp.status_code == 200:
            tier = ""
            try:
                tier = str(resp.json().get("subscription", {}).get("tier", ""))
            except Exception:
                pass
            return True, f"ElevenLabs verified — {tier} tier" if tier else "ElevenLabs verified"
        if resp.status_code == 401:
            return False, "Invalid ElevenLabs API key"
        if resp.status_code == 403:
            return False, "ElevenLabs key is restricted or disabled"
        return False, f"Unexpected response: {resp.status_code}"
    except Exception as e:
        return False, _connection_error(e)


def _verify_tavus(token: str) -> tuple[bool, str]:
    """Verify a Tavus API key by listing replicas — free, and the key's core permission."""
    try:
        import httpx

        resp = httpx.get("https://tavusapi.com/v2/replicas", headers={"x-api-key": token}, timeout=10)
        if resp.status_code == 200:
            return True, "Tavus verified"
        if resp.status_code in (401, 403):
            return False, "Invalid Tavus API key"
        return False, f"Unexpected response: {resp.status_code}"
    except Exception as e:
        return False, _connection_error(e)


def _verify_azdevops(org_url: str, project: str, token: str) -> tuple[bool, str]:
    """Verify Azure DevOps credentials by listing work item types for the project."""
    try:
        import base64

        import httpx

        b64 = base64.b64encode(f":{token}".encode()).decode()
        url = f"{org_url.rstrip('/')}/{project}/_apis/wit/workitemtypes?api-version=7.1"
        resp = httpx.get(
            url,
            headers={"Authorization": f"Basic {b64}", "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "Azure DevOps verified"
        if resp.status_code in (401, 403):
            return False, "Invalid Azure DevOps credentials"
        if resp.status_code == 404:
            return False, "Project not found — check org URL and project name"
        return False, f"Unexpected response: {resp.status_code}"
    except Exception as e:
        return False, _connection_error(e)
