/* =============================================================================
   Sentinel Dashboard — app.js
   Talks to the FastAPI backend (api/main.py) at API_BASE. No build step —
   open index.html directly, or serve dashboard/ with any static server.
   ============================================================================= */

const API_BASE = "http://localhost:8000";
let SENTINEL_KEY = sessionStorage.getItem("sentinelKey") || "";
let autoRefreshTimer = null;

// ---- Small fetch wrapper that attaches the auth header when set ------------
async function apiFetch(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (SENTINEL_KEY) headers["X-Sentinel-Key"] = SENTINEL_KEY;
  return fetch(`${API_BASE}${path}`, { ...options, headers });
}

// ---- Tabs ---------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ---- Health / auth check ------------------------------------------------------
let projectRootPath = "";

function renderMcpConfig() {
  const preview = document.getElementById("mcpConfigPreview");
  if (!projectRootPath) {
    preview.textContent = "Loading integration config (awaiting API path)...";
    return;
  }
  const config = {
    "mcpServers": {
      "sentinel": {
        "command": "python",
        "args": ["mcp_server/server.py"],
        "cwd": projectRootPath
      }
    }
  };
  preview.textContent = JSON.stringify(config, null, 2);
}

document.getElementById("copyMcpConfigBtn").addEventListener("click", () => {
  const preview = document.getElementById("mcpConfigPreview");
  const status = document.getElementById("copyMcpStatus");
  
  navigator.clipboard.writeText(preview.textContent).then(() => {
    status.className = "save-status ok";
    status.textContent = "Copied!";
    setTimeout(() => { status.textContent = ""; }, 2500);
  }).catch(err => {
    status.className = "save-status error";
    status.textContent = "Failed to copy: " + err;
    setTimeout(() => { status.textContent = ""; }, 2500);
  });
});

async function checkApiStatus() {
  const el = document.getElementById("apiStatus");
  const authBar = document.getElementById("authBar");
  try {
    const res = await fetch(`${API_BASE}/health`);
    const data = await res.json();
    el.textContent = "API online";
    el.className = "api-badge online";
    authBar.classList.toggle("hidden", !data.auth_required || !!SENTINEL_KEY);
    if (data.project_root) {
      projectRootPath = data.project_root;
      renderMcpConfig();
    }
  } catch (e) {
    el.textContent = "API offline";
    el.title = "Start API: uvicorn api.main:app --port 8000";
    el.className = "api-badge";
  }
}

document.getElementById("saveAuthKey").addEventListener("click", () => {
  SENTINEL_KEY = document.getElementById("authKeyInput").value.trim();
  sessionStorage.setItem("sentinelKey", SENTINEL_KEY);
  document.getElementById("authBar").classList.add("hidden");
  loadEverything();
});

// ---- Review form ----------------------------------------------------------------
document.getElementById("reviewForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const actionText = document.getElementById("actionText").value.trim();
  const userTask = document.getElementById("userTask").value.trim();
  const resultEl = document.getElementById("reviewResult");
  if (!actionText) return;

  resultEl.className = "review-result";
  resultEl.textContent = "Reviewing...";

  try {
    const res = await apiFetch("/review", {
      method: "POST",
      body: JSON.stringify({ action_text: actionText, user_task: userTask }),
    });
    const data = await res.json();
    resultEl.className = `review-result ${data.verdict}`;
    resultEl.innerHTML = `<strong>${data.verdict}</strong> — decided by <em>${data.decided_by_stage}</em><br>${data.reason}`;
    
    // Trigger animated cyber threads pulse based on output
    if (typeof triggerPipelineAnimation === "function") {
      triggerPipelineAnimation(data.decided_by_stage, data.verdict);
    }
    
    loadFeed();
  } catch (err) {
    resultEl.className = "review-result BLOCK";
    resultEl.textContent = `Error contacting API: ${err.message}`;
  }
});

// ---- Live decision feed ------------------------------------------------------------
async function loadFeed() {
  const feedEl = document.getElementById("decisionFeed");
  try {
    const res = await apiFetch("/log?limit=50");
    const rows = await res.json();
    
    // Reset highlights on nodes
    document.querySelectorAll(".stage-node").forEach(node => node.classList.remove("pulse"));

    if (rows.length === 0) {
      feedEl.innerHTML = `<p style="color: var(--text-dim)">No decisions logged yet.</p>`;
      updateStats(0, 0, 0, 0, 0, 0);
      return;
    }
    
    feedEl.innerHTML = rows.map(row => `
      <div class="feed-row ${row.final_verdict}">
        <span class="verdict">${row.final_verdict}</span>
        <span class="stage">${row.decided_by_stage}</span>
        <span class="details">
          <span class="action">${escapeHtml(row.action_text)}</span>
          <span class="reason">${escapeHtml(row.reason || "")}</span>
        </span>
      </div>
    `).join("");

    // Calculate stats
    let allow = 0, review = 0, block = 0;
    let rules = 0, classifier = 0, llm = 0;

    rows.forEach(row => {
      // Verdict stats
      if (row.final_verdict === "ALLOW") allow++;
      else if (row.final_verdict === "REVIEW") review++;
      else if (row.final_verdict === "BLOCK") block++;

      // Stage stats
      if (row.decided_by_stage === "rules_engine") rules++;
      else if (row.decided_by_stage === "classifier") classifier++;
      else if (row.decided_by_stage === "llm_reviewer") llm++;
    });

    updateStats(allow, review, block, rules, classifier, llm, rows.length);

    // Pulse the node representing the latest decision stage
    const latestStage = rows[0].decided_by_stage;
    if (latestStage === "rules_engine") {
      document.getElementById("stageNode1").classList.add("pulse");
    } else if (latestStage === "classifier") {
      document.getElementById("stageNode2").classList.add("pulse");
    } else if (latestStage === "llm_reviewer") {
      document.getElementById("stageNode3").classList.add("pulse");
    }

  } catch (err) {
    feedEl.innerHTML = `<p style="color: var(--signal-block)">Could not load feed: ${err.message}</p>`;
  }
}

