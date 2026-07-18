# Sentinel

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)
![MCP Protocol](https://img.shields.io/badge/protocol-MCP-blueviolet)
![Stage 2 CV Accuracy](https://img.shields.io/badge/Stage%202%20CV%20Accuracy-76.3%25-brightgreen)
![Dataset](https://img.shields.io/badge/dataset-828%20examples-orange)

**A three-stage guardrail agent for LLM-powered coding assistants.**

Sentinel sits between an LLM agent and its execution environment, reviewing every proposed action before it runs. It integrates with tools like Claude Code, Cursor, and CodeX via the Model Context Protocol (MCP), acting as an always-on safety layer that can block destructive commands, flag scope creep, and maintain a full audit trail of every decision.

---

## The Problem

Autonomous LLM coding agents can execute shell commands, modify files, push to remote repositories, and make network requests. This power comes with real risk: a single poorly-scoped prompt or a hallucinated action can cause data loss, expose credentials, or make irreversible changes to a production system.

Existing solutions are binary — either the agent runs everything without review, or a human must manually approve every step. Neither scales.

---

## The Solution

Sentinel implements a **multi-stage decision pipeline** that handles the full spectrum from obviously safe to dangerously risky actions, using the fastest and most appropriate tool at each stage:

**Stage 1 — Rules Engine**: Pattern matching on a configurable YAML ruleset. Handles unambiguous cases (recursive deletes, credential exposure, root-level writes) in microseconds with zero network dependency.

**Stage 2 — Trained Classifier**: A TF-IDF vectorizer and Logistic Regression classifier trained on a labeled dataset of agent actions. Runs in milliseconds, entirely offline, and produces an explainable risk score with confidence bounds.

**Stage 3 — LLM Reviewer**: For ambiguous actions that statistical models cannot resolve reliably, a large language model evaluates the action in the context of the user's stated task. This is the only stage that makes a network call, and it only activates when the previous stages are uncertain. Supports Ollama (local), OpenAI, Anthropic, and Google Gemini.

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

## Classifier Performance

| Metric | Value |
|---|---|
| Training Examples | 828 (hand-labeled + synthetically generated) |
| Class Balance | 58% safe / 42% risky |
| 5-Fold Cross-Validation Accuracy | 76.3% ± 3.0% |
| CV Macro-F1 Score | 75.9% ± 3.1% |
| Hold-out Test Accuracy | 75.2% |
| Risky Class Precision | 70% |
| Safe Class Precision | 79% |
| Confident Predictions (Stage 2 handles directly) | 40% of traffic |
| Stage 3 LLM escalation rate | 60% of traffic |

The classifier's confidence threshold is set at 80%. Predictions above this threshold are resolved by Stage 2 without invoking the Stage 3 LLM, reducing average latency and eliminating API cost for 40% of all reviewed actions.

**Top 5 features associated with risky actions:** `bash`, `delete`, `iptables`, `exec`, `secret`

**Top 5 features associated with safe actions:** `version`, `list`, `describe`, `test`, `check`

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
│   ├── train_classifier.py      Training script (scikit-learn)
│   └── generate_training_data.py  Synthetic training data generation
├── docs/
│   └── ARCHITECTURE.md          Internal design notes and rationale
├── start.bat                    Windows one-click launcher
├── Dockerfile                   Container image definition
└── requirements.txt
```

---

## Design Decisions

### Why three stages instead of one?

The design goal was to minimize latency and cost for the common case while preserving high-accuracy judgment for the ambiguous case. The vast majority of agent actions are either obviously safe (`git status`, `npm install`) or obviously risky (`rm -rf /`, `git push --force`). Routing both through an LLM would be slow and expensive. Routing both through a rules engine alone would miss the large middle ground.

The three-stage cascade solves this:

- **Stage 1** handles the clear-cut cases deterministically, in microseconds, with no model in the loop. A pattern match on a known-dangerous string cannot hallucinate. This is the last line of defense for catastrophic commands.
- **Stage 2** handles the statistical middle ground offline, in milliseconds, with an explainable coefficient-based model. We chose TF-IDF + Logistic Regression deliberately: the model trains in seconds on a CPU, produces inspectable coefficients, and is well-suited to short action text where risk is concentrated in specific keywords and n-grams. A neural network would add opacity without meaningfully improving the problem.
- **Stage 3** handles genuine ambiguity — cases where context (the user's stated task, the scope of the session) matters more than surface-level tokens. This is where an LLM's reasoning ability adds real value, and it is the only stage that pays the latency and cost of a model call.

### Why local-first for Stage 3?

We implemented Stage 3 with Ollama as the default to ensure that no action text leaves the user's machine unless they explicitly configure a cloud provider. This is important for codebases that may contain proprietary logic, internal hostnames, or sensitive file paths. The provider abstraction in `model_manager.py` makes it straightforward to switch to a cloud LLM without changing any Stage 3 logic.

### Why a confidence threshold?

Stage 2 does not pass every prediction to Stage 3 — only predictions below an 80% confidence threshold. This gates the expensive network call behind a statistical signal. Predictions above the threshold are resolved by Stage 2 directly, which accounts for approximately 40% of all traffic in practice. The remaining 60% escalates to Stage 3, where LLM reasoning provides the most marginal value.

---

## Setup

**Requirements**: Python 3.10 or later, pip, internet access (for API providers), or Ollama installed locally.

### Docker (recommended for production)

```bash
# Build the image
docker build -t sentinel .

# Run the container
docker run -p 8000:8000 -p 8080:8080 sentinel
```

The dashboard will be available at `http://localhost:8080` and the API at `http://localhost:8000`.

To persist the audit log and configuration across container restarts, mount a volume:

```bash
docker run -p 8000:8000 -p 8080:8080 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/sentinel.db:/app/sentinel.db \
  sentinel
```

### Windows (one-click)

Double-click `start.bat` in the project root. The script will:
1. Create a Python virtual environment if one does not exist
2. Install all dependencies from `requirements.txt`
3. Train the Stage 2 classifier if model artifacts are missing
4. Start the FastAPI server on port 8000
5. Start the dashboard on port 8080
6. Open the dashboard in your browser

### Manual setup

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

A complete annotated example config file is also included at `claude_mcp_config_example.json`.

### Rapid Connection Guide (Thread Modal)

To simplify client integration, the dashboard features a **Connect** utility in the top breadcrumb header:
1. Click the **Connect** button at the top right of the dashboard.
2. A modal overlay will display the local API server URL (`http://localhost:8000`) and a timeline of instructions.
3. Follow the sequence: copy the pre-built configuration JSON from the **Export & Share** tab, paste it into your editor/terminal settings, and start the coding assistant. Live review decisions will begin streaming to the **Overview feed** instantly.

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

## Stage 2 Classifier — Training

The classifier is trained on `data/training_examples.csv`, a hand-curated and synthetically augmented dataset of shell commands, SQL statements, git operations, and API calls labeled as `safe` or `risky`.

To generate additional synthetic training data:

```bash
python train/generate_training_data.py
```

To retrain after modifying or expanding the dataset:

```bash
python train/train_classifier.py
```

The script prints a full classification report and the top 15 features associated with risk, allowing the model's learned signals to be verified and understood without treating it as a black box.

---

## Security Notes

- API keys are stored only in `config/model_config.local.yaml`, which is gitignored by default.
- The audit log (`sentinel.db`) records action text and review decisions but never stores API keys.
- If `SENTINEL_API_KEY` is set as an environment variable before starting the server, all mutating endpoints (rules update, model config update, pause/resume) require that key via the `X-Sentinel-Key` header.
- The guardrail can be paused from the dashboard. In paused mode, all actions return `REVIEW`, requiring manual sign-off. This is intentionally conservative.

---

## License

MIT License. See `LICENSE` for details.
