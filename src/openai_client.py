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
#
# The web UI chooses one per run instead, and passes it — with the model name —
# in the environment of the subprocess it starts.  That works without any special
# case here because this module reads both from the environment at import, and
# python-dotenv does not overwrite a variable that is already set: whatever the
# UI puts there wins over the .env file, and .env remains the default for a run
# started from the command line.
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
_DEFAULT_MODEL = "gpt-4o"


def _parse_names(raw):
    """Split a model/deployment setting into a list of names.

    One variable carries however many names a machine has access to, written in
    whichever of the three shapes is convenient: a bare name, a comma-separated
    list, or a JSON array.  Shared by every such variable rather than repeated
    per provider.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [name.strip() for name in raw.split(",") if name.strip()]
    if isinstance(parsed, list):
        return [str(name).strip() for name in parsed if str(name).strip()]
    return [str(parsed).strip()]


AZURE_OPENAI_DEPLOYMENTS = _parse_names(
    os.getenv("AZURE_OPENAI_DEPLOYMENT", _DEFAULT_MODEL)) or [_DEFAULT_MODEL]

AZURE_OPENAI_DEPLOYMENT = AZURE_OPENAI_DEPLOYMENTS[0]  # backward compat


def provider_models(provider=None):
    """The model/deployment names *provider* is configured with, in .env order.

    Read from ``LLM_MODELS_<PROVIDER>`` — ``LLM_MODELS_ANTHROPIC``,
    ``LLM_MODELS_OPENAI`` and so on — so one machine can carry a list per
    backend, which is what the web UI needs to offer the right names when the
    provider is switched for a single run.  For whichever provider ``.env``
    itself selects, ``AZURE_OPENAI_DEPLOYMENT`` is the fallback: that is where
    the names lived before there was more than one list, and it keeps working.

    An empty result is not an error.  It means only that this machine has not
    listed any names for that provider; the UI still accepts one typed in, and
    the check runs whatever name it is handed.
    """
    provider = (provider or LLM_PROVIDER).lower()
    names = _parse_names(os.getenv(f"LLM_MODELS_{provider.upper()}"))
    if not names and provider == LLM_PROVIDER:
        names = list(AZURE_OPENAI_DEPLOYMENTS)
    return names


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

# The providers this project documents and the web UI offers, in the order it
# offers them.  Not a limit on what litellm accepts: an LLM_PROVIDER that is not
# here is passed through untouched, so a backend nobody has written a
# requirements entry for still runs (and fails, if it must, in litellm's own
# words rather than in a guess made here).
SUPPORTED_PROVIDERS = tuple(_PROVIDER_REQUIREMENTS)


def missing_provider_config(provider=None):
    """The ``.env`` value names *provider* needs and does not have.

    Answers for **any** provider rather than only the active one, because the
    web UI offers the choice per run and has to report readiness for what is
    selected, not for what ``.env`` happens to name.

    Either name in each pair counts as set: the ``.env`` name is the one the
    user edits, and the litellm name is what this module maps it to for Azure —
    also the one anyone exporting variables by hand is likely to have used.
    """
    provider = (provider or LLM_PROVIDER).lower()
    return [dotenv_name
            for env_name, dotenv_name in _PROVIDER_REQUIREMENTS.get(provider, [])
            if not (os.environ.get(env_name) or os.environ.get(dotenv_name) or "").strip()]


def validate_provider_config(provider=None):
    """Raise a clear error if *provider*'s credentials are missing.

    Call this early — before any slow work — so a misconfigured ``.env`` fails
    fast with a message naming the exact ``.env`` line(s) to fill, rather than
    crashing deep inside litellm on the first request (and only after the long
    deterministic pass has already run).  The fast, no-LLM check is unaffected.

    *provider* defaults to the active one, which is what the checks pass.  The
    web UI names one explicitly: it asks about the provider its Step 3 has
    selected, which is not necessarily the one this process was started with.
    """
    provider = (provider or LLM_PROVIDER).lower()
    missing = missing_provider_config(provider)
    if missing:
        raise RuntimeError(
            f"LLM is not configured: LLM_PROVIDER={provider!r} but these .env "
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
