"""LLM instance factory for the Scrum Agent.

# See docs: "Architecture" — Model layer
# See docs: "Agentic Blueprint Reference" — Core Graph Setup

Provider-agnostic LLM factory. The active provider is selected at runtime via
the LLM_PROVIDER env var (default: "anthropic"). This means the agent works
with Anthropic Claude, OpenAI GPT, or Google Gemini — swap by changing one
env var, no code changes required.

Why lazy imports?
Each provider requires its own langchain integration package. Lazy imports
(inside the if-branches) mean importing this module never fails even if one
of the optional packages isn't installed — the error is surfaced only when
get_llm() is called with that provider.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from langchain_core.language_models import BaseChatModel

from yeaboi.config import get_llm_model, get_llm_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM override — inject a caller-supplied model into every get_llm() call
# ---------------------------------------------------------------------------
#
# # See README: "MCP Server" — sampling (host-model) mode
# The MCP server can route yeaboi's LLM calls through the *client's* model
# (MCP "sampling") instead of the user's own API key. Engines and nodes all
# obtain their model via get_llm(), so the override must live here — modules
# import get_llm by name (`from yeaboi.agent.llm import get_llm`), which means
# swapping the module attribute from outside would not reach them.
#
# A ContextVar (not a plain global) scopes the override to the current
# execution context: concurrent MCP tool calls each see only their own
# override, and contextvars propagate into worker threads started with
# anyio.to_thread.run_sync (anyio copies the caller's context), so setting
# the override in an async handler is visible to the sync engine running in
# its worker thread.

_llm_override: ContextVar[BaseChatModel | None] = ContextVar("yeaboi_llm_override", default=None)


@contextmanager
def llm_override(model: BaseChatModel):
    """Make get_llm() return `model` for the duration of the block.

    While active, get_llm()'s `model`/`temperature` arguments are ignored —
    the injected model is returned as-is for every call. Used by the MCP
    server to substitute a sampling-backed model; tests may use it to inject
    fakes without monkeypatching four provider branches.
    """
    token = _llm_override.set(model)
    try:
        yield
    finally:
        _llm_override.reset(token)


# ---------------------------------------------------------------------------
# Token usage tracking — accumulates across all LLM calls in this process
# ---------------------------------------------------------------------------

_usage_stats: dict[str, int] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "call_count": 0,
}


def track_usage(response) -> None:
    """Extract token usage from an LLM response and accumulate it.

    Call this after every LLM invoke() to track token consumption.
    Works with all providers (Anthropic, OpenAI, Google, Bedrock).
    """

    def _as_int(value) -> int:
        # Defensive: metadata shapes vary per provider (and mocks in tests) —
        # anything non-numeric counts as "no data" rather than crashing a call.
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    # Preferred source: LangChain's provider-neutral `usage_metadata` attribute
    # (an AIMessage field with input_tokens/output_tokens). ChatOllama populates
    # this while its response_metadata uses Ollama-native keys (prompt_eval_count/
    # eval_count) that the metadata fallback below also understands.
    usage_meta = getattr(response, "usage_metadata", None)
    inp = out = 0
    if isinstance(usage_meta, dict):
        inp = _as_int(usage_meta.get("input_tokens", 0))
        out = _as_int(usage_meta.get("output_tokens", 0))
        if inp or out:
            logger.info("track_usage: using usage_metadata (in=%d, out=%d)", inp, out)
    if not (inp or out):
        meta = getattr(response, "response_metadata", None)
        meta = meta if isinstance(meta, dict) else {}
        logger.info("track_usage: response_metadata keys=%s", list(meta.keys()) if meta else "empty")

        # Anthropic: meta has 'usage' dict with input_tokens/output_tokens
        # OpenAI: meta has 'token_usage' dict with prompt_tokens/completion_tokens
        # Ollama: meta itself carries prompt_eval_count/eval_count
        usage = meta.get("usage", {}) or meta.get("token_usage", {})
        if not isinstance(usage, dict) or not usage:
            usage = meta
        logger.info(
            "track_usage: usage keys=%s, values=%s", list(usage.keys()) if isinstance(usage, dict) else "?", usage
        )

        inp = (
            _as_int(usage.get("input_tokens", 0))
            or _as_int(usage.get("prompt_tokens", 0))
            or _as_int(usage.get("prompt_eval_count", 0))
        )
        out = (
            _as_int(usage.get("output_tokens", 0))
            or _as_int(usage.get("completion_tokens", 0))
            or _as_int(usage.get("eval_count", 0))
        )
    # Local-model performance metrics. Ollama echoes timing in response_metadata
    # (nanoseconds) — total_duration/eval_duration/load_duration/eval_count. These
    # are read *unconditionally* (not just in the token fallback above): ChatOllama
    # populates usage_metadata, so the token branch short-circuits before ever
    # touching response_metadata, and the timing would otherwise be lost. Cloud
    # providers lack these keys → perf stays empty and the columns stay NULL.
    perf = _extract_local_perf(getattr(response, "response_metadata", None), _as_int)

    if inp or out:
        _usage_stats["input_tokens"] += inp
        _usage_stats["output_tokens"] += out
        _usage_stats["total_tokens"] += inp + out
        _usage_stats["call_count"] += 1
        logger.info(
            "Token usage: +%d in, +%d out (total: %d, calls: %d)",
            inp,
            out,
            _usage_stats["total_tokens"],
            _usage_stats["call_count"],
        )
        # Persist to SQLite for lifetime tracking across sessions
        try:
            from yeaboi.sessions import SessionStore

            provider = get_llm_provider()
            model = get_llm_model() or _PROVIDER_DEFAULTS.get(provider, "")
            from yeaboi.paths import get_db_path

            db = get_db_path()
            with SessionStore(db) as store:
                store.record_token_usage(inp, out, model=model, provider=provider, **perf)
            if perf:
                logger.info(
                    "local call: model=%s in=%d out=%d duration=%.0fms tok/s=%s",
                    model,
                    inp,
                    out,
                    perf.get("duration_ms") or 0.0,
                    perf.get("tokens_per_sec"),
                )
        except Exception:
            logger.debug("Failed to persist token usage to DB", exc_info=True)
    else:
        logger.warning("track_usage: no token data found in response metadata")


def _extract_local_perf(meta, _as_int) -> dict:
    """Pull Ollama's per-call timing out of response_metadata (ns → ms).

    Returns {} for cloud providers (keys absent) so callers pass no perf kwargs.
    tokens_per_sec = generated tokens / generation seconds — the headline local
    throughput number surfaced on the Usage page.
    """
    if not isinstance(meta, dict):
        return {}
    total_ns = _as_int(meta.get("total_duration", 0))
    eval_ns = _as_int(meta.get("eval_duration", 0))
    load_ns = _as_int(meta.get("load_duration", 0))
    eval_cnt = _as_int(meta.get("eval_count", 0))
    if not (total_ns or eval_ns):
        return {}
    return {
        "duration_ms": (total_ns / 1e6) or None,
        "eval_duration_ms": (eval_ns / 1e6) or None,
        "load_duration_ms": (load_ns / 1e6) or None,
        "tokens_per_sec": round(eval_cnt / (eval_ns / 1e9), 2) if (eval_ns and eval_cnt) else None,
    }


def get_usage_stats() -> dict:
    """Return accumulated token usage stats for display on the Usage page."""
    return dict(_usage_stats)


def reset_usage_stats() -> None:
    """Reset token counters (e.g. at start of a new session)."""
    logger.info(
        "Token usage stats reset (was %d tokens, %d calls)", _usage_stats["total_tokens"], _usage_stats["call_count"]
    )
    for k in _usage_stats:
        _usage_stats[k] = 0


# Default models per provider — chosen for best quality/cost balance.
# Override any of these with the LLM_MODEL env var.
_PROVIDER_DEFAULTS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "google": "gemini-2.5-flash",
    "bedrock": "us.anthropic.claude-sonnet-4-6-v1:0",
    # Local default: best small model for JSON adherence + tool calling that
    # fits comfortably on a 16 GB machine. See README: "Local Mode (Ollama)"
    # for the full model trade-off table.
    "ollama": "qwen3:8b",
}

# Kept for backward compatibility — callers that imported DEFAULT_MODEL still work.
DEFAULT_MODEL = _PROVIDER_DEFAULTS["anthropic"]

# Output-token cap requested from Ollama. Single-sourced: get_llm() passes it
# as num_predict, and the context-budget maths (warn_if_context_overflow,
# nodes._trim_history_for_local) reserve the same amount out of OLLAMA_NUM_CTX.
_OLLAMA_NUM_PREDICT = 8192


def _supports_temperature(model: str) -> bool:
    """Whether a Claude model still accepts the ``temperature`` sampling param.

    Newer Claude models (Opus 4.6+ and the Claude 5 family) reject sampling
    parameters *by presence*: sending ``temperature`` at all — even a default
    value — fails with HTTP 400 "temperature is deprecated for this model".
    For those models the parameter must be omitted entirely so the API uses
    the model's own default sampling. Matched by substring so plain Anthropic
    ids ("claude-opus-4-8") and Bedrock ids ("us.anthropic.claude-opus-4-8-v1:0")
    both work. Older models (Sonnet 4.x, Haiku 4.5, Claude 3.x) keep the
    explicit value.
    """
    import re

    return not re.search(r"opus-4-[6-9]|(fable|sonnet|haiku|opus)-5(?!\d)", model)


def get_llm(model: str | None = None, temperature: float = 0.0, json_mode: bool = False) -> BaseChatModel:
    """Create an LLM instance for the configured provider.

    # See docs: "Agentic Blueprint Reference" — Core Graph Setup
    # BaseChatModel is LangChain's common interface for all chat LLMs.
    # Every provider wrapper (ChatAnthropic, ChatOpenAI, ChatGoogleGenerativeAI)
    # implements BaseChatModel, so the rest of the agent — nodes, bind_tools(),
    # streaming — works identically regardless of which LLM is active.
    #
    # Provider selection:
    #   LLM_PROVIDER=anthropic  →  ChatAnthropic  (default)
    #   LLM_PROVIDER=openai     →  ChatOpenAI
    #   LLM_PROVIDER=google     →  ChatGoogleGenerativeAI
    #
    # Model selection (highest priority wins):
    #   1. `model` argument passed directly to get_llm()
    #   2. LLM_MODEL env var
    #   3. Provider default from _PROVIDER_DEFAULTS

    Args:
        model: Model ID override. None means use LLM_MODEL env var or provider default.
        temperature: Sampling temperature. 0.0 = deterministic (default for structured
            artifact generation). Use 0.2–0.5 for tools that benefit from variety.
        json_mode: When True and the provider is Ollama, enables constrained JSON
            decoding (ChatOllama's ``format="json"``) — the model literally cannot
            emit anything but syntactically valid JSON, which is the main
            reliability lever for weaker local models. A documented no-op for the
            cloud providers (their JSON discipline comes from the prompts plus the
            invoke_json() repair loop). Never set this for prose calls (the
            conversational agent, llm_tools, guardrail classifiers).

    Returns:
        A configured BaseChatModel ready for use in LangGraph nodes.

    Raises:
        OSError: If the required API key for the selected provider is not set.
        ValueError: If LLM_PROVIDER is set to an unknown value.
    """
    # MCP sampling / test injection — an active override short-circuits
    # provider selection entirely (see llm_override() above).
    override = _llm_override.get()
    if override is not None:
        logger.debug("get_llm: returning injected override model (%s)", type(override).__name__)
        return override

    provider = get_llm_provider()
    resolved_model = model or get_llm_model() or _PROVIDER_DEFAULTS.get(provider, "")
    logger.debug("get_llm: provider=%s, model=%s, temperature=%s", provider, resolved_model, temperature)

    # Newer Claude models reject `temperature` by presence (400 "temperature is
    # deprecated for this model") — omit the kwarg entirely for those.
    _sampling = {"temperature": temperature} if _supports_temperature(resolved_model) else {}
    if not _sampling:
        logger.debug("get_llm: omitting temperature — %s deprecates sampling params", resolved_model)

    if provider == "anthropic":
        # langchain-anthropic is a required dependency — always available.
        from langchain_anthropic import ChatAnthropic

        from yeaboi.config import get_anthropic_api_key

        llm = ChatAnthropic(
            model=resolved_model,
            api_key=get_anthropic_api_key(),
            **_sampling,
        )
        logger.info("LLM ready: provider=anthropic, model=%s", resolved_model)
        return llm

    if provider == "openai":
        # langchain-openai is an optional dependency (install with: uv add langchain-openai)
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            raise ImportError("langchain-openai is not installed. Run: uv add langchain-openai") from e

        from yeaboi.config import get_openai_api_key

        api_key = get_openai_api_key()
        if not api_key:
            raise OSError("OPENAI_API_KEY is not set. Add it to your .env file.")
        logger.info("LLM ready: provider=openai, model=%s", resolved_model)
        return ChatOpenAI(model=resolved_model, api_key=api_key, temperature=temperature)

    if provider == "google":
        # langchain-google-genai is an optional dependency (install with: uv add langchain-google-genai)
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as e:
            raise ImportError("langchain-google-genai is not installed. Run: uv add langchain-google-genai") from e

        from yeaboi.config import get_google_api_key

        api_key = get_google_api_key()
        if not api_key:
            raise OSError("GOOGLE_API_KEY is not set. Add it to your .env file.")
        logger.info("LLM ready: provider=google, model=%s", resolved_model)
        return ChatGoogleGenerativeAI(model=resolved_model, google_api_key=api_key, temperature=temperature)

    if provider == "bedrock":
        # langchain-aws is an optional dependency (install with: uv sync --extra bedrock)
        # # See docs: "Deploy on AWS Lightsail (OpenClaw)" — Bedrock uses IAM credentials
        # from the instance role, ~/.aws/credentials, or AWS_ACCESS_KEY_ID env vars.
        # No API key needed on Lightsail — the IAM role is attached automatically.
        try:
            from langchain_aws import ChatBedrockConverse
        except ImportError as e:
            raise ImportError("langchain-aws is not installed. Run: uv sync --extra bedrock") from e

        import boto3

        from yeaboi.config import get_aws_profile, get_bedrock_region

        region = get_bedrock_region()
        profile = get_aws_profile()

        # Create a boto3 session with the detected profile so IAM role
        # credentials from ~/.aws/config are used (e.g. Lightsail's
        # [profile assumed] with credential_source=Ec2InstanceMetadata).
        from botocore.config import Config as BotoConfig

        # Increase read timeout for large prompts (story writer, task decomposer).
        # The default 60s is too short for cross-region inference profiles
        # (global.*) which route through US regions and back.
        boto_config = BotoConfig(read_timeout=300, connect_timeout=10, retries={"max_attempts": 2})

        session = boto3.Session(profile_name=profile, region_name=region)
        bedrock_client = session.client("bedrock-runtime", region_name=region, config=boto_config)

        logger.info("LLM ready: provider=bedrock, model=%s, region=%s, profile=%s", resolved_model, region, profile)
        return ChatBedrockConverse(
            model=resolved_model,
            region_name=region,
            client=bedrock_client,
            **_sampling,
        )

    if provider == "ollama":
        # langchain-ollama is an optional dependency (install with: uv sync --extra ollama)
        # # See README: "Local Mode (Ollama)" — keyless local provider. The model
        # runs entirely on the user's machine via the Ollama server; there are no
        # credentials, so get_llm() never raises OSError for this provider.
        try:
            from langchain_ollama import ChatOllama
        except ImportError as e:
            raise ImportError("langchain-ollama is not installed. Run: uv sync --extra ollama") from e

        from yeaboi.config import get_ollama_base_url, get_ollama_num_ctx

        base_url = get_ollama_base_url()
        num_ctx = get_ollama_num_ctx()
        llm = ChatOllama(
            model=resolved_model,
            base_url=base_url,
            temperature=temperature,
            # Request a context large enough for the biggest assembled prompts —
            # Ollama's server default (2-4k tokens) silently truncates, which
            # destroys JSON output. See config.get_ollama_num_ctx().
            num_ctx=num_ctx,
            # Cap output generously; Ollama's small default can cut JSON mid-array.
            num_predict=_OLLAMA_NUM_PREDICT,
            # The planning pipeline runs 5 sequential nodes — keep the model
            # loaded in RAM between them instead of reloading every call.
            keep_alive="10m",
            # Local inference on CPU can be slow; mirror bedrock's read_timeout.
            client_kwargs={"timeout": 300},
            # format="json" is Ollama's constrained decoding — see json_mode docs.
            format="json" if json_mode else "",
            # Thinking models (qwen3) spend generation budget on a reasoning
            # pass BEFORE the constrained JSON — on a big planning prompt that
            # can consume the entire num_predict and return EMPTY content after
            # minutes of local compute. JSON calls therefore disable thinking
            # (reasoning=False → "think": false); prose calls keep the model's
            # default (thinking improves prose; strip_think_tags() handles the
            # tags). None = omit the option entirely for maximum server compat.
            reasoning=False if json_mode else None,
        )
        logger.info(
            "LLM ready: provider=ollama, model=%s, base_url=%s, num_ctx=%d, json_mode=%s",
            resolved_model,
            base_url,
            num_ctx,
            json_mode,
        )
        return llm

    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r}. Valid options are: anthropic (default), openai, google, bedrock, ollama."
    )


# ---------------------------------------------------------------------------
# Multimodal (vision) helpers — pasted screenshot support
# ---------------------------------------------------------------------------
#
# # See docs: "Prompt Construction" — multimodal content blocks
# LangChain message content is either a plain string OR a list of typed
# "content blocks". The portable block shape
#   {"type": "image", "source_type": "base64", "mime_type": "image/png", "data": ...}
# is translated by langchain-core into each provider's native format
# (ChatAnthropic, ChatOpenAI, ChatGoogleGenerativeAI, ChatBedrockConverse), so
# one shape covers all four providers get_llm() can return.
#
# Images travel through the app as *file paths* (under ~/.yeaboi/attachments/)
# and are base64-encoded only here, at the moment of the LLM call — state stays
# small, sessions survive --resume, and a deleted file degrades to text-only.

_MIME_FOR_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def load_image_b64(path) -> tuple[str, str] | None:
    """Read an image file and return ``(base64_data, mime_type)``.

    Returns ``None`` (with a warning log) when the file is missing or unreadable —
    e.g. the user deleted ~/.yeaboi/attachments/ between sessions. Callers skip
    the image and proceed text-only rather than failing the whole LLM call.
    """
    import base64
    from pathlib import Path

    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        logger.warning("pasted image missing or unreadable, skipping: %s (%s)", path, exc)
        return None
    mime = _MIME_FOR_EXT.get(p.suffix.lower(), "image/png")
    return base64.b64encode(data).decode("ascii"), mime


def build_multimodal_content(text: str, image_paths) -> str | list[dict]:
    """Build ``HumanMessage`` content: plain text, or text + image blocks.

    With no loadable images this returns ``text`` unchanged, so callers can swap
    it in unconditionally — the no-image behaviour is byte-identical to today's
    ``HumanMessage(content=prompt)``.
    """
    blocks: list[dict] = []
    for path in image_paths or []:
        loaded = load_image_b64(path)
        if loaded is None:
            continue
        b64, mime = loaded
        blocks.append({"type": "image", "source_type": "base64", "mime_type": mime, "data": b64})
    if not blocks:
        return text
    return [{"type": "text", "text": text}, *blocks]


def invoke_with_images(llm: BaseChatModel, prompt: str, image_paths=None):
    """Invoke ``llm`` with ``prompt`` plus any pasted screenshots.

    Drop-in replacement for ``llm.invoke([HumanMessage(content=prompt)])``.
    If the multimodal call fails (e.g. a non-vision Bedrock model rejects image
    blocks), we log a warning and retry once text-only — the user loses the
    screenshot context but the pipeline never crashes because of it. Auth/billing
    errors on the text-only path still propagate to the caller's usual handling.
    """
    from langchain_core.messages import HumanMessage

    content = build_multimodal_content(prompt, image_paths)
    if isinstance(content, str):
        return llm.invoke([HumanMessage(content=content)])
    try:
        return llm.invoke([HumanMessage(content=content)])
    except Exception as exc:
        logger.warning("model rejected image blocks (%s); retrying text-only", exc)
        return llm.invoke([HumanMessage(content=prompt)])


# ---------------------------------------------------------------------------
# Reliable JSON invocation — constrained decoding + one-shot repair loop
# ---------------------------------------------------------------------------
#
# # See README: "Local Mode (Ollama)" — how reliability is achieved
# The planning pipeline and the mode engines all parse the model's reply as
# JSON, and every parser falls back to a deterministic artifact when parsing
# fails. That fallback never crashes — but it silently downgrades quality.
# invoke_json() closes that gap for every provider:
#   1. json_mode=True turns on Ollama's constrained JSON decoding (no-op for
#      cloud providers).
#   2. If the reply still fails json.loads (truncation, prose, fences), we
#      re-ask ONCE with the exact parse error so the model can repair it.
# The return value is the raw response object, so existing call sites keep
# their `_parse_*_response(response.content)` line and fallback untouched.


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English prose/JSON).

    Deliberately heuristic — an exact count needs the model's own tokenizer.
    Used only for local context-window budget checks (warnings + history
    trimming), where ±25% accuracy is plenty.
    """
    return len(text) // 4


def warn_if_context_overflow(prompt: str) -> None:
    """Log a warning when a local-Ollama prompt likely exceeds the context window.

    Ollama silently LEFT-truncates anything beyond num_ctx − num_predict — no
    error, just a prompt missing its beginning, which destroys structured
    output. We can't prevent it from here; this makes it diagnosable in the
    logs instead of an invisible quality cliff. No-op for cloud providers
    (their windows are 200k+).
    """
    from yeaboi.config import get_llm_provider, get_ollama_num_ctx

    if get_llm_provider() != "ollama":
        return
    est = estimate_tokens(prompt)
    num_ctx = get_ollama_num_ctx()
    if est + _OLLAMA_NUM_PREDICT > num_ctx:
        logger.warning(
            "prompt ~%d tokens + %d output reserve exceeds OLLAMA_NUM_CTX=%d — "
            "Ollama truncates silently; raise OLLAMA_NUM_CTX in .env if output quality degrades",
            est,
            _OLLAMA_NUM_PREDICT,
            num_ctx,
        )


def strip_think_tags(text: str) -> str:
    """Remove ``<think>…</think>`` reasoning blocks from prose LLM output.

    Think-by-default local models (e.g. qwen3, the Ollama default) embed their
    chain-of-thought in the response content. JSON calls are protected by
    constrained decoding (format="json"), but prose paths — the conversational
    agent, llm_tools, the guardrail classifier — would show the raw tags to the
    user. Stripping at the consumption point is the zero-risk fix: cloud models
    never emit these, and prose calls deliberately keep the model's default
    thinking (it improves prose quality; JSON calls disable it in get_llm —
    see the reasoning= comment there).
    """
    import re

    if not isinstance(text, str) or "<think>" not in text:
        return text
    # Closed blocks first; then an unclosed leading block (truncated generation).
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"^\s*<think>.*", "", text, flags=re.DOTALL)
    return text.strip()


