# SENTINEL PROJECT — CONTINUATION DOCUMENT (v2)
### If your chat session ends, paste this whole file into a new Claude conversation and say:
### "Continue building this project from where this doc leaves off."
### This replaces the v1 continuation doc — v2 reflects the dashboard/model/MCP-registry expansion.

---

## 1. WHAT THIS PROJECT IS

**Sentinel** — a guardrail agent for a hackathon (OpenAI Codex / agentic-coding themed, no domain restriction).

**Core idea:** Most hackathon teams build "an agent that does a task." Sentinel is the
opposite — an agent that *supervises another agent's actions* before they execute, catching
destructive commands, scope creep, and unsafe operations. It sits between a primary coding
agent and the real execution environment.

**Key differentiators (say these in the pitch):**
1. Self-hosted by default — a local model (Ollama) can power the reasoning stage with zero
   external dependency, OR the user can plug in any API-based model instead. Both are first-class,
   switchable live from the dashboard, and it's the user's explicit choice which one is active.
2. A small classifier model is **trained from scratch by us** (not just prompting a third-party
   LLM) for the risk-judgment step. Public proof lives on Hugging Face.
3. Custom-built orchestration loop — no dependency on LangChain/OpenAI Agents SDK/any agent
   framework.
4. Dashboard lets a non-technical user edit safety rules, choose/configure the reasoning model
   (local or API), and manage connected MCP servers — all live, no code, no restarts.
5. Built to grow: the dashboard's MCP server registry means it isn't hard-wired to just this one
   guardrail instance — it's designed as a management surface other MCP servers can be registered
   into later.

---

## 2. ARCHITECTURE (3-stage pipeline)

```
Primary Agent wants to run an action (shell cmd / file write / git push / etc.)
        │
        ▼
STAGE 1 — Rule Engine (deterministic, instant, no model)
  - reads config/rules.yaml
  - hard blocklist patterns → BLOCK immediately
  - clearly safe patterns → ALLOW immediately
  - anything else → pass to Stage 2
        │
        ▼
STAGE 2 — Trained Classifier (small model, trained by us from scratch)
  - TF-IDF + Logistic Regression (scikit-learn) — simple, explainable, fast to train
  - trained on our own labeled dataset of safe vs risky actions
  - outputs APPROVE / REVIEW / BLOCK + confidence score
  - if confidence is low → pass to Stage 3
        │
        ▼
STAGE 3 — Model Reviewer (only for ambiguous cases; local OR API, user's choice)
  - reasons about whether action is in scope of the user's original task
  - outputs verdict + plain-English explanation
  - LOCAL mode: talks to Ollama on this machine, auto-detected — nothing leaves the machine
  - API mode: talks to OpenAI / Anthropic / any OpenAI-compatible endpoint via a stored key
  - which mode is active is entirely controlled from the dashboard's "Model Settings" tab
        │
        ▼
Executor — only runs approved actions
        │
        ▼
Audit Log (SQLite) — every decision + reasoning stored
        │
        ▼
Dashboard — tabbed UI: live feed, rule editor, model settings, MCP server registry, config export
```

**Why 3 stages instead of just "ask an LLM every time":** Rules are instant and 100% reliable for
known-dangerous patterns — never trust a model alone for hard safety guarantees. The trained
classifier is fast and explainable. The model (local or API) is reserved only for genuinely
ambiguous cases, which keeps the system fast and keeps the "we trained our own model" claim
honest and central, not decorative.

---

## 3. WHY TWO REPOS, AND WHAT GOES WHERE

**GitHub repo (`sentinel`)** — the whole project. Full folder structure, all code, config,
tests, docs. GitHub stores everything regardless of structure — this is your primary,
complete repo.

**Hugging Face repo (`sentinel-risk-classifier`)** — ONLY the trained classifier model + its
README (model card). Hugging Face is read primarily through its README, so that repo should be
minimal and clean: model file, a short inference script, and a README explaining what the model
does, how it was trained, and how to load it. This repo exists as a public, verifiable proof
that "we trained our own model" — judges can open it directly.

---

## 4. FOLDER STRUCTURE (current, as actually built)

### GitHub repo: `sentinel/`

