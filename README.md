# Sentinel

**A three-stage AI guardrail agent for LLM-powered coding assistants.**

Sentinel sits between an AI agent and its execution environment, reviewing every proposed action before it runs. It is designed to be integrated into tools like Claude Code, Cursor, and CodeX via the Model Context Protocol (MCP), where it acts as an always-on safety layer that can block destructive commands, flag scope creep, and provide a full audit trail of every decision.

---

## The Problem

Autonomous AI coding agents are capable of executing shell commands, modifying files, pushing to remote repositories, and making network requests. This power comes with real risk: a single poorly-scoped prompt or a hallucinated action can cause data loss, expose credentials, or make irreversible changes to a production system.

Existing solutions are binary — either the agent runs everything without review, or a human must manually approve every step. Neither scales.

## The Solution

Sentinel implements a **multi-stage decision pipeline** that handles the full spectrum from obviously safe to dangerously risky actions, using the fastest and most appropriate tool at each stage:

**Stage 1 — Rules Engine**: Pattern matching on a configurable YAML ruleset. Handles the unambiguous cases (recursive deletes, credential exposure, root-level writes) in microseconds with zero network dependency.

**Stage 2 — Trained Classifier**: A TF-IDF vectorizer and Logistic Regression classifier trained on a labeled dataset of agent actions. Runs in milliseconds, entirely offline, and produces an explainable risk score with confidence bounds.

**Stage 3 — LLM Reviewer**: For the ambiguous actions that statistical models cannot resolve reliably, a large language model evaluates the action in the context of the user's stated task. This is the only stage that uses a network call, and it only activates when the previous stages are uncertain. Supports Ollama (local), OpenAI, Anthropic, and Google Gemini.

---

## Architecture

```
MCP Client (Claude Code / Cursor / CodeX)
        |
        | stdio (MCP protocol)
        v
  mcp_server/server.py         <- MCP transport layer
        |
        | HTTP POST /review
        v
  api/main.py (FastAPI)        <- REST API, audit log, config management
        |
        v
  sentinel_core/orchestrator.py
        |
        +-- Stage 1: sentinel_core/rules_engine.py   (config/rules.yaml)
        +-- Stage 2: sentinel_core/classifier.py     (model_artifacts/model.pkl)
        +-- Stage 3: sentinel_core/llm_reviewer.py   (sentinel_core/model_manager.py)
        |
        v
  sentinel.db (SQLite)         <- append-only audit log
```

---

## Project Structure

```
sentinel/
├── api/
│   └── main.py                  FastAPI application, all HTTP endpoints
├── sentinel_core/
│   ├── orchestrator.py          Three-stage pipeline coordinator
│   ├── rules_engine.py          Stage 1: YAML rule matching
│   ├── classifier.py            Stage 2: sklearn inference
│   ├── llm_reviewer.py          Stage 3: LLM reasoning
│   ├── model_manager.py         Provider abstraction (Ollama / OpenAI / Anthropic / Gemini)
│   ├── audit_log.py             SQLite decision logger
│   └── model_artifacts/         model.pkl + vectorizer.pkl (gitignored)
├── mcp_server/
│   └── server.py                MCP stdio server (tool: review_action)
├── dashboard/
│   ├── index.html               Single-page control panel
│   ├── app.js                   Dashboard logic
│   └── style.css                Dashboard styles
├── config/
│   ├── rules.yaml               Stage 1 allow/block patterns
│   ├── model_config.yaml        Active provider and model selection
│   └── model_config.local.yaml  API keys (gitignored, never committed)
├── data/
│   └── training_examples.csv    Labeled dataset for Stage 2 training
├── train/
│   └── train_classifier.py      Training script (scikit-learn)
├── start.bat                    Windows one-click launcher
└── requirements.txt
```

---

## Setup

**Requirements**: Python 3.10 or later, pip, internet access (for API providers), or Ollama installed locally.

### Windows (recommended)

Double-click `start.bat` in the project root. The script will:
1. Create a Python virtual environment if one does not exist
2. Install all dependencies from `requirements.txt`
3. Train the Stage 2 classifier if model artifacts are missing
4. Start the FastAPI server on port 8000
5. Start the dashboard on port 8080
6. Open the dashboard in your browser

### Manual startup

```bash
# Create and activate the virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# (First time only) Train the Stage 2 classifier
python train/train_classifier.py

# Start the API server
python -m uvicorn api.main:app --port 8000 --reload

# In a separate terminal, serve the dashboard
cd dashboard
python -m http.server 8080

# Open in browser
# http://localhost:8080
```

---

## Configuration

