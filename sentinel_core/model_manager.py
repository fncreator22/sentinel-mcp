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
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

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
        payload = {
            "model": self.model,
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
            raise ProviderError(f"API returned HTTP {e.code}: {e.read().decode(errors='ignore')}")
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError, KeyError, IndexError) as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["getaddrinfo", "name resolution", "nodename", "network unreachable", "no route"]):
                raise ProviderError(
                    "No internet connection. Cannot reach the OpenAI API. "
                    "Check your network and try again, or switch to a local model."
                )
            raise ProviderError(f"Could not complete API request: {e}")


class GeminiProvider:
    """Google Gemini via the native generateContent API.
    Uses X-goog-api-key header — NOT OpenAI-compatible."""

    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def generate(self, prompt: str, timeout: int = 30) -> str:
        url = f"{self.base_url}/models/{self.model}:generateContent"
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
            raise ProviderError(f"Gemini API returned HTTP {e.code}: {e.read().decode(errors='ignore')}")
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError, KeyError, IndexError) as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["getaddrinfo", "name resolution", "nodename", "network unreachable", "no route"]):
                raise ProviderError(
                    "No internet connection. Cannot reach the Gemini API. "
                    "Check your network connection and try again, or switch to a local model in Model Settings."
                )
            raise ProviderError(f"Could not complete Gemini API request: {e}")



class AnthropicProvider:
    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def generate(self, prompt: str, timeout: int = 30) -> str:
        payload = {
            "model": self.model,
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
            raise ProviderError(f"API returned HTTP {e.code}: {e.read().decode(errors='ignore')}")
        except (urllib.error.URLError, TimeoutError, ConnectionRefusedError) as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["getaddrinfo", "name resolution", "nodename", "network unreachable", "no route"]):
                raise ProviderError(
                    "No internet connection. Cannot reach the Anthropic API. "
                    "Check your network and try again, or switch to a local model."
                )
            raise ProviderError(f"Could not complete API request: {e}")


def get_active_provider():
    """
    Reads the current config and returns a ready-to-use provider instance
    with a .generate(prompt) -> str method. This is the ONLY function
    llm_reviewer.py needs to call — it doesn't need to know whether it's
    talking to Ollama, OpenAI, or Anthropic underneath.
    """
    config = load_config()

    if config["active_provider"] == "local":
        local = config["local"]
        if not local.get("selected_model"):
            raise ProviderError(
                "No local model selected yet. Open the dashboard's Model "
                "Settings panel and pick one of your detected Ollama models."
            )
        return OllamaProvider(
            model=local["selected_model"],
            base_url=local.get("ollama_base_url", DEFAULT_CONFIG["local"]["ollama_base_url"]),
        )

    # active_provider == "api"
    api = config["api"]
    if not api.get("api_key"):
        raise ProviderError(
            "No API key configured yet. Open the dashboard's Model Settings "
            "panel, choose a provider, and paste in an API key."
        )

    provider_name = api.get("provider", "openai")

    # Auto-select the best known-working model if the user left it blank.
    model = api.get("model") or PROVIDER_DEFAULT_MODELS.get(provider_name, "gpt-4o-mini")

    if provider_name == "anthropic":
        return AnthropicProvider(model=model, base_url=api["base_url"], api_key=api["api_key"])
    if provider_name == "gemini":
        return GeminiProvider(model=model, base_url=api["base_url"], api_key=api["api_key"])
    return OpenAICompatibleProvider(model=model, base_url=api["base_url"], api_key=api["api_key"])



def test_active_provider() -> Dict[str, Any]:
    """Sends a trivial prompt through whichever provider is configured, so
    the dashboard's 'Test Connection' button can give a real yes/no."""
    try:
        provider = get_active_provider()
        response = provider.generate("Reply with the single word: OK")
        return {"ok": True, "message": f"Provider responded: {response.strip()[:200]}"}
    except ProviderError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"Unexpected error: {e}"}


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
            ids = sorted(m["id"] for m in data.get("data", []) if m.get("id"))
            return {"ok": True, "models": ids, "error": None}

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

            # Anthropic schema: { "data": [ {"id": "claude-3-5-sonnet-...", ...} ] }
            ids = sorted(m["id"] for m in data.get("data", []) if m.get("id"))
            return {"ok": True, "models": ids, "error": None}

        # ------------------------------------------------------------------ #
        # Google Gemini                                                        #
        # ------------------------------------------------------------------ #
        if provider == "gemini":
            url = f"{base_url}/models?key={api_key}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # Gemini schema: { "models": [ {"name": "models/gemini-1.5-flash-latest", ...} ] }
            def _strip_prefix(n: str) -> str:
                return n.split("/")[-1] if "/" in n else n

            ids = sorted(
                _strip_prefix(m["name"])
                for m in data.get("models", [])
                if m.get("name") and "generateContent" in m.get("supportedGenerationMethods", [])
            )
            return {"ok": True, "models": ids, "error": None}

        return {"ok": False, "models": [], "error": f"Unknown provider: {provider}"}

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