def strip_json_fences(raw: str) -> str:
    """Strip a surrounding markdown code fence from an LLM reply, if present.

    Models often wrap JSON in ```json ... ``` despite instructions. This is the
    shared version of the fence-strip idiom used by the `_parse_*_response`
    helpers; invoke_json() uses it for validation only.
    """
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw[raw.find("\n") + 1 :]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    return raw.strip()


def invoke_json(prompt: str, *, temperature: float = 0.0, image_paths=None, max_reasks: int = 1, get_llm_fn=None):
    """Invoke the configured LLM expecting a JSON reply, with a repair re-ask.

    Drop-in replacement for ``get_llm(temperature=...).invoke([HumanMessage(prompt)])``
    (or ``invoke_with_images(...)``) at call sites whose response is parsed as
    JSON. Calls track_usage() on every attempt, so callers must NOT track again.

    Args:
        prompt: The full prompt text.
        temperature: Sampling temperature (0.0 for structured artifacts).
        image_paths: Optional pasted-screenshot paths (see invoke_with_images).
        max_reasks: How many repair rounds to attempt after an invalid reply.
            Default 1 — one round captures nearly all the value; more just
            doubles latency on slow local models for rare wins.
        get_llm_fn: Optional factory used instead of this module's get_llm().
            nodes.py passes its own module-level reference so the established
            test seam (patching ``yeaboi.agent.nodes.get_llm``) keeps working.

    Returns:
        The last LLM response object. Its ``.content`` is best-effort valid
        JSON; if every repair attempt failed, callers' existing deterministic
        fallbacks take over exactly as before.
    """
    import json

    from langchain_core.messages import AIMessage, HumanMessage

    warn_if_context_overflow(prompt)
    llm = (get_llm_fn or get_llm)(temperature=temperature, json_mode=True)
    response = invoke_with_images(llm, prompt, image_paths)
    track_usage(response)

    for attempt in range(1, max_reasks + 1):
        try:
            json.loads(strip_json_fences(response.content))
            if attempt > 1:
                logger.info("invoke_json: repair re-ask produced valid JSON (attempt %d)", attempt)
            return response
        except (json.JSONDecodeError, TypeError) as err:
            logger.warning("invoke_json: reply is not valid JSON (attempt %d): %s — re-asking", attempt, err)
            previous = response.content if isinstance(response.content, str) else str(response.content)
            response = llm.invoke(
                [
                    # Rebuild the original message with its image blocks — a
                    # text-only repair round would silently drop pasted
                    # screenshots the first attempt could see.
                    HumanMessage(content=build_multimodal_content(prompt, image_paths)),
                    AIMessage(content=previous),
                    HumanMessage(
                        content=(
                            f"Your previous reply was not valid JSON ({err}). "
                            "Reply again with ONLY the corrected JSON — no prose, no code fences."
                        )
                    ),
                ]
            )
            track_usage(response)

    # Final validation purely for logging — the caller's parser + deterministic
    # fallback handle an invalid reply gracefully either way.
    try:
        json.loads(strip_json_fences(response.content))
        logger.info("invoke_json: repair re-ask produced valid JSON")
    except (json.JSONDecodeError, TypeError) as err:
        logger.warning("invoke_json: reply still invalid after %d re-ask(s): %s — caller fallback", max_reasks, err)
    return response
