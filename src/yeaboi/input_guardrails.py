"""Input guardrails — validate and sanitize user input before it reaches the agent.

# See docs: "Guardrails" — three lines of defence (Input layer)

Four-layer input validation, cheapest first:

1. **Length cap** — rejects inputs longer than MAX_INPUT_CHARS (regex, instant).
2. **Prompt injection detection** — catches override/jailbreak patterns (regex, instant).
3. **Profanity filter** — catches obvious abuse (regex, instant).
4. **Allowlist + LLM classifier** — allowlist passes known-good project inputs
   instantly (regex, free).  Only inputs that fail the allowlist go to a cheap
   LLM classifier (Haiku/gpt-4o-mini/Flash) for a RELEVANT/OFF_TOPIC check.
   Falls back to allowing the input on classifier failure — the system prompt
   is the safety net.
"""

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Length cap
# ---------------------------------------------------------------------------

MAX_INPUT_CHARS: int = 5_000
"""Maximum characters accepted per user input.

Generous enough for detailed project descriptions and multi-paragraph
answers, but prevents accidental pastes of entire files or deliberate
context-window flooding.
"""

# ---------------------------------------------------------------------------
# Prompt injection patterns
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
        r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
        r"forget\s+(all\s+)?(your|previous|prior)\s+(instructions?|prompts?|rules?)",
        r"you\s+are\s+now\s+(a|an|the)\s+",
        r"new\s+instructions?\s*:",
        r"system\s*:\s*you\s+are",
        r"<\s*/?\s*system\s*>",
        r"\bact\s+as\s+(a|an|the)\s+(?!scrum|product|project)",
        r"override\s+(your|the|all)\s+(instructions?|prompts?|rules?|guidelines?)",
        r"pretend\s+(you\s+are|to\s+be)\s+",
    )
]

# ---------------------------------------------------------------------------
# Profanity — fast regex pre-check (no LLM call needed for obvious abuse)
# ---------------------------------------------------------------------------

_PROFANITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(f+u+c*k|sh[i1]+t|stfu|wtf)\b",
        r"\b(bitch|asshole|dickhead|bastard|dumbass|retard)\b",
        r"\b(dirty|filthy|nasty|naughty)\s+(boy|boii?|girl|bitch|slut|dog)\b",
        r"\b(suck\s*(my|it|this)|blow\s*me|eat\s*my|kiss\s*my\s*a)\b",
    )
]

# ---------------------------------------------------------------------------
# Allowlist — known-good patterns that skip the LLM classifier
# ---------------------------------------------------------------------------

# Short commands and questionnaire responses
_ALLOWLIST_EXACT: frozenset[str] = frozenset(
    {
        # Questionnaire commands
        "yes",
        "no",
        "y",
        "n",
        "skip",
        "defaults",
        "confirm",
        "back",
        "continue",
        "done",
        "ok",
        "sure",
        "start",
        "analyse",
        "analyze",
        "go",
        "proceed",
        "accept",
        "reject",
        "edit",
        "export",
        # Choice answers (numbers)
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        # Common short answers
        "none",
        "n/a",
        "na",
        "not sure",
        "not yet",
        "no idea",
        "i don't know",
        "idk",
        "tbd",
        "to be determined",
        "greenfield",
        "existing codebase",
        "hybrid",
        "monorepo",
        "multi-repo",
        "microservices",
        "monolith",
        "both",
        "jira",
        "markdown",
        # Sprint lengths
        "1 week",
        "2 weeks",
        "3 weeks",
        "4 weeks",
    }
)

