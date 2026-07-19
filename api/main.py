"""
api/main.py
=============
FastAPI app exposing Sentinel over HTTP.

ENDPOINTS
  Core review pipeline:
    POST /review                 -> run an action through the 3-stage pipeline
    GET  /log                    -> recent audit log entries

  Rules (Stage 1):
    GET  /rules                  -> current config/rules.yaml
    POST /rules                  -> overwrite rules, hot-reload immediately

  Model settings (Stage 3 — local model or API):
    GET  /models/local           -> auto-detected Ollama models on this machine
    GET  /models/config          -> current provider config (secrets masked)
    POST /models/config          -> save provider config (local or api)
    POST /models/test            -> send a trivial prompt to confirm it works
    GET  /models/available       -> live model list fetched from the configured API key

  MCP server registry:
    GET    /mcp/servers          -> list registered MCP servers + live health
    POST   /mcp/servers          -> register a new server {name, endpoint, description}
    DELETE /mcp/servers/{name}   -> remove a registered server

  Sharing:
    GET  /export                 -> a shareable JSON bundle (rules + model
                                     config WITHOUT secrets + server list),
                                     so a setup can be handed to a teammate

  GET  /health                   -> liveness check (no auth required, ever —
                                     used by the MCP registry's health pings)

SECURITY (read this before hosting anywhere public)
This API has NO authentication by default, which is fine for a hackathon
demo on localhost. If you deploy it anywhere reachable by others, set the
SENTINEL_API_KEY environment variable before starting the server:

    export SENTINEL_API_KEY="some-long-random-string"
    uvicorn api.main:app --port 8000

Once set, every mutating endpoint (anything that isn't a plain GET, plus
/health which is always open) requires a header:

    X-Sentinel-Key: some-long-random-string

The dashboard will prompt for this key and remember it for the session if
the API reports auth is required. API keys for model providers are never
returned by any endpoint in full — only a masked last-4 view.

RUN WITH:
    uvicorn api.main:app --reload --port 8000
(run this from the sentinel/ project root so relative imports resolve)
"""

import os
import sys
from typing import Optional, List

import yaml
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sentinel_core.orchestrator import Orchestrator
from sentinel_core.rules_engine import DEFAULT_RULES_PATH
from sentinel_core import model_manager
from sentinel_core import mcp_registry

