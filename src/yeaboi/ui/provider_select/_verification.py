"""Validation and API verification for provider setup.

# See docs: "Architecture" — verification layer for the setup wizard.
# Handles format validation of API keys and live verification via API calls.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Shared Ollama failure copy — the same situation is reachable from both
# _verify_api_key and _verify_model, and the messages must stay identical so
# tests (and users retrying) see one consistent instruction.
_OLLAMA_PKG_MISSING = (
    "Ollama support isn't installed — run: uv sync --extra ollama (or: pip install langchain-ollama), then retry"
)


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

    min_lengths = {"sk-ant-": 40, "sk-": 30, "AIza": 30}
    min_len = min_lengths.get(prefix, 30)

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
                return False, "Invalid API key"
            if resp.status_code == 403:
                return False, "Key lacks permissions"
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
                return False, "Invalid API key"
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
                return False, "Invalid API key"
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

    except Exception as e:
        err_str = str(e)
        if provider_val == "ollama":
            return False, _ollama_unreachable_message()
        if "NoCredentialsError" in type(e).__name__ or "NoCredentialsError" in err_str:
            return False, "No AWS credentials found \u2014 configure IAM role, ~/.aws/credentials, or env vars"
        if "InvalidIdentityToken" in err_str or "AccessDenied" in err_str or "403" in err_str:
            return False, "AWS credentials lack Bedrock permissions"
        return False, f"Connection error: {e}"

    return False, "Unknown provider"


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
                return False, "Invalid API key"
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
                return False, "Invalid API key"
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
                return False, "Invalid API key"
            return False, f"Unexpected response: {resp.status_code}"

        elif provider_val == "bedrock":
            # api_key is the AWS region. Inference-profile ids (leading us./eu./
            # global.) — which is what OpenClaw auto-detects — are NOT returned by
            # list_foundation_models, so soft-accept those once the region resolves.
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

    except Exception as e:
        err_str = str(e)
        if provider_val == "ollama":
            return False, _ollama_unreachable_message()
        if "NoCredentialsError" in type(e).__name__ or "NoCredentialsError" in err_str:
            return False, "No AWS credentials found — configure IAM role, ~/.aws/credentials, or env vars"
        if "InvalidIdentityToken" in err_str or "AccessDenied" in err_str or "403" in err_str:
            return False, "AWS credentials lack Bedrock permissions"
        return False, f"Connection error: {e}"

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


def _filter_openai_chat_models(entries: list[tuple[str, int]]) -> list[str]:
    """Newest-first chat/reasoning model ids from OpenAI's raw (id, created) list."""
    entries = sorted(entries, key=lambda t: t[1], reverse=True)
    keep: list[str] = []
    seen: set[str] = set()
    for mid, _created in entries:
        low = mid.lower()
        if any(x in low for x in _OPENAI_NON_CHAT):
            continue
        if low.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")) and mid not in seen:
            seen.add(mid)
            keep.append(mid)
    return keep


def fetch_available_models(provider: dict[str, Any], api_key: str) -> list[str]:
    """Ask the provider which models this key can actually use (newest-first).

    This is the authoritative, always-current source — a hardcoded list is only
    a snapshot that goes stale when the provider retires a model. Returns [] on
    any failure (offline, timeout, unexpected shape, non-200) so callers fall
    back to the seed presets. Never raises. Bedrock is intentionally excluded —
    it resolves its model via OpenClaw auto-detection, not an API key.
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
        return False, f"Connection error: {e}"

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
        return False, f"Connection error: {e}"


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
        return False, f"Connection error: {e}"


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
        return False, f"Connection error: {e}"


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
        return False, f"Connection error: {e}"