# Patterns that indicate project-relevant content (case-insensitive).
# If ANY of these match, the input is considered relevant — instant pass.
_ALLOWLIST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        # --- Numbers and quantities ---
        r"\d+\s*(developer|engineer|dev|person|people|member|sprint|week|month|point|story)",
        r"\d+\s*(pts?|sp)\b",  # story points
        r"\b(team\s*(of|size|is)|velocity\s*(is|of|\d))",
        # --- Tech stack / languages / frameworks ---
        r"\b(python|java|javascript|typescript|go|golang|rust|ruby|php|swift|kotlin|c\+\+|c#|scala"
        r"|elixir|dart|perl|r\b|lua|haskell|clojure|erlang)\b",
        r"\b(react|angular|vue|svelte|next\.?js|nuxt|remix|gatsby|astro)\b",
        r"\b(node\.?js|express|fastapi|django|flask|rails|spring|laravel|nest\.?js|gin|fiber|echo)\b",
        r"\b(postgres|postgresql|mysql|mariadb|mongodb|redis|elasticsearch|dynamodb|sqlite|cassandra"
        r"|cockroachdb|supabase|firebase|neo4j|influxdb)\b",
        r"\b(aws|azure|gcp|google\s*cloud|heroku|vercel|netlify|cloudflare|digitalocean|linode)\b",
        r"\b(docker|kubernetes|k8s|terraform|ansible|pulumi|helm|argo|jenkins|circleci|github\s*actions"
        r"|gitlab\s*ci|azure\s*devops|bitbucket\s*pipelines)\b",
        r"\b(graphql|rest\s*api|grpc|websocket|mqtt|rabbitmq|kafka|nats|celery|sidekiq)\b",
        r"\b(tailwind|bootstrap|material\s*ui|chakra|styled|css|sass|less)\b",
        r"\b(prisma|sequelize|typeorm|sqlalchemy|hibernate|drizzle|knex)\b",
        r"\b(jest|pytest|junit|mocha|cypress|playwright|selenium|vitest|rspec)\b",
        r"\b(webpack|vite|esbuild|rollup|turbopack|parcel|babel)\b",
        r"\b(nginx|apache|caddy|traefik|envoy|haproxy)\b",
        r"\b(auth0|okta|keycloak|cognito|oauth|jwt|saml|sso|ldap)\b",
        r"\b(stripe|paypal|twilio|sendgrid|mailgun|slack|s3|sqs|sns|lambda)\b",
        r"\b(openai|anthropic|claude|gpt|llm|langchain|langgraph|hugging\s*face|ai\s*agent|ai\s*model|ai|ml"
        r"|machine\s*learning|deep\s*learning|neural|rag|embeddings?|vector\s*db|fine\s*tun)\b",
        r"\b(git|github|gitlab|bitbucket|azure\s*devops)\b",
        # --- Project / software terms ---
        r"\b(api|endpoint|route|controller|service|model|schema|migration|database|db)\b",
        r"\b(frontend|backend|fullstack|full\s*stack|microservices?|monolith|serverless)\b",
        r"\b(deploy|deployment|release|pipeline|ci/?cd|staging|production|dev\s*env)\b",
        r"\b(sprint|epic|story|task|backlog|kanban|scrum|agile|standup|retro)\b",
        r"\b(mvp|prototype|poc|proof\s*of\s*concept|beta|alpha|launch|milestone)\b",
        r"\b(feature|bug|fix|refactor|tech\s*debt|legacy|migrate|migration)\b",
        r"\b(component|module|package|library|plugin|extension|widget|hook)\b",
        r"\b(auth|login|signup|registration|user|admin|role|permission|dashboard)\b",
        r"\b(payment|checkout|cart|order|invoice|subscription|billing|notification)\b",
        r"\b(search|filter|sort|paginate|upload|download|import|export|sync)\b",
        r"\b(test|testing|unit\s*test|integration\s*test|e2e|coverage|qa|quality)\b",
        r"\b(security|encryption|ssl|tls|https|cors|csrf|xss|injection|vulnerability)\b",
        r"\b(cache|caching|cdn|performance|optimization|latency|throughput|scalab)\b",
        r"\b(monitoring|logging|alerting|observability|metrics|traces|datadog|grafana)\b",
        r"\b(repository|repo|branch|pull\s*request|pr|merge|commit|code\s*review)\b",
        r"\b(docker|container|image|volume|network|compose|swarm|pod|cluster)\b",
        r"\b(webhook|callback|event|queue|pubsub|message\s*broker|stream)\b",
        r"\b(mobile|ios|android|react\s*native|flutter|xamarin|cordova|expo)\b",
        r"\b(responsive|accessibility|a11y|i18n|localization|l10n|rtl)\b",
        r"\b(design|figma|sketch|wireframe|mockup|prototype|ui|ux)\b",
        r"\b(documentation|docs|readme|changelog|wiki|confluence|notion)\b",
        r"\b(stakeholder|client|customer|user|product\s*owner|po|manager|lead)\b",
        r"\b(estimate|complexity|risk|blocker|dependency|constraint|scope|deadline)\b",
        r"\b(greenfield|brownfield|existing\s*codebase|hybrid|rewrite|rebuild)\b",
        # --- Project intent verbs and general nouns ---
        r"\b(build|create|develop|implement|design|architect|ship|deliver|maintain|scale)\b",
        r"\b(app|application|website|web\s*app|system|tool|portal|platform|service|product)\b",
        r"\b(agent|bot|chatbot|automation|workflow|integration|connector|adapter|wrapper)\b",
        # --- Business / domain terms ---
        r"\b(b2b|b2c|saas|paas|iaas|enterprise|startup|platform|marketplace)\b",
        r"\b(ecommerce|e-commerce|fintech|healthtech|edtech|proptech|insuretech)\b",
        r"\b(crm|erp|cms|lms|hrms|pos|booking|reservation|inventory|logistics)\b",
        r"\b(onboarding|workflow|approval|compliance|audit|reporting|analytics)\b",
        r"\b(revenue|pricing|subscription|freemium|trial|quota|tenant|multi-tenant)\b",
        # --- URLs ---
        r"https?://",
        r"\b(github\.com|gitlab\.com|bitbucket\.org|dev\.azure\.com)\b",
        # --- File paths and extensions ---
        r"\b[\w/]+\.(py|js|ts|tsx|jsx|go|rs|rb|java|kt|cs|php|yaml|yml|json|toml|md)\b",
        # --- Timeline / deadline phrases ---
        r"\b(by|before|after|within|in)\s+\d+\s*(day|week|month|sprint|quarter)",
        r"\b(q[1-4]|h[12])\s*\d{0,4}\b",  # Q1 2025, H2, etc.
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        r"\b(deadline|timeline|roadmap|eta|target\s*date|go\s*live|ship)\b",
        # --- Uncertainty / "I don't know" variations ---
        r"\b(not\s*sure|don'?t\s*know|no\s*idea|uncertain|unclear|tbd|undecided|maybe)\b",
        r"\b(haven'?t\s*decided|still\s*(deciding|figuring|working))\b",
        r"\b(no\s*(existing|known|specific|explicit|hard)|none\s*(yet|that|so\s*far))\b",
    )
]