app = FastAPI(title="Sentinel API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Orchestrator()

SENTINEL_API_KEY = os.environ.get("SENTINEL_API_KEY")  # None => auth disabled

if not SENTINEL_API_KEY:
    print(
        "\n[Sentinel] WARNING: running with no API key set (SENTINEL_API_KEY). "
        "Anyone who can reach this port can edit your rules and model config. "
        "Fine for a local hackathon demo — set SENTINEL_API_KEY before hosting "
        "this anywhere reachable by others.\n"
    )


def require_auth(x_sentinel_key: Optional[str] = Header(default=None)):
    """
    Called at the top of every mutating endpoint. No-op if SENTINEL_API_KEY
    isn't set (local demo mode). Otherwise requires a matching header.
    """
    if SENTINEL_API_KEY and x_sentinel_key != SENTINEL_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Sentinel-Key header.")


# ---- Schemas -----------------------------------------------------------------

class ReviewRequest(BaseModel):
    action_text: str
    user_task: Optional[str] = ""


class ReviewResponse(BaseModel):
    action_text: str
    verdict: str
    decided_by_stage: str
    reason: str
    log_id: Optional[int] = None


class RulesUpdateRequest(BaseModel):
    block_patterns: List[dict]
    allow_patterns: List[dict]
    classifier_confidence_threshold: float = 0.75


class LocalModelConfig(BaseModel):
    ollama_base_url: str = "http://localhost:11434"
    selected_model: Optional[str] = None


class ApiModelConfig(BaseModel):
    provider: str = "openai"          # "openai" | "anthropic" | "custom"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key: Optional[str] = None     # write-only; omit/empty = keep existing key


class ModelConfigUpdateRequest(BaseModel):
    active_provider: str              # "local" | "api"
    local: LocalModelConfig
    api: ApiModelConfig


class McpServerCreateRequest(BaseModel):
    name: str
    endpoint: str
    description: Optional[str] = ""


# ---- Core pipeline -------------------------------------------------------------

@app.get("/health")
def health():
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root_normalized = project_root.replace("\\", "/")
    return {
    "status": "ok",
    "auth_required": bool(SENTINEL_API_KEY),
    "project_root": project_root_normalized,
    "python_executable": sys.executable,
    "paused": orchestrator.paused,
}


@app.get("/status")
def get_status():
    """Lightweight status endpoint — paused state + auth mode."""
    return {
        "paused": orchestrator.paused,
        "auth_required": bool(SENTINEL_API_KEY),
    }


@app.post("/pause")
def pause_sentinel(x_sentinel_key: Optional[str] = Header(default=None)):
    """Pause the guardrail pipeline. All /review calls will return REVIEW
    (requiring manual sign-off) until /resume is called."""
    require_auth(x_sentinel_key)
    orchestrator.paused = True
    return {"ok": True, "paused": True, "message": "Sentinel guardrail paused. All actions require manual review."}


@app.post("/resume")
def resume_sentinel(x_sentinel_key: Optional[str] = Header(default=None)):
    """Resume the guardrail pipeline after a /pause."""
    require_auth(x_sentinel_key)
    orchestrator.paused = False
    return {"ok": True, "paused": False, "message": "Sentinel guardrail resumed. Pipeline is active."}


@app.post("/review", response_model=ReviewResponse)
def review_action(req: ReviewRequest):
    decision = orchestrator.review(req.action_text, req.user_task)
    return ReviewResponse(
        action_text=decision.action_text,
        verdict=decision.verdict,
        decided_by_stage=decision.decided_by_stage,
        reason=decision.reason,
        log_id=decision.log_id,
    )


@app.get("/log")
def get_log(limit: int = 50):
    return orchestrator.audit_log.get_recent_decisions(limit=limit)


# ---- Rules ---------------------------------------------------------------------

@app.get("/rules")
def get_rules():
    with open(DEFAULT_RULES_PATH, "r") as f:
        data = yaml.safe_load(f) or {}
    return data


@app.post("/rules")
def update_rules(req: RulesUpdateRequest, x_sentinel_key: Optional[str] = Header(default=None)):
    require_auth(x_sentinel_key)
    new_data = {
        "block_patterns": req.block_patterns,
        "allow_patterns": req.allow_patterns,
        "classifier_confidence_threshold": req.classifier_confidence_threshold,
    }
    try:
        with open(DEFAULT_RULES_PATH, "w") as f:
            yaml.safe_dump(new_data, f, sort_keys=False)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not write rules.yaml: {e}")

    orchestrator.rules_engine.reload()
    return {"status": "ok", "message": "Rules updated and reloaded."}


# ---- Model settings --------------------------------------------------------------

@app.get("/models/local")
def get_local_models():
    """
    Auto-detects Ollama models installed on this machine. Empty list means
    either Ollama isn't installed/running, or nothing has been pulled yet
    — the dashboard should show install instructions in that case.
    """
    config = model_manager.load_config()
    base_url = config["local"]["ollama_base_url"]
    models = model_manager.detect_local_models(base_url)
    return {
        "ollama_reachable": model_manager._ping_ollama(base_url),
        "models": [{"name": m.name, "size_bytes": m.size_bytes} for m in models],
    }


@app.get("/models/config")
def get_model_config():
    """Returns current provider config with any API key masked."""
    return model_manager.masked_config()


@app.post("/models/config")
def update_model_config(req: ModelConfigUpdateRequest, x_sentinel_key: Optional[str] = Header(default=None)):
    require_auth(x_sentinel_key)

    new_config = {
        "active_provider": req.active_provider,
        "local": req.local.dict(),
        "api": req.api.dict(),
    }

    # Empty string from the dashboard means "don't change the stored key"
    if new_config["api"].get("api_key") == "":
        new_config["api"].pop("api_key")
        model_manager.save_config(new_config)
        return {"status": "ok", "message": "Model settings saved (existing API key kept)."}

    model_manager.save_config(new_config)
    return {"status": "ok", "message": "Model settings saved."}


@app.post("/models/test")
def test_model_config(x_sentinel_key: Optional[str] = Header(default=None)):
    require_auth(x_sentinel_key)
    return model_manager.test_active_provider()





# ---- MCP server registry ----------------------------------------------------------

@app.get("/mcp/servers")
def list_mcp_servers():
    servers = mcp_registry.load_servers()
    for s in servers:
        s["health"] = mcp_registry.check_server_health(s["endpoint"])
    return {"servers": servers}


@app.post("/mcp/servers")
def add_mcp_server(req: McpServerCreateRequest, x_sentinel_key: Optional[str] = Header(default=None)):
    require_auth(x_sentinel_key)
    try:
        servers = mcp_registry.add_server(req.name, req.endpoint, req.description or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "servers": servers}


@app.delete("/mcp/servers/{name}")
def remove_mcp_server(name: str, x_sentinel_key: Optional[str] = Header(default=None)):
    require_auth(x_sentinel_key)
    try:
        servers = mcp_registry.remove_server(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok", "servers": servers}


# ---- Sharing --------------------------------------------------------------------

@app.get("/export")
def export_config():
    """
    A shareable, secret-free bundle of this Sentinel instance's setup:
    rules, model provider choice (no API key), and registered MCP servers.
    Meant to be downloaded from the dashboard and handed to a teammate,
    who can import the rules/model sections into their own instance.
    """
    with open(DEFAULT_RULES_PATH, "r") as f:
        rules = yaml.safe_load(f) or {}

    return {
        "sentinel_export_version": 1,
        "rules": rules,
        "model_config": model_manager.masked_config(),
        "mcp_servers": mcp_registry.load_servers(),
    }
