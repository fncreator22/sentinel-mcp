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
    el.className = "api-status online";
    authBar.classList.toggle("hidden", !data.auth_required || !!SENTINEL_KEY);
    if (data.project_root) {
      projectRootPath = data.project_root;
      renderMcpConfig();
    }
  } catch (e) {
    el.textContent = "API offline — start it with: uvicorn api.main:app --port 8000";
    el.className = "api-status offline";
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
  // Update counts
  document.getElementById("countAllow").textContent = allow;
  document.getElementById("countReview").textContent = review;
  document.getElementById("countBlock").textContent = block;

  document.getElementById("countRules").textContent = rules;
  document.getElementById("countClassifier").textContent = classifier;
  document.getElementById("countLlm").textContent = llm;

  if (total > 0) {
    // Calculate percentages
    const pctAllow = (allow / total) * 100;
    const pctReview = (review / total) * 100;
    const pctBlock = (block / total) * 100;

    const pctRules = (rules / total) * 100;
    const pctClassifier = (classifier / total) * 100;
    const pctLlm = (llm / total) * 100;

    // Update widths
    document.getElementById("barAllow").style.width = `${pctAllow}%`;
    document.getElementById("barReview").style.width = `${pctReview}%`;
    document.getElementById("barBlock").style.width = `${pctBlock}%`;

    document.getElementById("barRules").style.width = `${pctRules}%`;
    document.getElementById("barClassifier").style.width = `${pctClassifier}%`;
    document.getElementById("barLlm").style.width = `${pctLlm}%`;
  } else {
    // Reset widths
    document.getElementById("barAllow").style.width = "0%";
    document.getElementById("barReview").style.width = "0%";
    document.getElementById("barBlock").style.width = "0%";

    document.getElementById("barRules").style.width = "0%";
    document.getElementById("barClassifier").style.width = "0%";
    document.getElementById("barLlm").style.width = "0%";
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
    statusEl.textContent = `Save failed: ${err.message}`;
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
  custom: { base_url: "", model: "" },
};

document.getElementById("apiProviderSelect").addEventListener("change", (e) => {
  const preset = API_PROVIDER_PRESETS[e.target.value];
  if (preset) {
    document.getElementById("apiBaseUrl").value = preset.base_url;
    document.getElementById("apiModelName").value = preset.model;
  }
});

async function loadLocalModels() {
  const statusEl = document.getElementById("localModelStatus");
  const listEl = document.getElementById("localModelList");
  statusEl.textContent = "Checking for local models...";
  statusEl.className = "status-line";

  try {
    const res = await apiFetch("/models/local");
    if (!res.ok) {
      throw new Error(`API returned HTTP ${res.status} for /models/local — is api/main.py up to date and restarted? See /docs to check the version.`);
    }
    const data = await res.json();

    if (!data.ollama_reachable) {
      statusEl.className = "status-line warn";
      statusEl.textContent = "Ollama isn't running or isn't installed on this machine.";
      listEl.innerHTML = "";
      return;
    }
    if (data.models.length === 0) {
      statusEl.className = "status-line warn";
      statusEl.textContent = "Ollama is running, but no models are pulled yet.";
      listEl.innerHTML = "";
      return;
    }

    statusEl.className = "status-line ok";
    statusEl.textContent = `Ollama is running — found ${data.models.length} model(s).`;

    const configRes = await apiFetch("/models/config");
    if (!configRes.ok) throw new Error(`API returned HTTP ${configRes.status} for /models/config`);
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
    statusEl.className = "status-line warn";
    statusEl.textContent = `Could not check for local models: ${err.message}`;
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
    if (!res.ok) {
      throw new Error(`API returned HTTP ${res.status} for /models/config — is api/main.py up to date and restarted? See /docs to check the version.`);
    }
    const data = await res.json();

    document.querySelector(`input[name="providerChoice"][value="${data.active_provider}"]`).checked = true;
    setProviderPanelVisibility(data.active_provider);

    document.getElementById("ollamaBaseUrl").value = data.local.ollama_base_url || "http://localhost:11434";
    document.getElementById("apiProviderSelect").value = data.api.provider;
    document.getElementById("apiBaseUrl").value = data.api.base_url;
    document.getElementById("apiModelName").value = data.api.model;
    document.getElementById("apiKeyStatus").textContent = data.api.api_key_set
      ? `(saved key ending in ${data.api.api_key_masked?.slice(-4) || "????"})`
      : "(no key saved yet)";
  } catch (err) { console.error("Could not load model config:", err); }
}

document.getElementById("saveModelSettings").addEventListener("click", async () => {
  const statusEl = document.getElementById("modelSaveStatus");
  const providerChoice = document.querySelector('input[name="providerChoice"]:checked').value;
  const selectedLocal = document.querySelector('input[name="localModelChoice"]:checked');

  const payload = {
    active_provider: providerChoice,
    local: {
      ollama_base_url: document.getElementById("ollamaBaseUrl").value.trim() || "http://localhost:11434",
      selected_model: selectedLocal ? selectedLocal.value : null,
    },
    api: {
      provider: document.getElementById("apiProviderSelect").value,
      base_url: document.getElementById("apiBaseUrl").value,
      model: document.getElementById("apiModelName").value,
      api_key: document.getElementById("apiKeyInput").value, // "" = keep existing
    },
  };

  try {
    const res = await apiFetch("/models/config", { method: "POST", body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    statusEl.className = "save-status ok";
    statusEl.textContent = "Model settings saved.";
    document.getElementById("apiKeyInput").value = "";
    loadModelConfig();
  } catch (err) {
    statusEl.className = "save-status error";
    statusEl.textContent = `Save failed: ${err.message}`;
  }
});

document.getElementById("testModelSettings").addEventListener("click", async () => {
  const statusEl = document.getElementById("modelTestStatus");
  statusEl.className = "save-status";
  statusEl.textContent = "Testing...";
  try {
    const res = await apiFetch("/models/test", { method: "POST" });
    const data = await res.json();
    statusEl.className = data.ok ? "save-status ok" : "save-status error";
    statusEl.textContent = data.message;
  } catch (err) {
    statusEl.className = "save-status error";
    statusEl.textContent = `Test failed: ${err.message}`;
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

// ---- Init ------------------------------------------------------------------------
function loadEverything() {
  checkApiStatus();
  loadPipelineStatus();
  loadFeed();
  loadRules();
  loadLocalModels();
  loadModelConfig();
  loadServers();
  startAutoRefresh();
}

setProviderPanelVisibility("local");
loadEverything();