# ---------------------------------------------------------------------------
# LLM off-topic classifier (only called when allowlist doesn't match)
# ---------------------------------------------------------------------------

_CLASSIFIER_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.5-flash",
    # ollama deliberately absent: .get() → None → the user's main model. A
    # second local model here would force Ollama to swap models in RAM on
    # every classifier call, which is far slower than just reusing the one
    # already loaded.
}

_CLASSIFIER_PROMPT = """\
You are a relevance classifier for a project planning tool that creates \
epics, user stories, sprints, and tasks for software projects.

Classify this user input as RELEVANT or OFF_TOPIC.

RELEVANT — could be a project planning answer (description, tech, team, timeline, etc.):
"e-commerce platform" → RELEVANT
"we need it by March" → RELEVANT
"React and Python" → RELEVANT
"not sure yet" → RELEVANT
"we have 3 backend devs and 1 designer" → RELEVANT

OFF_TOPIC — clearly unrelated to software project planning:
"do you love me" → OFF_TOPIC
"tell me a joke" → OFF_TOPIC
"whats up you dirty boii" → OFF_TOPIC
"show me the future" → OFF_TOPIC
"what is the meaning of life" → OFF_TOPIC
"can you sing" → OFF_TOPIC
"who won the world cup" → OFF_TOPIC

Respond with exactly one word: RELEVANT or OFF_TOPIC"""

_OFFTOPIC_MAX_LEN = 200


def check_input_length(text: str) -> str | None:
    """Return an error message if *text* exceeds the length cap, else None."""
    if len(text) > MAX_INPUT_CHARS:
        return (
            f"Input too long ({len(text):,} chars). "
            f"Maximum is {MAX_INPUT_CHARS:,} characters — please shorten your response."
        )
    return None


