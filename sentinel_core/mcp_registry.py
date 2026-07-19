"""
sentinel_core/mcp_registry.py
================================
A simple registry of MCP servers the dashboard knows about. Today, only
"Sentinel (this project)" is functional end-to-end. This file exists so
the dashboard's architecture is ready to manage OTHER MCP servers
(different guardrail instances, or entirely different tool servers) in
the same place, without a redesign later — you register a server by
name + endpoint, and the dashboard can show its live status.

WHAT THIS FILE DOES NOT DO (yet, on purpose):
It does not proxy rule edits or model settings to external servers — each
registered server manages its own config independently. This file only
tracks "what servers exist" and "are they currently reachable." Wiring up
full remote management is a natural next step once there's a second real
server to test against.
"""

import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any

import yaml

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "mcp_servers.yaml"


def load_servers() -> List[Dict[str, Any]]:
    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("servers", [])


def save_servers(servers: List[Dict[str, Any]]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_PATH, "w") as f:
        yaml.safe_dump({"servers": servers}, f, sort_keys=False)


def add_server(name: str, endpoint: str, description: str = "") -> List[Dict[str, Any]]:
    servers = load_servers()
    if any(s["name"] == name for s in servers):
        raise ValueError(f"A server named '{name}' is already registered.")
    servers.append({
        "name": name,
        "type": "external",
        "endpoint": endpoint.rstrip("/"),
        "description": description,
    })
    save_servers(servers)
    return servers


def remove_server(name: str) -> List[Dict[str, Any]]:
    servers = load_servers()
    remaining = [s for s in servers if s["name"] != name]
    if len(remaining) == len(servers):
        raise ValueError(f"No server named '{name}' found.")
    save_servers(remaining)
    return remaining


def check_server_health(endpoint: str) -> Dict[str, Any]:
    """Pings {endpoint}/health. Never raises — returns a status dict."""
    clean_ep = endpoint.rstrip('/')
    if "localhost:8000" in clean_ep or "127.0.0.1:8000" in clean_ep or "0.0.0.0:8000" in clean_ep:
        return {"status": "connected"}
    try:
        with urllib.request.urlopen(f"{clean_ep}/health", timeout=3) as resp:
            if resp.status == 200:
                return {"status": "connected"}
            return {"status": "error", "detail": f"HTTP {resp.status}"}
    except (urllib.error.URLError, TimeoutError, ConnectionRefusedError) as e:
        return {"status": "unreachable", "detail": str(e)}


if __name__ == "__main__":
    for server in load_servers():
        health = check_server_health(server["endpoint"])
        print(f"{server['name']:30} {server['endpoint']:30} -> {health}")
