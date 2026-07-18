"""
mcp_server/sse_server.py
=========================
Exposes the same Sentinel MCP tools (review_action, get_recent_decisions) over
HTTP using the SSE (Server-Sent Events) transport instead of stdio.

WHY THIS EXISTS:
    The original server.py uses stdio, which requires the client platform to
    spawn a local subprocess. Many web-based platforms and remote agents cannot
    do that. The SSE transport exposes an ordinary HTTP endpoint instead, so any
    HTTP-capable client can connect without spawning a local process.

TRANSPORT COMPARISON:
    stdio (server.py)       — local only, zero network exposure, client spawns
                              the process directly. Best for Claude Code, Cursor
                              IDE, and VS Code extensions running on this machine.

    SSE (this file)         — HTTP endpoint on port 8002, reachable from any
                              client that can make HTTP requests. Best for
                              web-based platforms, remote agents, or testing with
                              the MCP Inspector CLI.

INTERNET REQUIREMENT:
    The SSE transport itself needs NO internet connection.
    Internet is only required if you configured Stage 3 to use an API provider
    (OpenAI, Anthropic, Google Gemini) in the Model Settings dashboard tab.
    If Stage 3 is pointed at a local Ollama model the entire pipeline is offline.

RUN WITH:
    python mcp_server/sse_server.py
    python mcp_server/sse_server.py --port 8003   # custom port

CONNECT FROM A CLIENT:
    MCP Inspector:
        npx -y @modelcontextprotocol/inspector sse http://localhost:8002/sse

    Cursor IDE (Settings > MCP > Add Server):
        Type:    SSE
        URL:     http://localhost:8002/sse

    Any MCP-compatible HTTP client:
        SSE endpoint:     http://localhost:8002/sse
        Message endpoint: http://localhost:8002/messages/
"""

import argparse
import sys
import os

# ---------------------------------------------------------------------------
# Make sure the project root is on the Python path so sentinel_core imports
# work regardless of which directory this script is launched from.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mcp.server.fastmcp import FastMCP
from sentinel_core.orchestrator import Orchestrator

# ---------------------------------------------------------------------------
# Determine whether Stage 3 will need the internet so we can print an
# accurate startup notice. We read the saved model config without importing
# the entire API stack.
# ---------------------------------------------------------------------------
def _internet_required() -> bool:
    """Return True if the saved Stage 3 config uses a hosted API provider."""
    try:
        import yaml
        config_path = os.path.join(_ROOT, "config", "model_config.local.yaml")
        if not os.path.exists(config_path):
            config_path = os.path.join(_ROOT, "config", "model_config.yaml")
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("active_provider", "local") != "local"
    except Exception:
        # If we cannot read the config, assume local (safer assumption).
        return False


# ---------------------------------------------------------------------------
# Build the FastMCP server with the same two tools as server.py.
# FastMCP handles routing, serialisation, and the SSE protocol.
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "sentinel-sse",
    instructions=(
        "Sentinel is a three-stage guardrail agent for LLM-powered coding "
        "assistants. Call review_action before executing any shell command, "
        "file write, or git operation. The pipeline runs Stage 1 (rules), "
        "Stage 2 (classifier), and Stage 3 (LLM review) in order and returns "
        "a verdict of ALLOW, BLOCK, or REVIEW."
    ),
)

orchestrator = Orchestrator()


@mcp.tool()
def review_action(action_text: str, user_task: str = "") -> dict:
    """
    Review a proposed agent action (shell command, file write, git op, etc.)
    BEFORE executing it. Returns a verdict of ALLOW, BLOCK, or REVIEW along
    with the reasoning, so the calling agent can decide whether to proceed.

    Args:
        action_text: The exact command or action the agent wants to run.
        user_task: The user's original high-level task/goal, used by Stage 3
            to judge whether the action is within scope.
    """
    decision = orchestrator.review(action_text, user_task)
    return {
        "verdict": decision.verdict,
        "decided_by_stage": decision.decided_by_stage,
        "reason": decision.reason,
        "log_id": decision.log_id,
    }


@mcp.tool()
def get_recent_decisions(limit: int = 20) -> list:
    """Return the most recent Sentinel review decisions from the audit log."""
    return orchestrator.audit_log.get_recent_decisions(limit=limit)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sentinel MCP server (SSE transport)")
    parser.add_argument(
        "--port",
        type=int,
        default=8002,
        help="Port to listen on (default: 8002)",
    )
    args = parser.parse_args()

    needs_internet = _internet_required()

    print()
    print("=" * 60)
    print("  Sentinel MCP Server — SSE Transport")
    print("=" * 60)
    print(f"  SSE endpoint  : http://localhost:{args.port}/sse")
    print(f"  Messages      : http://localhost:{args.port}/messages/")
    print()
    if needs_internet:
        print("  [!] Stage 3 is configured to use a hosted API provider.")
        print("      An active internet connection is required for")
        print("      ambiguous actions that reach Stage 3.")
    else:
        print("  [OK] Stage 3 is configured to use a local Ollama model.")
        print("       The entire pipeline runs offline — no internet needed.")
    print()
    print("  Test with MCP Inspector:")
    print(f"    npx -y @modelcontextprotocol/inspector sse http://localhost:{args.port}/sse")
    print()
    print("  Connect from Cursor IDE:")
    print("    Settings > MCP > Add Server > Type: SSE")
    print(f"    URL: http://localhost:{args.port}/sse")
    print()
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    print()

    # Run using the SSE transport. FastMCP starts an internal Starlette/uvicorn
    # server on the configured port.
    mcp.run(transport="sse")
