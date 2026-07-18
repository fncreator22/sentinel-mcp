# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
- Ongoing updates to the dashboard UI.
- General repository cleanup and stabilization.

## [1.0.0] - 2026-07-19
### Added
- **Three-Stage Architecture**: Configurable pipeline handling deterministic rules, statistical classification, and LLM semantic review.
- **MCP Integration**: Fully functional Model Context Protocol server exposing `review_action` and `get_recent_decisions` tools.
- **Multiple Transports**: Support for standard Stdio (for local execution like Cursor and Claude Code) and SSE endpoints (for web-based platforms).
- **Dashboard UI**: Local 'CyberDefend' themed web application serving live stats, decision feeds, rule configurations, and model settings.
- **Local-First Design**: By default, leverages local Ollama models ensuring zero data leakage for sensitive enterprise environments.
- **Full Open-Source Standards**: Added LICENSE, CONTRIBUTING guidelines, CODE_OF_CONDUCT, SECURITY policy, and Issue/PR templates to elevate the project to an industrial standard.
