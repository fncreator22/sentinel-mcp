"""
sentinel_core/model_manager.py
=================================
Everything to do with WHICH model powers Stage 3 (the LLM reviewer), and
where it runs. This file is what makes the dashboard's "local model or
API key" switch actually work.

TWO WAYS TO RUN STAGE 3:
  1. "local"  — a model running on this machine via Ollama. We auto-detect
     what's installed by calling Ollama's own API (GET /api/tags) — the
     user never has to type a model name by hand if they've already
     pulled one.
  2. "api"    — any OpenAI-compatible chat completions API (OpenAI itself,
     or any self-hosted/third-party endpoint that mimics that schema),
     OR Anthropic's Messages API. The user supplies a base URL, model
     name, and API key via the dashboard.

CONFIG STORAGE, SPLIT ON PURPOSE:
  - config/model_config.yaml        <- committed to git. Provider choice,
                                        model name, base URL. NO secrets.
  - config/model_config.local.yaml  <- gitignored. Holds ONLY the API key,
                                        if one is set. Never leaves this
                                        machine, never printed to logs,
                                        never returned in full by the API
                                        (see mask_api_key below).

This split means you can safely commit/share model_config.yaml (e.g. via
the /export endpoint or just checking it into git) without ever leaking a
key, and a teammate cloning the repo just needs to drop their own key into
model_config.local.yaml (or paste it into the dashboard, which writes it
there for them).
"""

import json
import re
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Set

# ---- In-memory model cache (survives for the lifetime of the server process) --
# Maps  "<provider>:<last8ofkey>" -> model_id that last worked successfully.
# Avoids re-discovering the working model on every request.
_MODEL_CACHE: Dict[str, str] = {}

def _cache_key(provider: str, api_key: str) -> str:
    tail = api_key[-8:] if len(api_key) >= 8 else api_key
    return f"{provider}:{tail}"


def _clean_error(raw: str, provider: str = "api") -> str:
    """
    Convert any raw ProviderError / API response into a short, user-friendly
    message.  Never shows JSON blobs or stack traces.
    """
    low = raw.lower()
    
    if provider == "local":
        if "connection refused" in low or "could not reach" in low or "connectionrefused" in low:
            return "Could not reach Ollama. Make sure Ollama is running (ollama serve) and your Base URL is correct."
        if "404" in raw or "not found" in low:
            return "Model not found in Ollama. Try pulling it in your terminal first."
        if "no local model" in low:
            return raw
        # Remove any long error detail or JSON-like parts
        cleaned = re.sub(r'\{.*\}', '', raw, flags=re.DOTALL).strip(': \n')
        return cleaned[:200] if cleaned else raw[:200]

    if "quota" in low or "resource_exhausted" in low or "429" in raw:
        return "API quota exceeded for this key. Check your billing plan or try again later."
    if "401" in raw or "invalid api key" in low or "unauthorized" in low:
        return "Invalid API key — please check and re-enter your key."
    if "403" in raw or "forbidden" in low:
        return "Access denied. Your key may not have permission for this operation."
    if "404" in raw or "not found" in low:
        return "API endpoint not found. Check your Base URL in Model Settings."
    if "no internet" in low or "getaddrinfo" in low or "name resolution" in low:
        return "No internet connection. Check your network and try again."
    if "timed out" in low or "timeout" in low:
        return "Connection timed out. Try again in a moment."
    if "model name is empty" in low:
        return "No model selected. The system will auto-select one — click Test Connection."
    # Strip JSON blobs — anything between { } that is long
    cleaned = re.sub(r'\{.*\}', '', raw, flags=re.DOTALL).strip(': \n')
    if not cleaned:
        return raw[:200] if len(raw) > 200 else raw
    return cleaned[:200] if len(cleaned) > 200 else cleaned


def _preference_order(provider: str) -> List[str]:
    """Ordered list of model IDs to try for each provider, most preferred first."""
    if provider == "gemini":
        return [
            "gemini-flash-latest",
            "gemini-pro-latest",
            "gemini-flash-lite-latest",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash-latest",
            "gemini-1.5-pro-latest",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ]
    if provider == "openai":
        return ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]
    if provider == "anthropic":
        return [
            "claude-3-5-haiku-20241022",
            "claude-3-5-sonnet-20240620",
            "claude-3-haiku-20240307",
            "claude-3-opus-20240229",
        ]
    return []