def check_profanity(text: str) -> str | None:
    """Return a warning if *text* contains obvious profanity, else None."""
    for pattern in _PROFANITY_PATTERNS:
        if pattern.search(text):
            return (
                "I'm a project planning agent — I can help with epics, stories, sprints, and tasks. "
                "Please enter a project-related response."
            )
    return None


def _passes_allowlist(text: str) -> bool:
    """Return True if text matches a known-good project input pattern.

    Checks exact matches first (command words, numbers), then regex
    patterns for tech terms, project vocabulary, URLs, etc.
    """
    lowered = text.strip().lower()

    # Exact match — questionnaire commands, numbers, short answers
    if lowered in _ALLOWLIST_EXACT:
        return True

    # Pure numbers are always valid (team size, sprint count, points, etc.)
    if lowered.replace(".", "").replace(",", "").strip().isdigit():
        return True

    # Regex patterns — tech stack, project terms, URLs, timelines
    for pattern in _ALLOWLIST_PATTERNS:
        if pattern.search(text):
            return True

    return False


def check_off_topic(text: str) -> str | None:
    """Check input relevance: allowlist first, then LLM classifier if needed.

    Only checks short inputs (≤200 chars). Long inputs are assumed to be
    project descriptions. Returns a redirect message if off-topic, None if
    relevant. On classifier error, returns None (system prompt is fallback).
    """
    if len(text) > _OFFTOPIC_MAX_LEN:
        return None

    # Fast path — allowlist match means it's relevant, no LLM call needed
    if _passes_allowlist(text):
        return None

    # Slow path — input didn't match any known-good pattern, ask the LLM
    try:
        from yeaboi import config as _config_module
        from yeaboi.agent import llm as _llm_module

        provider = _config_module.get_llm_provider()
        model = _CLASSIFIER_MODELS.get(provider)
        llm = _llm_module.get_llm(model=model, temperature=0.0)
        # Disable retries — this is a non-critical guardrail; failing fast is
        # better than blocking the REPL for seconds on API overload (529).
        # hasattr guard: ChatOllama has no max_retries field, and assigning an
        # undefined field to a pydantic model raises — which the except below
        # would silently turn into a disabled guardrail.
        if hasattr(llm, "max_retries"):
            llm.max_retries = 0
        # Local models (ChatOllama) generate on the user's CPU/GPU — an
        # uncapped call lets a think-by-default model (qwen3) burn seconds of
        # <think> tokens before its one-word verdict, on the input critical
        # path. Cap the generation and turn thinking off for this one call.
        # Constructor-level reasoning=False was deliberately avoided in
        # get_llm() (older servers reject the option for non-think models),
        # but here the except below already fails open, so the risk is zero.
        # Cloud LLMs lack both fields — the hasattr guards make this a no-op.
        if hasattr(llm, "num_predict"):
            llm.num_predict = 64
        if hasattr(llm, "reasoning"):
            llm.reasoning = False

        response = llm.invoke(f"{_CLASSIFIER_PROMPT}\n\nUser input: {text}")
        # Local think-by-default models can bury the verdict token in a
        # <think> block — strip it before matching.
        result = _llm_module.strip_think_tags(response.content).strip().upper()

        if "OFF_TOPIC" in result:
            return (
                "I'm a project planning agent — I can help with epics, stories, sprints, and tasks. "
                "Please enter a project-related response."
            )
    except Exception:
        logger.debug("Off-topic classifier failed, allowing input", exc_info=True)

    return None


def check_prompt_injection(text: str) -> str | None:
    """Return a warning message if *text* matches a known injection pattern, else None."""
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return "Your input looks like a prompt injection attempt and has been blocked. Please rephrase your answer."
    return None


def validate_input(text: str) -> str | None:
    """Run all input guardrails.  Returns the first error/warning, or None if clean.

    Order: length → injection → profanity (all regex, instant) → allowlist + LLM (last).
    """
    return check_input_length(text) or check_prompt_injection(text) or check_profanity(text) or check_off_topic(text)