```
sentinel/
├── README.md                        # project overview, setup instructions
├── requirements.txt                  # python dependencies
├── .gitignore                        # excludes sentinel.db, *.pkl, model_config.local.yaml
├── config/
│   ├── rules.yaml                    # Stage 1 block/allow patterns (dashboard-editable)
│   ├── model_config.yaml             # Stage 3 provider choice, NO secrets (committed)
│   ├── model_config.local.yaml       # Stage 3 API key ONLY — gitignored, created on first save
│   └── mcp_servers.yaml              # registry of known MCP servers (self + any registered)
├── sentinel_core/
│   ├── __init__.py
│   ├── rules_engine.py               # Stage 1
│   ├── classifier.py                 # Stage 2 — loads trained model, predicts
│   ├── llm_reviewer.py               # Stage 3 — builds prompt, parses verdict, provider-agnostic
│   ├── model_manager.py              # Stage 3 infra — detects local models, manages
│   │                                  #   local/API config split, provider classes
│   │                                  #   (OllamaProvider, OpenAICompatibleProvider,
│   │                                  #   AnthropicProvider), get_active_provider()
│   ├── mcp_registry.py               # MCP server registry CRUD + health checks
│   ├── orchestrator.py               # glues stages 1-3 together, the core loop
│   ├── audit_log.py                  # SQLite read/write
│   └── model_artifacts/              # model.pkl + vectorizer.pkl (generated by train/, gitignored)
├── mcp_server/
│   └── server.py                     # exposes guarded tools via MCP protocol (review_action, etc.)
├── api/
│   └── main.py                       # FastAPI: /review /rules /log /models/* /mcp/servers* /export
│                                      #   optional auth via SENTINEL_API_KEY env var
├── dashboard/
│   ├── index.html                    # tabbed UI: Overview / Rules / Model Settings / MCP Servers / Export
│   ├── app.js                        # wires all tabs to the API, handles auth header
│   └── style.css                     # SOC-monitor visual style (dark, signal-colored feed)
├── data/
│   └── training_examples.csv         # labeled data: action text, label (safe/risky) — 185 rows
├── train/
│   └── train_classifier.py           # trains the model, saves it, also syncs to HF repo folder
├── tests/
│   └── test_rules_engine.py          # 9 passing tests for Stage 1
└── docs/
    ├── ARCHITECTURE.md                # deeper design rationale + failure-mode table
    └── PROJECT_CONTINUATION.md        # this file
```

### Hugging Face repo: `sentinel-risk-classifier/`

```
sentinel-risk-classifier/
├── README.md            # model card: what it is, how trained, how to use, real accuracy numbers
├── model.pkl             # trained sklearn LogisticRegression
├── vectorizer.pkl         # paired TfidfVectorizer
└── inference.py           # standalone load + predict example, no dependency on main repo
```

---

## 5. BUILD ORDER / STATUS CHECKLIST

Everything below is DONE and has been actually run/tested, not just written:

- [x] `config/rules.yaml` — 17 block patterns, 10 allow patterns
- [x] `sentinel_core/rules_engine.py` — Stage 1, 9/9 pytest passing
- [x] `data/training_examples.csv` — 185 labeled examples (105 safe / 80 risky)
- [x] `train/train_classifier.py` — trained for real, 81% held-out test accuracy, auto-syncs to HF folder
- [x] `sentinel_core/classifier.py` — Stage 2, loads real trained model, tested
- [x] `sentinel_core/llm_reviewer.py` — Stage 3, provider-agnostic (rewritten to use model_manager)
- [x] `sentinel_core/model_manager.py` — NEW: local model auto-detection (Ollama /api/tags),
      local/API config split with secrets isolation, OllamaProvider/OpenAICompatibleProvider/
      AnthropicProvider classes, get_active_provider(), test_active_provider()
- [x] `sentinel_core/mcp_registry.py` — NEW: MCP server registry CRUD + health pings
- [x] `sentinel_core/orchestrator.py` — the 3-stage pipeline function, verified end-to-end
- [x] `sentinel_core/audit_log.py` — SQLite logging, tested
- [x] `api/main.py` — FastAPI endpoints, ALL smoke-tested live with curl:
      /review /log /rules (GET+POST) /models/local /models/config (GET+POST) /models/test
      /mcp/servers (GET+POST+DELETE) /export /health. Optional auth via SENTINEL_API_KEY
      env var — tested BOTH with and without a key set (401 without, 200 with correct key).
- [x] `mcp_server/server.py` — MCP wrapper, import/tool-registration verified
- [x] `dashboard/` — full tabbed UI (Overview / Rules / Model Settings / MCP Servers / Export),
      HTML/CSS/JS, served and confirmed reachable alongside the API
- [x] `tests/test_rules_engine.py` — 9/9 passing
- [ ] Push to GitHub repo — **not yet done, you need to do this**
- [ ] Push trained model to Hugging Face repo with README — **not yet done, you need to do this**
- [ ] Pull an actual Ollama model and test Stage 3 with a real local model (only tested the
      graceful-fallback path so far, since no Ollama server was running in the build environment)
- [ ] Rehearse the 3-minute demo script (see README.md, bottom section)

---

## 6. WHAT CHANGED SINCE v1 OF THIS DOC (the dashboard/model/MCP expansion)

The original build (checklist v1) was a working 3-stage pipeline with a basic dashboard that
only had a live feed + rule editor, and Stage 3 was hardcoded to call Ollama directly. This was
expanded into what's described above, specifically to satisfy:

1. **"Everyone can choose local model or API model, and it's auto-detected"** →
   `model_manager.py` + the dashboard's Model Settings tab. Local models are found by hitting
   Ollama's own `/api/tags` endpoint — no manual typing needed if you've pulled a model. API mode
   supports OpenAI, Anthropic, or any custom OpenAI-compatible endpoint, with base URL and model
   name prefilled per provider and editable.

