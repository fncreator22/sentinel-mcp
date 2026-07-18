"""
mcp_server/server.py
======================
Exposes Sentinel as an MCP (Model Context Protocol) tool, so any MCP-aware
agent (e.g. an OpenAI Codex-style coding agent, Claude Code, etc.) can call
`review_action` before it executes a shell command / file write / git
operation, instead of Sentinel needing to be bolted onto each agent
framework separately.

WHAT THIS FILE DOES:
- Defines one MCP tool: `review_action(action_text, user_task)`
- Under the hood it just calls the same Orchestrator used by api/main.py
  and by tests — this file is a thin protocol adapter, not new logic.

WHY THIS IS A STRETCH GOAL / DONE LAST:
The core pipeline (Stages 1-3) and the API already fully demonstrate the
idea. This file exists to show Sentinel can slot into an MCP-based agent
workflow directly, which strengthens the "sits between a primary agent and
the real execution environment" pitch — but it's additive polish, not
required for the pipeline to work or be demoed via the API/dashboard.

RUN WITH:
    python mcp_server/server.py
(requires: pip install mcp)
"""

from mcp.server.fastmcp import FastMCP

from sentinel_core.orchestrator import Orchestrator

mcp = FastMCP("sentinel")
orchestrator = Orchestrator()


@mcp.tool()
def review_action(action_text: str, user_task: str = "") -> dict:
    """
    Review a proposed agent action (shell command, file write, git op, etc.)
    BEFORE executing it. Returns a verdict of ALLOW, BLOCK, or REVIEW along
    with the reasoning, so the calling agent can decide whether to proceed.

    Args:
        action_text: The exact command or action the agent wants to run.
        user_task: The user's original high-level task/goal, used by the
            Stage 3 LLM reviewer to judge scope creep on ambiguous actions.
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


if __name__ == "__main__":
    # Runs the MCP server over stdio, ready to be registered as a tool
    # server in any MCP-compatible agent client's config.
    mcp.run()