function updateStats(allow, review, block, rules, classifier, llm, total) {
  // Helper to safely update an element
  const setTxt = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  const setW   = (id, w)   => { const el = document.getElementById(id); if (el) el.style.width = w; };

  setTxt("countBlock",      block);
  setTxt("countRules",      rules);
  setTxt("countClassifier", classifier);
  setTxt("countLlm",        llm);

  if (total > 0) {
    setW("barRules",      `${(rules / total) * 100}%`);
    setW("barClassifier", `${(classifier / total) * 100}%`);
    setW("barLlm",        `${(llm / total) * 100}%`);
  } else {
    setW("barRules",      "0%");
    setW("barClassifier", "0%");
    setW("barLlm",        "0%");
  }
}


function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("refreshFeed").addEventListener("click", loadFeed);
document.getElementById("autoRefresh").addEventListener("change", (e) => {
  e.target.checked ? startAutoRefresh() : stopAutoRefresh();
});
function startAutoRefresh() { stopAutoRefresh(); autoRefreshTimer = setInterval(loadFeed, 3000); }
function stopAutoRefresh() { if (autoRefreshTimer) clearInterval(autoRefreshTimer); }

// ---- Rule editor -----------------------------------------------------------------
async function loadRules() {
  try {
    const res = await apiFetch("/rules");
    if (!res.ok) {
      throw new Error(`API returned HTTP ${res.status} for /rules`);
    }
    const data = await res.json();
    renderPatternList("blockPatterns", data.block_patterns || []);
    renderPatternList("allowPatterns", data.allow_patterns || []);
    const threshold = data.classifier_confidence_threshold ?? 0.75;
    document.getElementById("confidenceThreshold").value = threshold;
    document.getElementById("confidenceValue").textContent = threshold;
  } catch (err) { console.error("Could not load rules:", err); }
}

function renderPatternList(containerId, patterns) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  patterns.forEach(p => container.appendChild(makePatternRow(p)));
}

function makePatternRow(rule = { pattern: "", reason: "", regex: false }) {
  const row = document.createElement("div");
  row.className = "rule-row";
  row.innerHTML = `
    <input type="text" class="pattern-input" placeholder="pattern" value="${escapeAttr(rule.pattern)}" />
    <input type="text" class="reason-input" placeholder="reason shown in logs" value="${escapeAttr(rule.reason)}" />
    <label class="regex-toggle"><input type="checkbox" class="regex-input" ${rule.regex ? "checked" : ""} /> regex</label>
    <button type="button" class="remove-btn">Remove</button>
  `;
  row.querySelector(".remove-btn").addEventListener("click", () => row.remove());
  return row;
}

