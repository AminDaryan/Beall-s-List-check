import json
import os
import litellm
from dotenv import load_dotenv

load_dotenv()

# Suppress litellm's verbose startup/request logging
litellm.suppress_debug_info = True
litellm.drop_params = True
os.environ.setdefault("LITELLM_LOG", "ERROR")

# ── Provider selection ─────────────────────────────────────────────────────────
# Set LLM_PROVIDER in .env to switch backends without touching code.
# Supported values: azure | openai | anthropic | mistral | ollama | gemini
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure").lower()

# ── Azure OpenAI credentials (existing env var names, kept for compatibility) ──
OPENAI_KEY = os.getenv("OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")

# Map to the env var names litellm expects for Azure
if LLM_PROVIDER == "azure":
    if OPENAI_KEY:
        os.environ.setdefault("AZURE_API_KEY", OPENAI_KEY)
    if AZURE_OPENAI_ENDPOINT:
        os.environ.setdefault("AZURE_API_BASE", AZURE_OPENAI_ENDPOINT)
    if AZURE_OPENAI_API_VERSION:
        os.environ.setdefault("AZURE_API_VERSION", AZURE_OPENAI_API_VERSION)

# ── Deployment / model list ────────────────────────────────────────────────────
# For Azure: deployment names (e.g. "gpt-5-2025-08-07").
# For other providers: model names (e.g. "claude-3-5-sonnet-20241022").
# Supports JSON array or comma-separated: AZURE_OPENAI_DEPLOYMENT=["m1","m2"]
_raw_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
try:
    _parsed = json.loads(_raw_deployment)
    if isinstance(_parsed, list):
        AZURE_OPENAI_DEPLOYMENTS = [str(d).strip() for d in _parsed if str(d).strip()]
    else:
        AZURE_OPENAI_DEPLOYMENTS = [str(_parsed).strip()]
except (json.JSONDecodeError, TypeError):
    AZURE_OPENAI_DEPLOYMENTS = [d.strip() for d in _raw_deployment.split(",") if d.strip()]

AZURE_OPENAI_DEPLOYMENT = AZURE_OPENAI_DEPLOYMENTS[0]  # backward compat


def _prefixed_model(name: str) -> str:
    """Return the litellm model string for the configured provider."""
    if "/" in name:
        return name  # already prefixed
    if LLM_PROVIDER == "azure":
        return f"azure/{name}"
    if LLM_PROVIDER in ("anthropic", "mistral", "ollama", "gemini"):
        return f"{LLM_PROVIDER}/{name}"
    return name  # openai and others: bare name is fine


# Minimal per-provider credential requirements, checked by
# validate_provider_config() so a missing key/endpoint fails with a clear,
# actionable message instead of a cryptic litellm error deep in the stack
# (Azure with an unset endpoint raises the infamous "argument of type 'NoneType'
# is not iterable").  Each tuple is (env var litellm reads, .env name the user
# edits) — they differ for Azure, where openai_client maps the legacy names.
_PROVIDER_REQUIREMENTS = {
    "azure": [("AZURE_API_KEY", "OPENAI_KEY"),
              ("AZURE_API_BASE", "AZURE_OPENAI_ENDPOINT"),
              ("AZURE_API_VERSION", "AZURE_OPENAI_API_VERSION")],
    "openai": [("OPENAI_API_KEY", "OPENAI_API_KEY")],
    "anthropic": [("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")],
    "mistral": [("MISTRAL_API_KEY", "MISTRAL_API_KEY")],
    "gemini": [("GEMINI_API_KEY", "GEMINI_API_KEY")],
    "ollama": [],   # local server — no key required
}


def validate_provider_config():
    """Raise a clear error if the active provider's credentials are missing.

    Call this early — before any slow work — so a misconfigured ``.env`` fails
    fast with a message naming the exact ``.env`` line(s) to fill, rather than
    crashing deep inside litellm on the first request (and only after the long
    deterministic pass has already run).  The fast, no-LLM check is unaffected.
    """
    missing = [hint for env, hint in _PROVIDER_REQUIREMENTS.get(LLM_PROVIDER, [])
               if not (os.environ.get(env) or "").strip()]
    if missing:
        raise RuntimeError(
            f"LLM is not configured: LLM_PROVIDER={LLM_PROVIDER!r} but these .env "
            f"value(s) are empty: {', '.join(missing)}. Fill them in your .env "
            "(see .env.example) and re-run. (The fast check, bealls_list_check.py, "
            "needs no LLM and is unaffected.)"
        )


# ── Thin wrapper that preserves client.chat.completions.create() interface ─────

class _Completions:
    def create(self, *, model, messages, max_completion_tokens=None,
               response_format=None, **kwargs):
        validate_provider_config()      # clear error if the key/endpoint is unset
        call_kwargs = dict(
            model=_prefixed_model(model),
            messages=messages,
            **kwargs,
        )
        if max_completion_tokens is not None:
            call_kwargs["max_completion_tokens"] = max_completion_tokens
        if response_format is not None:
            call_kwargs["response_format"] = response_format
        # Reasoning models (gpt-5, o-series) otherwise default to heavy reasoning,
        # which is slow and costly for a simple grounded classification.  Cap it
        # (override via LLM_REASONING_EFFORT).  litellm.drop_params=True drops it
        # automatically for models that don't support the parameter.
        call_kwargs.setdefault("reasoning_effort", os.getenv("LLM_REASONING_EFFORT", "low"))
        return litellm.completion(**call_kwargs)


class _Chat:
    def __init__(self):
        self.completions = _Completions()


class _LiteLLMClient:
    def __init__(self):
        self.chat = _Chat()


client = _LiteLLMClient()