def _is_quota_error(msg: str) -> bool:
    """True when the error indicates a per-model quota / rate-limit issue."""
    low = msg.lower()
    return any(x in low for x in [
        "quota", "resource_exhausted", "429", "rate limit",
        "requests per", "tokens per",
    ])


def _is_model_specific_error(msg: str) -> bool:
    """True when the error is specific to this model ID (e.g. 404, deprecated, 400 Bad Request)."""
    low = msg.lower()
    if any(x in low for x in ["invalid api key", "unauthorized", "no internet", "network", "getaddrinfo", "401", "403"]):
        return False
    return _is_quota_error(msg) or any(x in low for x in [
        "not found", "no longer available", "not available", "404", "does not support",
        "invalid model", "unknown model", "bad request", "400"
    ])

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_PATH = CONFIG_DIR / "model_config.yaml"
SECRETS_PATH = CONFIG_DIR / "model_config.local.yaml"

DEFAULT_CONFIG = {
    "active_provider": "local",  # "local" | "api"
    "local": {
        "ollama_base_url": "http://localhost:11434",
        "selected_model": None,  # e.g. "llama3.2" — set once the user picks one
    },
    "api": {
        "provider": "openai",  # "openai" | "anthropic" | "custom"
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
}

# Sensible defaults per API provider, so the dashboard can prefill the
# base URL the moment a user picks a provider from a dropdown.
API_PROVIDER_PRESETS = {
    "openai":    {"base_url": "https://api.openai.com/v1",                         "model": "gpt-4o-mini"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1",                      "model": "claude-3-5-sonnet-20240620"},
    "gemini":    {"base_url": "https://generativelanguage.googleapis.com/v1beta",   "model": "gemini-1.5-flash-latest"},
    "custom":    {"base_url": "", "model": ""},
}

# The guaranteed-working default model for each provider.
# Used as an automatic fallback when the configured model field is blank/None.
PROVIDER_DEFAULT_MODELS: Dict[str, str] = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-20240620",
    "gemini":    "gemini-1.5-flash-latest",
}

# Static model lists — used as a fallback when no API key is saved yet.
# Once the user saves their key, the dashboard replaces these with live data.
PROVIDER_MODELS_STATIC: Dict[str, list] = {
    "openai": [
        "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo",
    ],
    "anthropic": [
        "claude-3-5-sonnet-20240620", "claude-3-5-haiku-20241022",
        "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307",
    ],
    "gemini": [
        "gemini-1.5-flash-latest", "gemini-1.5-pro-latest",
        "gemini-2.0-flash-exp", "gemini-1.0-pro",
    ],
    "custom": [],
}

# ---- Config load / save ----------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config() -> Dict[str, Any]:
    """
    Loads config/model_config.yaml (non-secret) merged with
    config/model_config.local.yaml (secret, if present). Returns the
    FULL config including the real api_key — only call this server-side,
    never hand the return value directly back to an HTTP response.
    """
    config = dict(DEFAULT_CONFIG)

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            on_disk = yaml.safe_load(f) or {}
        config = _deep_merge(config, on_disk)

    api_key = None
    if SECRETS_PATH.exists():
        with open(SECRETS_PATH, "r") as f:
            secrets = yaml.safe_load(f) or {}
        api_key = secrets.get("api_key")

    config.setdefault("api", {})["api_key"] = api_key
    return config


def save_config(new_config: Dict[str, Any]) -> None:
    """
    Splits the incoming config: the api_key (if any) goes into the
    gitignored secrets file, everything else goes into the committed
    config file. Called by the dashboard's "Save model settings" button.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    api_key = None
    if "api" in new_config and isinstance(new_config["api"], dict):
        api_key = new_config["api"].pop("api_key", None)

    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(new_config, f, sort_keys=False)

    # Only touch the secrets file if a key was actually provided, so
    # re-saving other settings doesn't accidentally wipe a stored key
    # unless the user explicitly clears it (sends an empty string).
    if api_key is not None:
        with open(SECRETS_PATH, "w") as f:
            yaml.safe_dump({"api_key": api_key}, f)


def mask_api_key(key: Optional[str]) -> Optional[str]:
    """Never send a real key back to the browser. Show last 4 chars only."""
    if not key:
        return None
    if len(key) <= 4:
        return "****"
    return f"{'*' * (len(key) - 4)}{key[-4:]}"


def masked_config() -> Dict[str, Any]:
    """Safe-to-return-over-HTTP version of the config: api_key is masked."""
    config = load_config()
    config = json.loads(json.dumps(config))  # cheap deep copy
    if "api" in config:
        config["api"]["api_key_set"] = bool(config["api"].get("api_key"))
        config["api"]["api_key_masked"] = mask_api_key(config["api"].get("api_key"))
        config["api"].pop("api_key", None)
    return config


# ---- Local model auto-detection --------------------------------------------

@dataclass
class LocalModel:
    name: str
    size_bytes: Optional[int] = None


def detect_local_models(base_url: Optional[str] = None) -> List[LocalModel]:
    """
    Calls Ollama's own API to list installed models. Returns an empty list
    (never raises) if Ollama isn't running or isn't installed — the
    dashboard uses an empty list as the signal to show install instructions
    instead of a model picker.
    """
    base_url = base_url or DEFAULT_CONFIG["local"]["ollama_base_url"]
    url = f"{base_url.rstrip('/')}/api/tags"

    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionRefusedError, ValueError):
        return []

    models = []
    for m in data.get("models", []):
        models.append(LocalModel(name=m.get("name") or m.get("model", "unknown"),
                                  size_bytes=m.get("size")))
    return models


def is_ollama_running(base_url: Optional[str] = None) -> bool:
    return len(detect_local_models(base_url)) >= 0 and _ping_ollama(base_url)


def _ping_ollama(base_url: Optional[str] = None) -> bool:
    base_url = base_url or DEFAULT_CONFIG["local"]["ollama_base_url"]
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=2):
            return True
    except Exception:
        return False


# ---- Unified "generate text" providers --------------------------------------

class ProviderError(Exception):
    pass


class OllamaProvider:
    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def generate(self, prompt: str, timeout: int = 30) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},
        }
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("response", "")
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="ignore")
            if e.code == 404:
                raise ProviderError(f"Model '{self.model}' not found in Ollama. Run: ollama pull {self.model}")
            raise ProviderError(f"Ollama API returned HTTP {e.code}: {raw}")
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError) as e:
            raise ProviderError(f"Could not reach local Ollama at {self.base_url}: {e}")


class OpenAICompatibleProvider:
    """Works for OpenAI itself, and any self-hosted/third-party endpoint
    that mirrors the OpenAI chat completions schema (many do)."""

    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def generate(self, prompt: str, timeout: int = 30) -> str:
        model_clean = (self.model or "").strip()
        if not model_clean:
            raise ProviderError("Model name is empty. Please enter or select a valid model name.")
        if " " in model_clean:
            raise ProviderError(
                f"Invalid model name '{self.model}'. Model names cannot contain spaces. "
                f"Please choose a valid model identifier (e.g., 'gpt-4o-mini') from the dropdown rather than a descriptive name."
            )
        payload = {
            "model": model_clean,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }

        try:
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="ignore")
            raise ProviderError(_clean_error(f"{e.code} {raw}"))
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError, KeyError, IndexError) as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["getaddrinfo", "name resolution", "nodename", "network unreachable", "no route"]):
                raise ProviderError("No internet connection. Check your network.")
            raise ProviderError(_clean_error(str(e)))


class GeminiProvider:
    """Google Gemini via the native generateContent API.
    Uses X-goog-api-key header — NOT OpenAI-compatible."""

    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def generate(self, prompt: str, timeout: int = 30) -> str:
        model_clean = (self.model or "").strip()
        if not model_clean:
            raise ProviderError("Model name is empty. Please enter or select a valid model name.")
        if " " in model_clean:
            raise ProviderError(
                f"Invalid model name '{self.model}'. Model names cannot contain spaces. "
                f"Please choose a valid model identifier (e.g., 'gemini-1.5-flash-latest') from the dropdown rather than a descriptive name."
            )
        url = f"{self.base_url}/models/{model_clean}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500},
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-goog-api-key": self.api_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                # Gemini response: candidates[0].content.parts[0].text
                return body["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="ignore")
            raise ProviderError(_clean_error(f"{e.code} {raw}"))
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError, KeyError, IndexError) as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["getaddrinfo", "name resolution", "nodename", "network unreachable", "no route"]):
                raise ProviderError("No internet connection. Check your network.")
            raise ProviderError(_clean_error(str(e)))



class AnthropicProvider:
    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def generate(self, prompt: str, timeout: int = 30) -> str:
        model_clean = (self.model or "").strip()
        if not model_clean:
            raise ProviderError("Model name is empty. Please enter or select a valid model name.")
        if " " in model_clean:
            raise ProviderError(
                f"Invalid model name '{self.model}'. Model names cannot contain spaces. "
                f"Please choose a valid model identifier (e.g., 'claude-3-5-sonnet-20240620') from the dropdown rather than a descriptive name."
            )
        payload = {
            "model": model_clean,
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            req = urllib.request.Request(
                f"{self.base_url}/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return "".join(block.get("text", "") for block in body.get("content", []))
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="ignore")
            raise ProviderError(_clean_error(f"{e.code} {raw}"))
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError) as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["getaddrinfo", "name resolution", "nodename", "network unreachable", "no route"]):
                raise ProviderError("No internet connection. Check your network.")
            raise ProviderError(_clean_error(str(e)))


# ---- Provider factory helper ---------------------------------------------------

def _make_provider(provider_name: str, model: str, base_url: str, api_key: str):
    """Instantiate the right provider class for the given settings."""
    if provider_name == "anthropic":
        return AnthropicProvider(model=model, base_url=base_url, api_key=api_key)
    if provider_name == "gemini":
        return GeminiProvider(model=model, base_url=base_url, api_key=api_key)
    return OpenAICompatibleProvider(model=model, base_url=base_url, api_key=api_key)


def _ranked_models(provider: str, base_url: str, api_key: str) -> List[str]:
    """
    Return an ordered list of model IDs to try for the given provider.
    Preference list first, restricted to models actually available on the key.
    Falls back to the full preference list if the /models endpoint is unreachable.
    """
    prefs = _preference_order(provider)
    if not api_key:
        return prefs or [PROVIDER_DEFAULT_MODELS.get(provider, "gpt-4o-mini")]

    res = list_available_models(provider, base_url, api_key)
    if res.get("ok") and res.get("models"):
        available: Set[str] = set(res["models"])
        # Keep preference order but restrict to models we know exist
        ordered = [m for m in prefs if m in available]
        if not ordered:
            # None of our preferences are available — rank the available ones
            # by matching substrings we like
            ordered = sorted(available, key=lambda m: (
                0 if "flash-lite" in m else
                1 if "flash" in m else
                2 if "pro" in m else
                3
            ))
        return ordered or [PROVIDER_DEFAULT_MODELS.get(provider, "gpt-4o-mini")]

    # /models call failed — return static preference list as best effort
    return prefs or [PROVIDER_DEFAULT_MODELS.get(provider, "gpt-4o-mini")]


def select_best_model(provider: str, base_url: str, api_key: str) -> str:
    """Return the single best model ID for this provider."""
    # Use cached model if available (set after a successful test)
    cache_k = _cache_key(provider, api_key) if api_key else None
    if cache_k and cache_k in _MODEL_CACHE:
        return _MODEL_CACHE[cache_k]
    ranked = _ranked_models(provider, base_url, api_key)
    return ranked[0] if ranked else PROVIDER_DEFAULT_MODELS.get(provider, "gpt-4o-mini")


def get_active_provider():
    """
    Reads the current config and returns a ready-to-use provider instance
    with a .generate(prompt) -> str method. This is the ONLY function
    llm_reviewer.py needs to call - it doesn't need to know whether it's
    talking to Ollama, OpenAI, or Anthropic underneath.
    """
    config = load_config()

    if config["active_provider"] == "local":
        local = config["local"]
        selected_model = local.get("selected_model")
        if not selected_model:
            available = detect_local_models(local.get("ollama_base_url"))
            if available:
                selected_model = available[0].name
            else:
                raise ProviderError(
                    "No local model selected and no Ollama models detected. Make sure Ollama is running (ollama serve) and you have pulled a model (e.g. 'ollama pull qwen2.5-coder:7b')."
                )
        return OllamaProvider(
            model=selected_model,
            base_url=local.get("ollama_base_url", DEFAULT_CONFIG["local"]["ollama_base_url"]),
        )

    # active_provider == "api"
    api = config["api"]
    if not api.get("api_key"):
        raise ProviderError(
            "No API key saved. Open Model Settings and paste your key."
        )

    provider_name = api.get("provider", "openai")
    base_url = api.get("base_url", "")
    api_key = api.get("api_key")

    # Use cached model if available, otherwise pick best
    model = select_best_model(provider_name, base_url, api_key)
    return _make_provider(provider_name, model, base_url, api_key)


def test_active_provider() -> Dict[str, Any]:
    """
    Sends a trivial prompt through the configured provider.
    Automatically tries multiple models when quota / rate-limit errors occur.
    Never returns raw JSON or API error blobs to the caller.
    """
    config = load_config()

    if config["active_provider"] == "local":
        try:
            prov = get_active_provider()
            t0 = time.time()
            resp = prov.generate("Reply with the single word: OK")
            ms = int((time.time() - t0) * 1000)
            return {"ok": True, "message": f"Connected to local model '{prov.model}' ({ms} ms)."}
        except ProviderError as e:
            return {"ok": False, "message": _clean_error(str(e), provider="local")}

    # --- API provider ---
    api = config.get("api", {})
    provider_name = api.get("provider", "openai")
    base_url = api.get("base_url", "")
    api_key = api.get("api_key", "")

    if not api_key:
        return {"ok": False, "message": "No API key saved. Open Model Settings and paste your key."}

    # Build the ordered list of models to try
    cache_k = _cache_key(provider_name, api_key)
    models_to_try = _ranked_models(provider_name, base_url, api_key)

    # If we have a cached winner, try it first before re-ranking
    if cache_k in _MODEL_CACHE:
        cached = _MODEL_CACHE[cache_k]
        if cached in models_to_try:
            models_to_try.remove(cached)
        models_to_try.insert(0, cached)

    last_err = "Could not connect — all models failed."
    skipped: List[str] = []

    for model_id in models_to_try:
        try:
            prov = _make_provider(provider_name, model_id, base_url, api_key)
            t0 = time.time()
            resp = prov.generate("Reply with the single word: OK", timeout=20)
            ms = int((time.time() - t0) * 1000)
            # Success — cache this model so future requests use it directly
            _MODEL_CACHE[cache_k] = model_id
            note = f" (skipped {len(skipped)} model(s) with quota issues)" if skipped else ""
            return {
                "ok": True,
                "message": f"Connected! Model: {model_id} ({ms} ms){note}.",
            }
        except ProviderError as e:
            msg = str(e)
            if _is_model_specific_error(msg):
                skipped.append(model_id)
                last_err = f"Failed on model '{model_id}': {_clean_error(msg)}"
                continue  # silently move to the next model
            # Key/network error — stop immediately
            _MODEL_CACHE.pop(cache_k, None)
            return {"ok": False, "message": _clean_error(msg)}
        except Exception as e:
            _MODEL_CACHE.pop(cache_k, None)
            return {"ok": False, "message": _clean_error(str(e))}

    # All models failed
    _MODEL_CACHE.pop(cache_k, None)
    if skipped:
        return {
            "ok": False,
            "message": f"Connection failed after testing {len(skipped)} available model(s). {last_err}",
        }
    return {"ok": False, "message": last_err}



def list_available_models(provider: str, base_url: str, api_key: str) -> Dict[str, Any]:
    """
    Calls the provider's live /models endpoint using the saved API key and
    returns a list of model IDs the account has access to.

    Returns:
        {
          "ok": True/False,
          "models": ["model-id-1", "model-id-2", ...],   # sorted
          "error": "Human-readable error string if ok=False"
        }

    Never raises — all errors become {"ok": False, "error": "..."}.
    """
    base_url = (base_url or "").rstrip("/")
    try:
        # ------------------------------------------------------------------ #
        # OpenAI  (and any OpenAI-compatible custom endpoint)                 #
        # ------------------------------------------------------------------ #
        if provider in ("openai", "custom"):
            req = urllib.request.Request(
                f"{base_url}/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # OpenAI schema: { "data": [ {"id": "gpt-4o", ...}, ... ] }
            details = sorted(
                [{"id": m["id"], "display_name": m.get("id", m["id"])} for m in data.get("data", []) if m.get("id")],
                key=lambda x: x["id"],
            )
            return {"ok": True, "models": [d["id"] for d in details], "model_details": details, "error": None}

        # ------------------------------------------------------------------ #
        # Anthropic                                                            #
        # ------------------------------------------------------------------ #
        if provider == "anthropic":
            req = urllib.request.Request(
                f"{base_url}/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # Anthropic schema: { "data": [ {"id": "claude-3-5-sonnet-...", "display_name": "...", ...} ] }
            details = sorted(
                [{"id": m["id"], "display_name": m.get("display_name", m["id"])} for m in data.get("data", []) if m.get("id")],
                key=lambda x: x["id"],
            )
            return {"ok": True, "models": [d["id"] for d in details], "model_details": details, "error": None}

        # ------------------------------------------------------------------ #
        # Google Gemini — with full pagination support                        #
        # ------------------------------------------------------------------ #
        if provider == "gemini":
            def _strip_prefix(n: str) -> str:
                return n.split("/")[-1] if "/" in n else n

            all_details: List[Dict[str, str]] = []
            page_token: Optional[str] = None

            # Fetch all pages (Gemini paginates large model lists)
            for _ in range(20):  # safety cap at 20 pages (~2000 models)
                paged_url = f"{base_url}/models?key={urllib.parse.quote(api_key, safe='')}&pageSize=100"
                if page_token:
                    paged_url += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
                with urllib.request.urlopen(paged_url, timeout=10) as resp:
                    page_data = json.loads(resp.read().decode("utf-8"))

                for m in page_data.get("models", []):
                    if m.get("name") and "generateContent" in m.get("supportedGenerationMethods", []):
                        model_id = _strip_prefix(m["name"])
                        all_details.append({
                            "id": model_id,
                            "display_name": m.get("displayName", model_id),
                        })

                page_token = page_data.get("nextPageToken")
                if not page_token:
                    break

            all_details.sort(key=lambda x: x["id"])
            return {
                "ok": True,
                "models": [d["id"] for d in all_details],
                "model_details": all_details,
                "error": None,
            }

        return {"ok": False, "models": [], "model_details": [], "error": f"Unknown provider: {provider}"}

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="ignore")
        except Exception:
            pass
        code = e.code
        if code == 401:
            return {"ok": False, "models": [], "error": "Invalid API key — please check and re-enter it."}
        if code == 403:
            return {"ok": False, "models": [], "error": "Access denied. Your key may not have permission to list models."}
        if code == 404:
            return {"ok": False, "models": [], "error": "Model listing endpoint not found. Check the Base URL."}
        if code == 429:
            return {"ok": False, "models": [], "error": "Rate limit reached. Wait a moment and try again."}
        return {"ok": False, "models": [], "error": f"API returned HTTP {code}. Response: {body[:200]}"}

    except (urllib.error.URLError, TimeoutError, ConnectionRefusedError) as e:
        err_str = str(e).lower()
        if any(x in err_str for x in ["getaddrinfo", "name resolution", "nodename", "network unreachable", "no route"]):
            return {"ok": False, "models": [], "error": "No internet connection. Check your network and try again."}
        if "timed out" in err_str or isinstance(e, TimeoutError):
            return {"ok": False, "models": [], "error": "Connection timed out. The API may be slow — try again in a moment."}
        return {"ok": False, "models": [], "error": f"Could not reach the API endpoint. Check the Base URL."}

    except Exception as e:
        return {"ok": False, "models": [], "error": f"Unexpected error while fetching models: {type(e).__name__}: {e}"}


if __name__ == "__main__":
    print("Detected local Ollama models:", detect_local_models())
    print("Current config (masked):", masked_config())
    print("Test result:", test_active_provider())

