# Sentinel — Architecture Notes

## Design goals, in priority order

1. **Never let a genuinely dangerous action slip through.** This is why
   Stage 1 exists as a pure, deterministic pattern match with no model in
   the loop. A classifier or LLM can be wrong or unavailable; a substring/
   regex match on a known-dangerous pattern cannot.
2. **Be fast for the common case.** Most actions an agent takes are boring
   (`git status`, `npm install`, reading a file). Stage 1 and Stage 2 both
   resolve in milliseconds, so only genuinely ambiguous actions pay the
   cost of an LLM call in Stage 3.
3. **Be explainable.** Every decision — whichever stage made it — comes
   with a plain-English reason and is written to the audit log. Any
   future maintainer should be able to look at any decision and
   understand exactly why it was made.
4. **Be editable without redeploying.** The dashboard writes directly to
   `config/rules.yaml` and the API hot-reloads the rule engine in place
   (`RulesEngine.reload()`), so adding a new block pattern takes effect
   immediately.

## Why TF-IDF + Logistic Regression for Stage 2 (not a neural net)

- It is trained in seconds on a laptop CPU, no GPU required — no
  specialized hardware or cloud infrastructure needed.
- `model.pkl` + `vectorizer.pkl` together are a few hundred KB, trivial to
  publish on Hugging Face.
- The coefficients are directly inspectable — `train/train_classifier.py`
  prints the top risk-associated words/phrases after training, which is a
  strong, honest way to demonstrate "we trained this and understand what
  it learned" rather than treating it as a black box.
- It's a genuinely appropriate tool for this problem: action text is short,
  and risk signal is heavily concentrated in specific words/phrases
  (`rm -rf`, `force`, `drop table`, `sudo`, `.env`, etc.) — exactly the
  kind of signal a bag-of-words/n-gram model is good at picking up.

## Why the LLM stage is local (Ollama) and only used conditionally

Two reasons converge here:
- **Privacy/self-hosting claim.** If Stage 3 called an external API, "no
  data leaves the machine" would be false. Ollama keeps inference fully
  local.
- **Cost/latency.** LLM calls are the slowest, most expensive step in the
  pipeline. Gating Stage 3 behind Stage 2's confidence threshold means it's
  only invoked for the genuinely hard cases — which is also exactly where
  its reasoning ability (understanding *scope*, not just pattern-matching
  danger words) adds real value over Stage 2.

## Failure modes and how they're handled

| Failure                                   | Behavior                                                          |
|--------------------------------------------|---------------------------------------------------------------------|
| Ollama not running / unreachable           | `llm_reviewer.py` catches the connection error and returns `REVIEW` (safe default), never silently `ALLOW`. |
| LLM returns unparseable / non-JSON text    | `_parse_response` returns `None`, orchestrator defaults to `REVIEW`. |
| `rules.yaml` is hand-edited into invalid YAML | `RulesEngine.reload()` will raise on load; keep the last-known-good rules loaded until the file is fixed (recommended follow-up: validate on the `/rules` POST endpoint before writing to disk). |
| Classifier model files missing            | `RiskClassifier.__init__` raises a clear `FileNotFoundError` telling you to run `train/train_classifier.py` first. |

## Data flow for one action review (concrete example)

Action: `"git push --force origin main"`, user_task: `"deploy the fix"`

1. `Orchestrator.review()` calls `RulesEngine.check(...)`.
2. The rule engine's `block_patterns` list contains the literal pattern
   `"git push --force"` → immediate match.
3. Verdict `BLOCK` is returned with reason "Force push can overwrite remote
   history and destroy others' work."
4. `_finalize()` writes this to `audit_log.decisions` with
   `decided_by_stage = "rules_engine"`, `stage2_*` and `stage3_verdict`
   columns left `NULL` since those stages were never reached.
5. The API returns this decision as JSON; the dashboard's feed shows a red
   (`BLOCK`) row immediately on next poll.

## Why the model reviewer supports both local and API providers

Stage 3 originally called Ollama directly. It's now split into two layers on
purpose:

- `sentinel_core/model_manager.py` — knows about *infrastructure*: how to
  detect local Ollama models, how to store/load provider config with secrets
  properly isolated, and how to turn "whatever's configured" into a ready
  `.generate(prompt) -> str` object. It has three provider classes
  (`OllamaProvider`, `OpenAICompatibleProvider`, `AnthropicProvider`) behind
  one factory function, `get_active_provider()`.
- `sentinel_core/llm_reviewer.py` — knows about *Sentinel's Stage 3 logic*:
  the specific prompt asking about scope-creep and irreversible harm, and
  parsing the JSON verdict back out. It calls `get_active_provider()` and
  doesn't care what's on the other end.

This split means adding a fourth provider (say, a different local runtime,
or a different hosted API) only touches `model_manager.py` — Stage 3's
actual review logic never changes.

**Secrets handling:** `config/model_config.yaml` is committed and can only
ever contain non-secret settings (provider choice, base URL, model name).
The API key lives exclusively in `config/model_config.local.yaml`, which is
gitignored. `model_manager.save_config()` enforces this split on every
write — it pops `api_key` out of whatever it's given and writes it
separately. Every read that could reach an HTTP response goes through
`masked_config()`, which replaces the key with a last-4-characters view and
a boolean `api_key_set` flag. The raw key is never sent back to the
dashboard after being saved.

## Why the MCP server registry is scoped the way it is

`sentinel_core/mcp_registry.py` + `config/mcp_servers.yaml` let the
dashboard track multiple MCP servers, but deliberately do NOT proxy rule or
model management to external servers yet. Today it's a registry + live
health board: register a server's name and endpoint, and the dashboard
pings `{endpoint}/health` to show connected/unreachable/error. This is
sufficient to establish that the dashboard is architected for multi-instance
management, without overclaiming remote administration that isn't built yet.
Full remote config push/pull is the natural next step once there's a second
real server to build and test against.

## Why `/export` masks secrets instead of omitting the whole model section

An earlier design considered leaving `model_config` out of the export bundle
entirely. Instead it's included but always passed through
`model_manager.masked_config()` — so a teammate receiving the export can see
*which* provider and model you're using (useful context) without ever
receiving the key itself. This mirrors the same masking used by
`GET /models/config`, so there's only one code path that needs to be kept
secret-safe.

## Extending Sentinel

- **New hard-coded danger pattern:** add it to `config/rules.yaml` under
  `block_patterns` (via the dashboard or directly) — no code change or
  restart needed.
- **Retrain the classifier with more examples:** add rows to
  `data/training_examples.csv`, re-run `train/train_classifier.py`. It
  automatically re-syncs `model.pkl`/`vectorizer.pkl` into the sibling
  Hugging Face repo folder if present.
- **Change the Stage 3 model:** use the dashboard's Model Settings tab
  (local model picker or API provider fields) — no code change needed at
  all. To add a new *kind* of provider (not just a new model), add a class
  to `sentinel_core/model_manager.py` alongside `OllamaProvider` /
  `OpenAICompatibleProvider` / `AnthropicProvider`.
- **New integration surface (beyond API + MCP):** write a thin adapter that
  calls `Orchestrator.review(action_text, user_task)` — that's the entire
  contract. Everything else (rules, classifier, model selection, logging)
  is already handled.