function escapeAttr(str) { return (str || "").replace(/"/g, "&quot;"); }

document.getElementById("addBlockPattern").addEventListener("click", () => {
  document.getElementById("blockPatterns").appendChild(makePatternRow());
});
document.getElementById("addAllowPattern").addEventListener("click", () => {
  document.getElementById("allowPatterns").appendChild(makePatternRow());
});
document.getElementById("confidenceThreshold").addEventListener("input", (e) => {
  document.getElementById("confidenceValue").textContent = e.target.value;
});

function collectPatterns(containerId) {
  const rows = document.querySelectorAll(`#${containerId} .rule-row`);
  return Array.from(rows).map(row => ({
    pattern: row.querySelector(".pattern-input").value,
    reason: row.querySelector(".reason-input").value,
    regex: row.querySelector(".regex-input").checked,
  })).filter(r => r.pattern.trim() !== "");
}

document.getElementById("saveRules").addEventListener("click", async () => {
  const statusEl = document.getElementById("saveStatus");
  const payload = {
    block_patterns: collectPatterns("blockPatterns"),
    allow_patterns: collectPatterns("allowPatterns"),
    classifier_confidence_threshold: parseFloat(document.getElementById("confidenceThreshold").value),
  };
  try {
    const res = await apiFetch("/rules", { method: "POST", body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    statusEl.className = "save-status ok";
    statusEl.textContent = "Saved and reloaded — new rules are live.";
  } catch (err) {
    statusEl.className = "save-status error";
    statusEl.textContent = `❌ ${friendlyError(err, "Could not save rules")}`;
  }
});

// ---- Model settings ----------------------------------------------------------------
function setProviderPanelVisibility(choice) {
  document.getElementById("localModelPanel").classList.toggle("hidden", choice !== "local");
  document.getElementById("apiModelPanel").classList.toggle("hidden", choice !== "api");
}

document.querySelectorAll('input[name="providerChoice"]').forEach(radio => {
  radio.addEventListener("change", (e) => setProviderPanelVisibility(e.target.value));
});

const API_PROVIDER_PRESETS = {
  openai: { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  anthropic: { base_url: "https://api.anthropic.com/v1", model: "claude-sonnet-4-5" },
  gemini: { base_url: "https://generativelanguage.googleapis.com/v1beta", model: "gemini-1.5-flash" },
  custom: { base_url: "", model: "" }
};

// ---- Friendly error messages -----------------------------------------------
/**
 * Convert any raw error/response into a clean, user-facing string.
 * Never shows raw stack traces or API internals to the user.
 */
function friendlyError(err, context) {
  if (!err) return "Something went wrong. Please try again.";
  const raw = (err.message || String(err)).toLowerCase();

  // Network / reachability
  if (raw.includes("failed to fetch") || raw.includes("networkerror") || raw.includes("network request failed"))
    return "Cannot reach the Sentinel API. Make sure start.bat is running and try refreshing.";
  if (raw.includes("getaddrinfo") || raw.includes("name resolution") || raw.includes("no route"))
    return "No internet connection. Check your network and try again.";
  if (raw.includes("timed out") || raw.includes("timeout"))
    return "The request timed out. The server may be busy — try again in a moment.";

  // HTTP status codes
  if (raw.includes("401") || raw.includes("unauthorized"))
    return "Invalid API key. Double-check that you pasted it correctly and re-save.";
  if (raw.includes("403") || raw.includes("forbidden"))
    return "Access denied. Your API key may not have permission for this action.";
  if (raw.includes("404"))
    return "Endpoint not found. The Sentinel API may need to be restarted after the latest update.";
  if (raw.includes("429") || raw.includes("rate limit"))
    return "Too many requests. Wait a moment and try again.";
  if (raw.includes("500") || raw.includes("internal server error"))
    return "The Sentinel API encountered an internal error. Check the terminal for details.";

  // Model-specific
  if (raw.includes("model") && (raw.includes("not found") || raw.includes("does not exist")))
    return "The selected model was not found on this account. Pick another from the dropdown.";
  if (raw.includes("api key") || raw.includes("api_key"))
    return "API key problem. Check that the key is correct and has the required permissions.";

  // Generic fallback — still clean, no raw internals shown
  if (context) return `${context}. Please check your settings and try again.`;
  return "Something went wrong. Please check your settings and try again.";
}

// ---- Static fallback model lists (shown before API key is saved) -----------
const PROVIDER_MODELS_FALLBACK = {
  openai:    ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  anthropic: ["claude-3-5-sonnet-20240620", "claude-3-5-haiku-20241022", "claude-3-opus-20240229", "claude-3-haiku-20240307"],
  gemini:    ["gemini-1.5-flash-latest", "gemini-1.5-pro-latest", "gemini-2.0-flash-exp", "gemini-1.0-pro"],
  custom:    [],
};

const PROVIDER_DEFAULTS = {
  openai:    "gpt-4o-mini",
  anthropic: "claude-3-5-sonnet-20240620",
  gemini:    "gemini-1.5-flash-latest",
  custom:    "",
};

// ---- Live model fetching ---------------------------------------------------
async function fetchAvailableModels(provider) {
  const datalist   = document.getElementById("modelNameList");
  const hint       = document.getElementById("modelNameHint");
  const fetchBtn   = document.getElementById("fetchModelsBtn");

  if (!datalist) return;

  // Show loading state
  if (hint) { hint.textContent = "⏳ Fetching available models from the API…"; hint.className = "hint small"; }
  if (fetchBtn) { fetchBtn.disabled = true; fetchBtn.textContent = "Fetching…"; }

  try {
    const res  = await apiFetch("/models/available");
    const data = await res.json();

    // Populate datalist
    datalist.innerHTML = "";
    const modelList = (data.models && data.models.length > 0)
      ? data.models
      : (PROVIDER_MODELS_FALLBACK[provider] || []);

    modelList.forEach(id => {
      const opt   = document.createElement("option");
      opt.value   = id;
      datalist.appendChild(opt);
    });

    if (data.ok && data.models && data.models.length > 0) {
      if (hint) {
        hint.textContent = `✅ ${data.models.length} models available on your account. Pick one or leave blank to auto-select.`;
        hint.className = "hint small ok";
      }
    } else {
      // Key not set yet or error — show static fallback
      const reason = data.error || "Save your API key first to see live models.";
      if (hint) {
        hint.textContent = `ℹ️ Showing suggested models. ${reason}`;
        hint.className = "hint small warn";
      }
    }
  } catch (err) {
    // Network error — populate with static fallback silently
    const fallback = PROVIDER_MODELS_FALLBACK[provider] || [];
    datalist.innerHTML = "";
    fallback.forEach(id => {
      const opt = document.createElement("option");
      opt.value = id;
      datalist.appendChild(opt);
    });
    if (hint) {
      hint.textContent = `ℹ️ Could not reach API — showing suggested models. ${friendlyError(err, "Model fetch")}`;
      hint.className = "hint small warn";
    }
  } finally {
    if (fetchBtn) { fetchBtn.disabled = false; fetchBtn.textContent = "↻ Refresh"; }
    const def = PROVIDER_DEFAULTS[provider];
    // Only update hint if still showing default text
    if (def && hint && hint.textContent === "") {
      hint.textContent = `Auto-select will use: ${def}`;
    }
  }
}

document.getElementById("apiProviderSelect").addEventListener("change", (e) => {
  const preset = API_PROVIDER_PRESETS[e.target.value];
  if (preset) {
    document.getElementById("apiBaseUrl").value  = preset.base_url;
    document.getElementById("apiModelName").value = preset.model;
  }
  fetchAvailableModels(e.target.value);
});

// "✕ Auto" button — clear the model name so the backend auto-selects
document.getElementById("clearModelName")?.addEventListener("click", () => {
  document.getElementById("apiModelName").value = "";
  document.getElementById("apiModelName").focus();
});

// "↻ Refresh" button — re-fetch models on demand
document.getElementById("fetchModelsBtn")?.addEventListener("click", () => {
  const provider = document.getElementById("apiProviderSelect").value;
  fetchAvailableModels(provider);
});


async function loadLocalModels() {
  const statusEl = document.getElementById("localModelStatus");
  const listEl   = document.getElementById("localModelList");
  statusEl.textContent = "Checking for local models…";
  statusEl.className   = "status-line";

  try {
    const res = await apiFetch("/models/local");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (!data.ollama_reachable) {
      statusEl.className   = "status-line warn";
      statusEl.textContent = "Ollama isn't running or isn't installed on this machine.";
      listEl.innerHTML = "";
      return;
    }
    if (data.models.length === 0) {
      statusEl.className   = "status-line warn";
      statusEl.textContent = "Ollama is running, but no models are pulled yet. Run: ollama pull llama3.2";
      listEl.innerHTML = "";
      return;
    }

    statusEl.className   = "status-line ok";
    statusEl.textContent = `Ollama is running — found ${data.models.length} model(s).`;

    const configRes = await apiFetch("/models/config");
    if (!configRes.ok) throw new Error(`HTTP ${configRes.status}`);
    const currentConfig = await configRes.json();
    const selected = currentConfig.local?.selected_model;

    listEl.innerHTML = data.models.map(m => `
      <label class="model-option">
        <input type="radio" name="localModelChoice" value="${escapeAttr(m.name)}" ${m.name === selected ? "checked" : ""} />
        <span>${escapeHtml(m.name)}</span>
        <span class="size">${m.size_bytes ? formatBytes(m.size_bytes) : ""}</span>
      </label>
    `).join("");
  } catch (err) {
    statusEl.className   = "status-line warn";
    statusEl.textContent = friendlyError(err, "Could not check for local models");
  }
}

function formatBytes(bytes) {
  const gb = bytes / (1024 ** 3);
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / (1024 ** 2)).toFixed(0)} MB`;
}

document.getElementById("refreshLocalModels").addEventListener("click", loadLocalModels);

async function loadModelConfig() {
  try {
    const res = await apiFetch("/models/config");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    document.querySelector(`input[name="providerChoice"][value="${data.active_provider}"]`).checked = true;
    setProviderPanelVisibility(data.active_provider);

    const providerText = document.getElementById("activeProviderText");
    if (providerText) providerText.textContent = data.active_provider.toUpperCase();

    document.getElementById("ollamaBaseUrl").value = data.local.ollama_base_url || "http://localhost:11434";

    const providerVal = data.api.provider || "openai";
    document.getElementById("apiProviderSelect").value  = providerVal;
    document.getElementById("apiBaseUrl").value         = data.api.base_url;
    document.getElementById("apiModelName").value       = data.api.model;
    document.getElementById("apiKeyStatus").textContent = data.api.api_key_set
      ? `(saved key ending in ${data.api.api_key_masked?.slice(-4) || "????"})` 
      : "(no key saved yet)";

    // Fetch live models for the current provider
    fetchAvailableModels(providerVal);

  } catch (err) {
    console.warn("Could not load model config:", err);
  }
}

document.getElementById("saveModelSettings").addEventListener("click", async () => {
  const statusEl      = document.getElementById("modelSaveStatus");
  const providerChoice = document.querySelector('input[name="providerChoice"]:checked').value;
  const selectedLocal  = document.querySelector('input[name="localModelChoice"]:checked');

  const payload = {
    active_provider: providerChoice,
    local: {
      ollama_base_url: document.getElementById("ollamaBaseUrl").value.trim() || "http://localhost:11434",
      selected_model:  selectedLocal ? selectedLocal.value : null,
    },
    api: {
      provider: document.getElementById("apiProviderSelect").value,
      base_url: document.getElementById("apiBaseUrl").value,
      model:    document.getElementById("apiModelName").value,
      api_key:  document.getElementById("apiKeyInput").value, // "" = keep existing
    },
  };

  statusEl.className   = "save-status";
  statusEl.textContent = "Saving…";

  try {
    const res = await apiFetch("/models/config", { method: "POST", body: JSON.stringify(payload) });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `HTTP ${res.status}`);
    }
    statusEl.className   = "save-status ok";
    statusEl.textContent = "✅ Settings saved.";
    document.getElementById("apiKeyInput").value = "";
    loadModelConfig();
    // Auto-fetch live models now that key is saved
    if (providerChoice === "api") {
      setTimeout(() => fetchAvailableModels(payload.api.provider), 500);
    }
  } catch (err) {
    statusEl.className   = "save-status error";
    statusEl.textContent = `❌ ${friendlyError(err, "Could not save settings")}`;
  }
});

document.getElementById("testModelSettings").addEventListener("click", async () => {
  const statusEl = document.getElementById("modelTestStatus");
  statusEl.className   = "save-status";
  statusEl.textContent = "⏳ Testing connection…";
  try {
    const res  = await apiFetch("/models/test", { method: "POST" });
    const data = await res.json();
    statusEl.className   = data.ok ? "save-status ok" : "save-status error";
    statusEl.textContent = data.ok
      ? `✅ ${data.message}`
      : `❌ ${data.message}`;
  } catch (err) {
    statusEl.className   = "save-status error";
    statusEl.textContent = `❌ ${friendlyError(err, "Connection test failed")}`;
  }
});

// ---- MCP servers -----------------------------------------------------------------
async function loadServers() {
  const listEl = document.getElementById("serverList");
  try {
    const res = await apiFetch("/mcp/servers");
    if (!res.ok) {
      throw new Error(`API returned HTTP ${res.status} for /mcp/servers — is api/main.py up to date and restarted? See /docs to check the version.`);
    }
    const data = await res.json();
    listEl.innerHTML = data.servers.map(s => `
      <div class="server-card ${s.health?.status || ''}">
        <span class="badge">${s.health?.status || 'unknown'}</span>
        <div class="info">
          <strong>${escapeHtml(s.name)}</strong>
          <span>${escapeHtml(s.endpoint)}${s.description ? " — " + escapeHtml(s.description) : ""}</span>
        </div>
        <span></span>
        ${s.type === "self" ? "" : `<button class="remove-btn" data-name="${escapeAttr(s.name)}">Remove</button>`}
      </div>
    `).join("");
    listEl.querySelectorAll(".remove-btn").forEach(btn => {
      btn.addEventListener("click", () => removeServer(btn.dataset.name));
    });
  } catch (err) {
    listEl.innerHTML = `<p style="color: var(--signal-block)">Could not load servers: ${err.message}</p>`;
  }
}

document.getElementById("addServerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const statusEl = document.getElementById("serverStatus");
  const payload = {
    name: document.getElementById("serverName").value.trim(),
    endpoint: document.getElementById("serverEndpoint").value.trim(),
    description: document.getElementById("serverDescription").value.trim(),
  };
  try {
    const res = await apiFetch("/mcp/servers", { method: "POST", body: JSON.stringify(payload) });
    if (!res.ok) throw new Error((await res.json()).detail || `HTTP ${res.status}`);
    statusEl.className = "save-status ok";
    statusEl.textContent = "Server registered.";
    document.getElementById("addServerForm").reset();
    loadServers();
  } catch (err) {
    statusEl.className = "save-status error";
    statusEl.textContent = `Could not register server: ${err.message}`;
  }
});

async function removeServer(name) {
  try {
    const res = await apiFetch(`/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    loadServers();
  } catch (err) {
    console.error("Could not remove server:", err);
  }
}

// ---- Export --------------------------------------------------------------------
document.getElementById("exportBtn").addEventListener("click", async () => {
  const preview = document.getElementById("exportPreview");
  try {
    const res = await apiFetch("/export");
    if (!res.ok) {
      throw new Error(`API returned HTTP ${res.status} for /export — is api/main.py up to date and restarted? See /docs to check the version.`);
    }
    const data = await res.json();
    const json = JSON.stringify(data, null, 2);
    preview.textContent = json;

    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "sentinel-config-export.json";
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    preview.textContent = `Export failed: ${err.message}`;
  }
});

// ---- Pause / Resume guardrail -----------------------------------------------
let sentinelPaused = false;

function updatePauseButton() {
  const badge = document.getElementById("pipelineBadge");
  const btn = document.getElementById("pauseResumeBtn");
  const ringFill = document.getElementById("ringFill");
  const ringLabel = document.getElementById("ringLabel");
  const stateText = document.getElementById("pipelineStateText");

  if (!badge || !btn) return;
  if (sentinelPaused) {
    badge.textContent = "Paused";
    badge.className = "pipeline-badge paused";
    btn.textContent = "Resume Guardrail";
    btn.className = "resume-btn";
    if (ringLabel) ringLabel.textContent = "Paused";
    if (ringFill) {
      ringFill.style.stroke = "var(--signal-review)";
      ringFill.style.strokeDashoffset = 241.3; // 20% filled
    }
    if (stateText) {
      stateText.textContent = "PAUSED";
      stateText.className = "value warning";
    }
  } else {
    badge.textContent = "Active";
    badge.className = "pipeline-badge active";
    btn.textContent = "Pause Guardrail";
    btn.className = "pause-btn";
    if (ringLabel) ringLabel.textContent = "Active";
    if (ringFill) {
      ringFill.style.stroke = "var(--accent)";
      ringFill.style.strokeDashoffset = 30.2; // 90% filled
    }
    if (stateText) {
      stateText.textContent = "ACTIVE";
      stateText.className = "value active";
    }
  }
}


async function loadPipelineStatus() {
  try {
    const res = await apiFetch("/status");
    const data = await res.json();
    sentinelPaused = data.paused;
    updatePauseButton();
  } catch (e) {
    // API offline — leave button in default state
  }
}

document.getElementById("pauseResumeBtn").addEventListener("click", async () => {
  const endpoint = sentinelPaused ? "/resume" : "/pause";
  try {
    const res = await apiFetch(endpoint, { method: "POST" });
    const data = await res.json();
    sentinelPaused = data.paused;
    updatePauseButton();
    // Refresh the feed so the paused state change is reflected in the log
    setTimeout(loadFeed, 200);
  } catch (e) {
    console.error("Failed to toggle guardrail pause state:", e);
  }
});

// ---- Connect Modal (Timeline Guide) ------------------------------------------
const connectModal = document.getElementById("connectModal");
const connectModalBtn = document.getElementById("connectModalBtn");
const closeConnectModal = document.getElementById("closeConnectModal");
const copyEndpointBtn = document.getElementById("copyEndpointBtn");

if (connectModalBtn && connectModal) {
  connectModalBtn.addEventListener("click", () => {
    connectModal.classList.remove("hidden");
  });
}

if (closeConnectModal && connectModal) {
  closeConnectModal.addEventListener("click", () => {
    connectModal.classList.add("hidden");
  });
  
  // Close when clicking outside content area
  connectModal.addEventListener("click", (e) => {
    if (e.target === connectModal) {
      connectModal.classList.add("hidden");
    }
  });
}

if (copyEndpointBtn) {
  copyEndpointBtn.addEventListener("click", () => {
    const val = document.getElementById("connectEndpointVal").value;
    const status = document.getElementById("copyEndpointStatus");
    
    navigator.clipboard.writeText(val).then(() => {
      status.className = "save-status ok";
      status.textContent = "Copied!";
      setTimeout(() => { status.textContent = ""; }, 2000);
    }).catch(err => {
      status.className = "save-status error";
      status.textContent = "Failed: " + err;
      setTimeout(() => { status.textContent = ""; }, 2000);
    });
  });
}

// Connect Modal Tabs (Stdio vs SSE)
const tabStdio = document.getElementById("tabStdio");
const tabSse = document.getElementById("tabSse");
const panelStdio = document.getElementById("panelStdio");
const panelSse = document.getElementById("panelSse");

if (tabStdio && tabSse && panelStdio && panelSse) {
  tabStdio.addEventListener("click", () => {
    tabStdio.classList.add("active");
    tabSse.classList.remove("active");
    panelStdio.classList.remove("hidden");
    panelSse.classList.add("hidden");
  });
  tabSse.addEventListener("click", () => {
    tabSse.classList.add("active");
    tabStdio.classList.remove("active");
    panelSse.classList.remove("hidden");
    panelStdio.classList.add("hidden");
    checkSseStatus();
  });
}

const copySseEndpointBtn = document.getElementById("copySseEndpointBtn");
if (copySseEndpointBtn) {
  copySseEndpointBtn.addEventListener("click", () => {
    const val = document.getElementById("sseEndpointVal").value;
    const status = document.getElementById("copySseEndpointStatus");
    
    navigator.clipboard.writeText(val).then(() => {
      status.className = "save-status ok";
      status.textContent = "Copied!";
      setTimeout(() => { status.textContent = ""; }, 2000);
    }).catch(err => {
      status.className = "save-status error";
      status.textContent = "Failed: " + err;
      setTimeout(() => { status.textContent = ""; }, 2000);
    });
  });
}

async function checkSseStatus() {
  const badge = document.getElementById("sseBadge");
  const text = document.getElementById("sseBadgeText");
  if (!badge || !text) return;
  try {
    const res = await fetch("http://localhost:8002/sse", { method: "HEAD" });
    badge.className = "conn-badge online-badge";
    text.textContent = "SSE Server online on port 8002";
  } catch (e) {
    badge.className = "conn-badge offline-badge";
    text.textContent = "SSE Server offline. Run: python mcp_server/sse_server.py";
  }
}

// ---- Init ------------------------------------------------------------------------
function loadEverything() {
  checkApiStatus();
  loadPipelineStatus();
  loadFeed();
  loadRules();
  loadLocalModels();
  loadModelConfig();
  loadServers();
  checkSseStatus();
  startAutoRefresh();
}

setProviderPanelVisibility("local");
loadEverything();

// =============================================================================
// CyberDefend Interactive Canvas Particle Pipelines and Threat Monitor Waves
// =============================================================================

// ----- Thread Canvas (Middle-Right Panel) -----
const threadCanvas = document.getElementById("threadCanvas");
const threadCtx = threadCanvas ? threadCanvas.getContext("2d") : null;

let threadNodes = {
  stages: [],     // Left sources (Stage 1, Stage 2, Stage 3)
  components: [], // Middle routers (Towers)
  terminals: []   // Right sinks (Stdio, SSE, DB)
};

let threadParticles = [];
let ambientTimer = 0;

function initThreadCanvas() {
  if (!threadCanvas) return;
  
  // Set resolution based on CSS sizing
  const rect = threadCanvas.getBoundingClientRect();
  threadCanvas.width = rect.width * window.devicePixelRatio;
  threadCanvas.height = rect.height * window.devicePixelRatio;
  threadCtx.scale(window.devicePixelRatio, window.devicePixelRatio);
  
  const w = rect.width;
  const h = rect.height;
  
  // Calculate relative coordinates matching the layout cards
  threadNodes.stages = [
    { x: 30, y: h * 0.22, name: "Rules Engine" },
    { x: 30, y: h * 0.50, name: "Classifier" },
    { x: 30, y: h * 0.78, name: "LLM Reviewer" }
  ];
  
  threadNodes.components = [
    { x: w * 0.35, y: h * 0.28, name: "stdio_mcp" },
    { x: w * 0.35, y: h * 0.48, name: "sse_mcp" },
    { x: w * 0.35, y: h * 0.68, name: "pattern_val" }
  ];
  
  threadNodes.terminals = [
    { x: w - 120, y: h * 0.25, name: "Stdio Client" },
    { x: w - 120, y: h * 0.50, name: "SSE Gateway" },
    { x: w - 120, y: h * 0.75, name: "Audit Logger" }
  ];
}

class ThreadParticle {
  constructor(pathPoints, color, speed, size, pulseOnEnd = false) {
    this.path = pathPoints; // Array of points [{x, y}, ...]
    this.color = color;
    this.speed = speed;     // progress increment per frame
    this.size = size;
    this.progress = 0;      // 0 to 1
    this.pulseOnEnd = pulseOnEnd;
  }
  
  update() {
    this.progress += this.speed;
    return this.progress >= 1;
  }
  
  draw(ctx) {
    if (this.path.length < 2) return;
    
    // Find point coordinates along the multi-segment bezier path
    const segCount = this.path.length - 1;
    const scaledT = this.progress * segCount;
    const segIdx = Math.floor(scaledT);
    const t = scaledT - segIdx;
    
    let x, y;
    
    if (segIdx >= segCount) {
      const last = this.path[this.path.length - 1];
      x = last.x; y = last.y;
    } else {
      const p0 = this.path[segIdx];
      const p1 = this.path[segIdx + 1];
      
      // Compute bezier control points for a smooth curve
      const cpX1 = p0.x + (p1.x - p0.x) * 0.5;
      const cpY1 = p0.y;
      const cpX2 = p0.x + (p1.x - p0.x) * 0.5;
      const cpY2 = p1.y;
      
      // Cubic Bezier interpolation formula
      const mt = 1 - t;
      x = mt * mt * mt * p0.x + 3 * mt * mt * t * cpX1 + 3 * mt * t * t * cpX2 + t * t * t * p1.x;
      y = mt * mt * mt * p0.y + 3 * mt * mt * t * cpY1 + 3 * mt * t * t * cpY2 + t * t * t * p1.y;
    }
    
    // Draw glowing particle
    ctx.shadowBlur = 10;
    ctx.shadowColor = this.color;
    ctx.fillStyle = this.color;
    ctx.beginPath();
    ctx.arc(x, y, this.size, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0; // reset
  }
}

// Draw the underlying glow threads
function drawConnectionTracks(ctx, w, h) {
  ctx.strokeStyle = "rgba(0, 217, 245, 0.05)";
  ctx.lineWidth = 1.5;
  
  // Connect Stages to Components
  threadNodes.stages.forEach(st => {
    threadNodes.components.forEach(cp => {
      ctx.beginPath();
      ctx.moveTo(st.x, st.y);
      ctx.bezierCurveTo(
        st.x + (cp.x - st.x) * 0.5, st.y,
        st.x + (cp.x - st.x) * 0.5, cp.y,
        cp.x, cp.y
      );
      ctx.stroke();
    });
  });
  
  // Connect Components to Terminals
  threadNodes.components.forEach(cp => {
    threadNodes.terminals.forEach(tm => {
      ctx.beginPath();
      ctx.moveTo(cp.x, cp.y);
      ctx.bezierCurveTo(
        cp.x + (tm.x - cp.x) * 0.5, cp.y,
        cp.x + (tm.x - cp.x) * 0.5, tm.y,
        tm.x, tm.y
      );
      ctx.stroke();
    });
  });
}

function animateThreads() {
  if (!threadCanvas || !threadCtx) return;
  
  const w = threadCanvas.width / window.devicePixelRatio;
  const h = threadCanvas.height / window.devicePixelRatio;
  
  threadCtx.clearRect(0, 0, w, h);
  
  // Draw glowing grid lines or tracks
  drawConnectionTracks(threadCtx, w, h);
  
  // Spawn ambient packet particles
  ambientTimer++;
  if (ambientTimer % 45 === 0 && !sentinelPaused) {
    // Choose random path Stage -> Component -> Terminal
    const st = threadNodes.stages[Math.floor(Math.random() * threadNodes.stages.length)];
    const cp = threadNodes.components[Math.floor(Math.random() * threadNodes.components.length)];
    const tm = threadNodes.terminals[Math.floor(Math.random() * threadNodes.terminals.length)];
    
    threadParticles.push(new ThreadParticle(
      [st, cp, tm],
      "rgba(0, 217, 245, 0.5)", // ambient cyan
      0.005 + Math.random() * 0.003,
      1.5
    ));
  }
  
  // Update and draw active packets
  for (let i = threadParticles.length - 1; i >= 0; i--) {
    const p = threadParticles[i];
    p.draw(threadCtx);
    const finished = p.update();
    if (finished) {
      threadParticles.splice(i, 1);
    }
  }
  
  requestAnimationFrame(animateThreads);
}

// Triggers a custom high-visibility particle package when a review completes
function triggerPipelineAnimation(decidedByStage, verdict) {
  if (!threadCanvas) return;
  
  // Determine verdict color
  let color = "var(--signal-allow)"; // green
  if (verdict === "BLOCK") color = "var(--signal-block)"; // red
  if (verdict === "REVIEW") color = "var(--signal-review)"; // orange
  
  // Build particle path based on which stage resolved the query
  const path = [];
  
  // Always starts from incoming request stage 1
  path.push(threadNodes.stages[0]);
  
  if (decidedByStage === "rules_engine") {
    // Direct from stage 1 -> components -> audit DB
    path.push(threadNodes.components[2]);
    path.push(threadNodes.terminals[2]);
  } else if (decidedByStage === "classifier") {
    // Flow: Stage 1 -> Stage 2 -> Component -> Terminal
    path.push(threadNodes.stages[1]);
    path.push(threadNodes.components[0]);
    path.push(threadNodes.terminals[0]);
  } else if (decidedByStage === "llm_reviewer") {
    // Full path flow: Stage 1 -> Stage 2 -> Stage 3 -> Component -> Terminal
    path.push(threadNodes.stages[1]);
    path.push(threadNodes.stages[2]);
    path.push(threadNodes.components[1]);
    path.push(threadNodes.terminals[1]);
  } else {
    // Fallback
    path.push(threadNodes.components[0]);
    path.push(threadNodes.terminals[0]);
  }
  
  // Shoot 2 packets consecutively for high visual feedback
  threadParticles.push(new ThreadParticle(path, color, 0.015, 3.5, true));
  setTimeout(() => {
    threadParticles.push(new ThreadParticle(path, color, 0.018, 2.5, false));
  }, 150);
  
  // Trigger oscilloscope wave surge spike
  triggerWaveSurge(decidedByStage);
}


// ----- Oscilloscope Wave Canvas (Bottom Monitor) -----
const waveCanvas = document.getElementById("waveCanvas");
const waveCtx = waveCanvas ? waveCanvas.getContext("2d") : null;

let waves = [
  { amp: 10, targetAmp: 10, freq: 0.012, speed: 0.015, phase: 0, color: "rgba(59, 130, 246, 0.25)" },  // Rules (Blue)
  { amp: 14, targetAmp: 14, freq: 0.008, speed: 0.010, phase: 0, color: "rgba(0, 230, 118, 0.25)" }, // Classifier (Green)
  { amp: 8,  targetAmp: 8,  freq: 0.020, speed: 0.022, phase: 0, color: "rgba(0, 217, 245, 0.25)" }  // LLM (Cyan)
];

function initWaveCanvas() {
  if (!waveCanvas) return;
  const rect = waveCanvas.getBoundingClientRect();
  waveCanvas.width = rect.width * window.devicePixelRatio;
  waveCanvas.height = rect.height * window.devicePixelRatio;
  waveCtx.scale(window.devicePixelRatio, window.devicePixelRatio);
}

function animateWaves() {
  if (!waveCanvas || !waveCtx) return;
  
  const w = waveCanvas.width / window.devicePixelRatio;
  const h = waveCanvas.height / window.devicePixelRatio;
  
  waveCtx.clearRect(0, 0, w, h);
  
  // Animate and draw each sine wave
  waves.forEach((wv, idx) => {
    // Gradual dampening of surge amplitudes back to base
    if (wv.amp > wv.targetAmp) {
      wv.amp -= 0.35; // decay
    } else if (wv.amp < wv.targetAmp) {
      wv.amp = wv.targetAmp;
    }
    
    wv.phase += wv.speed;
    
    waveCtx.beginPath();
    waveCtx.moveTo(0, h / 2);
    
    for (let x = 0; x < w; x++) {
      // Compute overlapping multi-wave sine curves
      const y = h / 2 + Math.sin(x * wv.freq + wv.phase) * wv.amp;
      waveCtx.lineTo(x, y);
    }
    
    // Add wave stroke
    waveCtx.strokeStyle = wv.color.replace("0.25", "0.85");
    waveCtx.lineWidth = idx === 2 ? 2.5 : 1.5;
    waveCtx.stroke();
    
    // Fill area below wave
    waveCtx.lineTo(w, h);
    waveCtx.lineTo(0, h);
    waveCtx.fillStyle = wv.color;
    waveCtx.fill();
  });
  
  requestAnimationFrame(animateWaves);
}

function triggerWaveSurge(decidedByStage) {
  // Increase wave amplitude dynamically based on matching stage
  if (decidedByStage === "rules_engine" && waves[0]) {
    waves[0].amp = 35; // spike
  } else if (decidedByStage === "classifier" && waves[1]) {
    waves[1].amp = 45; // spike
  } else if (decidedByStage === "llm_reviewer" && waves[2]) {
    waves[2].amp = 30; // spike
  }
}

// Window sizing bindings
window.addEventListener("resize", () => {
  initThreadCanvas();
  initWaveCanvas();
});

// Start animations
setTimeout(() => {
  initThreadCanvas();
  initWaveCanvas();
  animateThreads();
  animateWaves();
}, 200);