### Stage 3 Model Provider

Open the dashboard at `http://localhost:8080` and navigate to **Model Settings**.

**Local (Ollama)**: Select any model detected from your local Ollama installation. No internet required. Pull a model with `ollama pull <model-name>` before selecting it.

**API providers**: Select Google Gemini, OpenAI, or Anthropic, enter your API key, and save. The key is stored in `config/model_config.local.yaml` on disk, never written to the audit log, and never returned in full over the API (only the last 4 characters are shown in the dashboard).

### Stage 1 Rules

Navigate to the **Rules** tab in the dashboard to add, edit, or remove pattern-matching rules. Rules support exact substring matching and regular expressions. Changes take effect immediately without restarting the server.

---

## MCP Integration

### Claude Code

Add the following block to your Claude Code settings file (`~/.claude/claude_code_config.json` or the project-level `.claude/settings.json`):

```json
{
  "mcpServers": {
    "sentinel": {
      "command": "python",
      "args": ["mcp_server/server.py"],
      "cwd": "/absolute/path/to/sentinel"
    }
  }
}
```

Replace `/absolute/path/to/sentinel` with the actual path to this repository on your machine.

### Claude Desktop

Add the same block to `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS).

### Getting the config automatically

In the Sentinel dashboard, go to **Export and Share** and click **Copy Config to Clipboard**. The correct JSON block with your machine's path pre-filled will be copied, ready to paste directly into your MCP client configuration.

### Available MCP tools

| Tool | Description |
|---|---|
| `review_action` | Review a proposed agent action. Returns `ALLOW`, `BLOCK`, or `REVIEW`. |
| `get_recent_decisions` | Return recent entries from the audit log. |

---

## API Reference

The FastAPI backend exposes the following endpoints. Full interactive documentation is available at `http://localhost:8000/docs` when the server is running.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check, returns paused state and project path |
| GET | `/status` | Lightweight paused/auth status |
| POST | `/review` | Submit an action for review |
| POST | `/pause` | Pause the guardrail pipeline |
| POST | `/resume` | Resume the guardrail pipeline |
| GET | `/log` | Retrieve recent audit log entries |
| GET | `/rules` | Get current Stage 1 ruleset |
| POST | `/rules` | Update Stage 1 ruleset |
| GET | `/models/local` | List locally available Ollama models |
| GET | `/models/config` | Get current model provider configuration |
| POST | `/models/config` | Update model provider configuration |
| POST | `/models/test` | Test the active model provider connection |
| GET | `/mcp/servers` | List registered MCP servers |
| POST | `/mcp/servers` | Register a new MCP server |
| DELETE | `/mcp/servers/{name}` | Remove a registered MCP server |

### Example: Reviewing an action

```bash
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{"action_text": "rm -rf /tmp/build", "user_task": "Clean up build artifacts"}'
```

Response:

```json
{
  "action_text": "rm -rf /tmp/build",
  "verdict": "ALLOW",
  "decided_by_stage": "classifier",
  "reason": "Classifier predicted 'safe' with 89% confidence.",
  "log_id": 42
}
```

---

## Stage 2 Classifier

The classifier is trained on `data/training_examples.csv`, a hand-curated dataset of shell commands, SQL statements, git operations, and API calls labeled as `safe` or `risky`.

To retrain after modifying the dataset:

```bash
python train/train_classifier.py
```

The script prints a classification report and the top 15 features associated with risk, which can be used to explain the model's behavior to a technical audience.

---

## Security Notes

- API keys are stored only in `config/model_config.local.yaml`, which is gitignored by default.
- The audit log (`sentinel.db`) records action text and review decisions but never stores API keys.
- If `SENTINEL_API_KEY` is set as an environment variable before starting the server, all mutating endpoints (rules update, model config update, pause/resume) require that key via the `X-Sentinel-Key` header.
- The guardrail can be paused from the dashboard. In paused mode, all actions return `REVIEW`, requiring manual sign-off. This is intentionally conservative.

---

## Hackathon Context

Sentinel was built to demonstrate that practical AI safety tooling does not have to be a research project. The core contribution is the three-stage cascade design: deterministic rules handle the clear cases instantly, a trained classifier handles the statistical cases offline, and a large language model handles only the genuinely ambiguous edge cases. This keeps latency low, cost near zero for most deployments, and the system explainable at every decision point.

The trained model artifacts are not bundled in this repository. Run `python train/train_classifier.py` to generate them. The training script prints a full classification report and the top weighted features so judges and reviewers can verify that the model learned meaningful risk signals.

---

## License

MIT License. See `LICENSE` for details.