2. **"Everything maintained by the dashboard, everything explained"** → the dashboard was
   rebuilt as a 5-tab interface (Overview, Rules, Model Settings, MCP Servers, Export) with
   plain-English `<p class="hint">` explanations under every control, aimed at a non-technical
   user. No tab requires reading code to understand what it does.

3. **"Error handling and security"** → API keys are split into a gitignored
   `model_config.local.yaml` file, NEVER returned in full by any endpoint (only last-4-masked),
   NEVER logged. An optional `SENTINEL_API_KEY` env var gates all mutating endpoints behind a
   header check — off by default for local demo friction-lessness, but real and tested. Every
   provider call (Ollama or API) has explicit try/except with a safe REVIEW fallback rather than
   crashing or silently approving.

4. **"MCP server can be shared / dashboard ready for other MCP servers in future"** →
   `mcp_registry.py` + `config/mcp_servers.yaml` + the MCP Servers dashboard tab. This project
   (`Sentinel (this project)`) is always self-registered and fully functional. Additional servers
   can be registered by name + endpoint and their live `/health` status is shown — this is
   intentionally scoped as a registry + status board for now, NOT full remote rule/model
   management of other servers (that's flagged as a roadmap item, honestly, both in code comments
   and in the dashboard's own copy).

5. **"Share the whole MCP server through their project"** → `GET /export` on the API + a
   "Export & Share" dashboard tab. Downloads a JSON bundle of rules + model config (masked, no
   key) + registered servers, meant to be handed to a teammate or checked into a wiki.

---

## 7. KEY DESIGN DECISIONS TO REMEMBER WHEN RESUMING

- **Secrets split:** `config/model_config.yaml` is committed and NEVER has a real key.
  `config/model_config.local.yaml` is gitignored and holds ONLY `{api_key: ...}`. This split
  happens inside `model_manager.save_config()` — any future code touching model config must
  preserve this split, don't ever write the key into the committed file.
- **Provider abstraction boundary:** `llm_reviewer.py` only knows about prompts and JSON-verdict
  parsing. `model_manager.py` only knows about "how do I get raw text out of whichever model is
  configured." Don't blur this line — it's what makes swapping providers not require touching
  Stage 3's actual review logic.
- **Fail-safe default is always REVIEW, never ALLOW.** Every failure path (Ollama down, no API
  key set, unparseable response, MCP server unreachable) resolves to a safe/neutral outcome, not
  a silent pass-through. Preserve this invariant in any new code.
- **Rules always win over models.** Stage 1's block patterns are checked before anything else,
  every time, with no exceptions. Don't introduce a code path where Stage 2/3 could override a
  Stage 1 BLOCK.
- **The dashboard talks directly to the API at `http://localhost:8000`** (hardcoded `API_BASE` in
  `dashboard/app.js`) — if you host the API somewhere other than localhost:8000, update that
  constant (or make it configurable — noted as a good next step if this goes beyond hackathon
  demo stage).
- **Background processes in this build environment are unreliable across tool calls** — if
  you're continuing this in a similar sandboxed setup, always start the server AND run your curl
  tests in the SAME shell command (chained with `&&`), because backgrounded servers reliably die
  between separate tool invocations. Also avoid `pkill` in that kind of environment — it can kill
  the tool's own shell session; prefer letting old servers die naturally or use precise `kill
  <pid>` instead.

---

## 8. HOW TO RESUME IN A NEW CHAT

If your session ends, in a new conversation:
1. Paste this entire document.
2. Tell Claude which checklist items above are already done (as of this doc, everything except
   the three unchecked GitHub/HuggingFace/Ollama-pull items is done and tested).
3. Say what you want next — e.g. "add a Dockerfile," "let me push this to GitHub and walk me
   through it," "add real remote rule management for external MCP servers," "help me pull an
   Ollama model and verify Stage 3 for real."

Claude does not retain memory between separate conversations unless you've enabled memory —
this doc is what replaces that.

---

## 9. IMMEDIATE NEXT STEPS (in priority order)

1. **Push to GitHub.** `cd sentinel && git init && git add . && git commit -m "Initial Sentinel build"`
   then create a GitHub repo and push. Double check `.gitignore` is respected (no `.pkl`,
   `sentinel.db`, or `model_config.local.yaml` should be committed).
2. **Push the classifier to Hugging Face.** Create a new model repo named
   `sentinel-risk-classifier`, upload the 3 files from that folder as-is.
3. **Pull a real Ollama model and verify Stage 3 end-to-end** with something genuinely ambiguous,
   e.g. `curl -X POST .../review -d '{"action_text": "delete the staging database", "user_task":
   "clean up test data"}'` and confirm the local model gives a sensible REVIEW/BLOCK verdict with
   a real explanation (not the fallback message).
4. **Rehearse the demo** using the script at the bottom of `README.md` — it now should also show
   off the Model Settings and MCP Servers tabs, not just the rule editor.
