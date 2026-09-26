/* =============================================================================
 * AI Creative Studio — Enterprise Frontend Engine
 * Version 2.0  |  2026
 * =============================================================================
 * Preserves all original pipeline functionality and adds:
 *   • Autopilot Engine (multi-platform one-click campaigns)
 *   • Brand DNA renderer
 *   • Quality score badges
 *   • Platform copy variant tabs
 *   • Campaign bundle renderer with bulk download
 *   • Structured autopilot progress log
 * ============================================================================= */

"use strict";

/* ------------------------------------------------------------------ */
/* Core state & DOM helpers                                            */
/* ------------------------------------------------------------------ */

const S = {
  pid: null,
  p: null,
  models: {},
  selectedModels: {},
  lastStage: null,
  busy: false,
};

const $ = id => document.getElementById(id);
const show = id => $(id)?.classList.remove("hidden");
const hide = id => $(id)?.classList.add("hidden");

/** Escape HTML to prevent XSS */
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

/** Fetch wrapper with timeout, JSON parse, and error normalisation */
async function api(path, opt = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opt.timeout || 300_000);
  try {
    const r = await fetch(path, {
      ...opt,
      signal: controller.signal,
      headers:
        opt.body instanceof FormData
          ? opt.headers || {}
          : { "Content-Type": "application/json", ...(opt.headers || {}) },
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw Error(d.error || `Request failed (${r.status})`);
    return d;
  } finally {
    clearTimeout(timer);
  }
}

/* ------------------------------------------------------------------ */
/* Pipeline step tracker (7 sequenced stages)                          */
/* ------------------------------------------------------------------ */

const STEP_DEFS = [
  { key: "created",         label: "Brief",             sub: "URL · upload · mood" },
  { key: "scraped",         label: "Ingest",            sub: "HTML · images · videos · frames" },
  { key: "prompts_generated",label:"Analyze & Prompt",  sub: "Visual analysis → prompts" },
  { key: "reviewed",        label: "Review",            sub: "Human edits & feedback" },
  { key: "verified",        label: "Verify & Generate", sub: "Best-model routing" },
  { key: "generated",       label: "Deliver",           sub: "Outputs ready" },
  { key: "final_reviewed",  label: "Final Review",      sub: "Approve · memory · refine" },
];

const STAGE_STEP = {
  created: 0,
  scraping: 1, scraped: 1,
  analyzing: 2, prompting: 2, prompts_generated: 2,
  reviewed: 3,
  verified: 4, generating: 4, generation_failed: 4,
  generated: 5,
  final_reviewed: 6,
};

const GEN_SUBSTEPS = [
  { match: ["scraping", "scraped"],                              label: "Reference ingestion (HTML / images / videos / frames)" },
  { match: ["analyzing", "prompting", "prompts_generated"],     label: "Visual + metadata analysis → prompt engineering" },
  { match: ["reviewed", "verified"],                             label: "Human review & verification gate" },
  { match: ["generating", "generated", "generation_failed"],    label: "Best-model generation (parallel creatives)" },
  { match: ["final_reviewed"],                                   label: "Final review → memory / refine loop" },
];

function stageIndex(stage) {
  return stage in STAGE_STEP ? STAGE_STEP[stage] : 0;
}

function steps(stage) {
  const n = stageIndex(stage);
  const failed = stage === "generation_failed";
  $("steps").innerHTML = STEP_DEFS.map((s, i) => {
    const done   = i < n || (stage === "final_reviewed" && i <= n);
    const active = i === n && stage !== "final_reviewed";
    const err    = active && failed;
    return `<div class="step ${done ? "done" : ""} ${active ? "active" : ""} ${err ? "err" : ""}">
      <i>${done && !active ? "✓" : i + 1}</i>
      <span>${esc(s.label)}<small>${esc(s.sub)}</small></span>
    </div>`;
  }).join("");

  const pct   = stage === "final_reviewed" ? 100 : Math.round(((n + 1) / STEP_DEFS.length) * 100);
  const fill  = $("pipelineFill");
  const pctEl = $("pipelinePct");
  if (fill)  fill.style.width  = pct + "%";
  if (pctEl) pctEl.textContent = pct + "% complete" + (failed ? " · failed" : "");
}

function renderGenSteps(stage) {
  const box = $("genSteps");
  if (!box) return;
  const idx = GEN_SUBSTEPS.findIndex(g => g.match.includes(stage));
  box.innerHTML = GEN_SUBSTEPS.map((g, i) => {
    const state = i < idx ? "done" : i === idx ? "active" : "";
    return `<div class="gen-step ${state}"><i>${i < idx ? "✓" : i + 1}</i>${esc(g.label)}</div>`;
  }).join("");
  const fill = $("genFill");
  if (fill) {
    const pct = stage === "generated" || stage === "final_reviewed" ? 100
      : stage === "generation_failed" ? Math.max(20, ((idx + 1) / GEN_SUBSTEPS.length) * 100)
      : Math.round(((idx + 1) / GEN_SUBSTEPS.length) * 100);
    fill.style.width = Math.min(100, pct) + "%";
    fill.classList.toggle("failed", stage === "generation_failed");
  }
}

function renderActivity(p) {
  const box = $("activityLog");
  if (!box) return;
  const hist = (p.history || []).slice(-24).reverse();
  if (!hist.length) {
    box.innerHTML = '<span class="muted">No activity yet — start a campaign to track each stage.</span>';
    return;
  }
  box.innerHTML = hist.map(h => {
    const t  = new Date((h.timestamp || 0) * 1000);
    const hh = String(t.getHours()).padStart(2, "0");
    const mm = String(t.getMinutes()).padStart(2, "0");
    const ss = String(t.getSeconds()).padStart(2, "0");
    return `<div class="log-line"><span class="log-time">${hh}:${mm}:${ss}</span><span>${esc(h.message)}</span></div>`;
  }).join("");
}

/* ------------------------------------------------------------------ */
/* Form helpers                                                        */
/* ------------------------------------------------------------------ */

function selectedTypes() {
  return [...document.querySelectorAll("#typeChecks input:checked")].map(x => x.value);
}

function selectedModel(type) {
  return S.selectedModels[type] || $(`model_${type}`)?.value || null;
}

function selectedModelsMap() {
  const types = selectedTypes().length ? selectedTypes() : ["copy", "image", "video"];
  return Object.fromEntries(types.map(c => [c, selectedModel(c)]));
}

function currentSettings() {
  const mood = $("mood").value;
  return {
    mood,
    copy:  { mood },
    image: { image_size: $("imageSize").value, mood },
    video: {
      duration:     Number($("videoDuration").value || 6),
      aspect_ratio: $("videoRatio").value,
      resolution:   $("videoResolution").value,
      mood,
    },
  };
}

function refreshDuration() {
  const model     = S.models.video?.find(m => m.id === selectedModel("video"));
  const durations = model?.durations || [4, 6, 8];
  const sel = $("videoDuration");
  if (!sel) return;
  const old = Number(sel.value);
  sel.innerHTML = durations.map(x => `<option value="${x}">${x} seconds</option>`).join("");
  if      (durations.includes(old)) sel.value = String(old);
  else if (durations.includes(6))   sel.value = "6";
  else                              sel.value = String(durations[0]);
  const note = model?.note || "";
  const hint = $("durationHint");
  if (hint) {
    hint.textContent = model
      ? `${model.label} supports: ${durations.join(", ")}s${note ? " — " + note : ""}`
      : "Select a video model to see supported durations.";
    hint.classList.toggle("hint-adjusted", Boolean(old && !durations.includes(old)));
    if (old && !durations.includes(old)) hint.textContent += ` (adjusted from ${old}s)`;
  }
}

function renderModelControls() {
  const types = ["copy", "image", "video"];
  const box   = $("modelControls");
  if (!box) return;
  box.innerHTML = types.map(type => {
    const list        = S.models[type] || [];
    const usableCount = list.filter(m => m.usable).length;
    const totalCount  = list.length;
    const firstUsable = list.find(m => m.usable);
    const savedModel  = S.selectedModels[type];
    const currentVal  = savedModel && list.find(m => m.id === savedModel && m.usable)
      ? savedModel
      : (firstUsable ? firstUsable.id : "");
    S.selectedModels[type] = currentVal;
    return `
    <div class="model-card ${usableCount ? "ready" : "missing"}" data-type="${type}">
      <div class="model-icon">${type === "copy" ? "Aa" : type === "image" ? "◆" : "▶"}</div>
      <div class="model-info">
        <b>${type.toUpperCase()}</b>
        <span>${usableCount
          ? usableCount + " of " + totalCount + " engine" + (totalCount > 1 ? "s" : "") + " available"
          : "Configure API keys in .env to unlock"}</span>
      </div>
      <div class="model-select-wrap">
        <select id="model_${type}" ${totalCount ? "" : "disabled"}>
          ${list.map(m =>
            `<option value="${esc(m.id)}" ${m.usable ? "" : "disabled"} ${m.id === currentVal ? "selected" : ""}>${esc(m.label)}${
              m.usable ? "" : " — " + (m.unavailable_reason || "unavailable")
            }</option>`
          ).join("")}
        </select>
      </div>
    </div>`;
  }).join("");

  types.forEach(type => {
    const sel = $(`model_${type}`);
    if (!sel) return;
    sel.addEventListener("change", function () {
      S.selectedModels[type] = this.value;
      if (type === "video") refreshDuration();
    });
  });
  refreshDuration();
}

async function loadModels() {
  S.models = await api("/api/models");
  renderModelControls();
}

async function saveProviderKeys() {
  const button = $("saveProviderKeys");
  const status = $("providerSettingsStatus");
  if (!button || !status) return;
  button.disabled = true;
  status.textContent = "Saving securely for this server session…";
  try {
    const result = await api("/api/settings/providers", {
      method: "POST",
      body: JSON.stringify({
        providers: {
          openrouter: $("openrouterKey").value,
          agnes:      $("agnesKey").value,
        },
      }),
    });
    $("openrouterKey").value = "";
    $("agnesKey").value      = "";
    const configured = result.providers || {};
    status.textContent = `Saved. OpenRouter ${configured.openrouter ? "ready" : "off"} · Agnes ${configured.agnes ? "ready" : "off"}`;
    await loadModels();
    const h = await api("/api/health");
    updateProviderStatus(h.providers || {});
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function updateProviderStatus(providers) {
  $("providerStatus").innerHTML = Object.entries(providers).map(([k, v]) =>
    `<span class="provider-pill ${v.configured ? "on" : "off"}">${
      k === "openrouter" ? "OpenRouter" : "Agnes"
    } ${v.configured ? "●" : "○"}</span>`
  ).join("");
}

/* ------------------------------------------------------------------ */
/* Rendering project state                                             */
/* ------------------------------------------------------------------ */

async function copyTextToClipboard(text, btnId) {
  try {
    await navigator.clipboard.writeText(text);
    const b = $(btnId);
    if (b) {
      const o = b.innerHTML;
      b.innerHTML = "✓ Copied";
      setTimeout(() => (b.innerHTML = o), 1600);
    }
  } catch (e) {
    alert("Could not copy to clipboard.");
  }
}

function assetThumb(p, x) {
  if (x.type === "image" && x.path) {
    const fn = String(x.path).replaceAll("\\", "/").split("/").pop();
    return `<img loading="lazy" src="/reference/${p.id}/${encodeURIComponent(fn)}" alt="Reference image">`;
  }
  if (x.type === "video" && x.frame_paths && x.frame_paths.length) {
    const fn = String(x.frame_paths[0]).replaceAll("\\", "/").split("/").pop();
    return `<img loading="lazy" src="/reference/${p.id}/${encodeURIComponent(fn)}" alt="Video frame">`;
  }
  return `<div class="video-tile">${x.type === "video" ? "VIDEO" : "ASSET"}</div>`;
}

function renderReference(p) {
  show("referenceCard");
  const a      = p.scraped.assets || [];
  const imgs   = a.filter(x => x.type === "image" && !x.error);
  const vids   = a.filter(x => x.type === "video" && !x.error);
  const frames = a.reduce((n, x) => n + ((x.frame_paths || []).length), 0);
  $("reference").innerHTML = `
    <div class="reference">
      <div class="stat"><b>${esc(p.scraped.title || "Brand intelligence")}</b><br><span class="muted">${esc(
        (p.scraped.meta_description || p.scraped.text || "").slice(0, 180)
      )}${(p.scraped.text || "").length > 180 ? "…" : ""}</span></div>
      <div class="stat"><b>${imgs.length}</b><br>Images ingested</div>
      <div class="stat"><b>${vids.length}</b><br>Videos · ${frames} frames</div>
    </div>
    <div class="asset-grid">${a.filter(x => !x.error).map(x =>
      `<div class="asset">${assetThumb(p, x)}<small>${x.type}${
        x.signals?.width ? ` · ${x.signals.width}×${x.signals.height}` : ""
      }</small></div>`
    ).join("") || '<span class="muted">No downloadable assets — brief-only analysis will be used.</span>'}</div>`;

  const errBox = $("scrapeError");
  if (errBox) {
    if (p.scraped.error) {
      errBox.textContent = "Scrape note: " + p.scraped.error;
      errBox.classList.remove("hidden");
    } else {
      errBox.classList.add("hidden");
    }
  }
}

function renderPrompts(p) {
  if (!p.reviewed_prompts || !Object.keys(p.reviewed_prompts).length) return;
  show("promptCard");
  $("prompts").innerHTML = Object.entries(p.reviewed_prompts).map(([c, v]) =>
    `<div class="prompt">
      <h4>${c.toUpperCase()} DIRECTIVE</h4>
      <textarea id="p_${c}" rows="8">${esc(v)}</textarea>
      <label>Reviewer feedback <span>folds into the prompt on save</span></label>
      <textarea id="f_${c}" rows="2" placeholder="e.g. warmer palette, tighter headline, 9:16 safe area…">${esc(
        p.review_feedback?.[c] || ""
      )}</textarea>
    </div>`
  ).join("");
}

/* ------------------------------------------------------------------ */
/* Quality score badge                                                 */
/* ------------------------------------------------------------------ */

/**
 * Returns an HTML string for a quality badge.
 * @param {number} score  0–100
 * @returns {string}
 */
function qualityBadge(score) {
  if (score == null || score === undefined) return "";
  const n   = Number(score);
  const cls = n >= 80 ? "excellent" : n >= 60 ? "good" : "low";
  const lbl = n >= 80 ? "Excellent" : n >= 60 ? "Good"      : "Low";
  return `<span class="quality-badge ${cls}" title="Quality score: ${n}/100">${lbl} ${n}</span>`;
}

/* ------------------------------------------------------------------ */
/* Platform copy variant tabs                                          */
/* ------------------------------------------------------------------ */

/**
 * Renders tabbed copy variants if result has `platform_variants`.
 * @param {object} copyResult  Output object for creative type "copy"
 * @param {string} containerId  Target element ID
 */
function renderCopyVariants(copyResult, containerId = "copyVariantBox") {
  const box = $(containerId);
  if (!box) return;
  const variants = copyResult?.platform_variants;
  if (!variants || !Object.keys(variants).length) {
    box.innerHTML = "";
    return;
  }
  const platformLabels = {
    instagram_feed:  "Instagram",
    instagram_story: "IG Story",
    facebook_feed:   "Facebook",
    linkedin:        "LinkedIn",
    tiktok:          "TikTok",
    twitter_x:       "Twitter/X",
    google_display:  "Google Ads",
  };
  const keys      = Object.keys(variants);
  const firstKey  = keys[0];
  const tabsHtml  = keys.map(k =>
    `<button class="variant-tab${k === firstKey ? " active" : ""}" data-key="${esc(k)}">${esc(platformLabels[k] || k)}</button>`
  ).join("");
  const panelsHtml = keys.map(k =>
    `<pre class="copy-content${k === firstKey ? "" : " hidden"}" data-key="${esc(k)}">${esc(variants[k])}</pre>`
  ).join("");

  box.innerHTML = `
    <div class="variant-tabs" role="tablist">${tabsHtml}</div>
    <div class="variant-panels">${panelsHtml}</div>`;

  box.querySelectorAll(".variant-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key;
      box.querySelectorAll(".variant-tab").forEach(b => b.classList.toggle("active", b.dataset.key === key));
      box.querySelectorAll(".copy-content").forEach(p => p.classList.toggle("hidden", p.dataset.key !== key));
    });
  });
}

/* ------------------------------------------------------------------ */
/* Enhanced output rendering                                           */
/* ------------------------------------------------------------------ */

function renderOutputs(p) {
  show("outputCard");
  const outputs = p.outputs || {};
  $("outputs").innerHTML = Object.entries(outputs).map(([c, o]) => {
    let media = "", action = "";

    if (c === "image") {
      media  = `<img class="preview" src="${o.url}" alt="Generated campaign image">`;
      action = `<a class="btn-action" href="${o.url}" download="${o.filename}">↓ Download image</a>`;
    } else if (c === "video") {
      media  = `<video class="preview" controls autoplay muted loop src="${o.url}"></video>`;
      action = `<a class="btn-action" href="${o.url}" download="${o.filename}">↓ Download video</a>`;
    } else {
      /* copy — render text + optional platform variant tabs */
      media  = `<div class="copy-box" id="copyText_${c}">Loading campaign copy…</div>
                <div id="copyVariantBox_${c}"></div>`;
      action = `<button class="btn-action" id="copyBtn_${c}">Copy</button><a class="btn-action" href="${o.url}" download="${o.filename}">View raw</a>`;
      fetch(o.url)
        .then(r => r.text())
        .then(t => {
          const b = $(`copyText_${c}`);
          if (b) b.textContent = t;
          const btn = $(`copyBtn_${c}`);
          if (btn) btn.onclick = () => copyTextToClipboard(t, `copyBtn_${c}`);
        })
        .catch(() => {});
      if (o.platform_variants) {
        /* Defer so DOM is present */
        setTimeout(() => renderCopyVariants(o, `copyVariantBox_${c}`), 50);
      }
    }

    const prior      = p.final_review?.[c] || {};
    const qBadge     = qualityBadge(o.quality_score);
    const modelLabel = o.model_used
      ? `<span class="output-model">${esc(o.model_used)}</span>` : "";
    const retryInfo  = (o.retry_count && o.retry_count > 1)
      ? `<span class="output-retry">↺ ${o.retry_count} attempts</span>` : "";
    const platTags   = (o.platforms || []).map(pl =>
      `<span class="platform-tag">${esc(pl.replace(/_/g, " "))}</span>`).join("");

    return `<div class="output">
      <h4>
        <span>${c.toUpperCase()} DELIVERABLE</span>
        <span class="output-meta">${modelLabel}${retryInfo}${qBadge}</span>
      </h4>
      ${platTags ? `<div class="platform-tags">${platTags}</div>` : ""}
      ${media}
      <div class="output-actions">${action}</div>
      ${o.warning  ? `<p class="warn">Provider note: ${esc(o.warning)}</p>` : ""}
      ${o.duration ? `<div class="meta-line">Generated duration: ${o.duration}s</div>` : ""}
      <div class="review-row">
        <label>Decision
          <select id="d_${c}">
            <option value="true"  ${prior.approved !== false ? "selected" : ""}>✓ Approved</option>
            <option value="false" ${prior.approved === false ? "selected" : ""}>✗ Needs correction</option>
          </select>
        </label>
        <label>Feedback / correction notes<textarea id="n_${c}" rows="2" placeholder="Specific correction or creative direction…">${esc(prior.notes || "")}</textarea></label>
      </div>
      <label>Rating (1–5)<input id="r_${c}" type="number" min="1" max="5" value="${prior.rating || 5}" style="width:80px"></label>
    </div>`;
  }).join("");
}

function render(p) {
  S.p = p;
  steps(p.stage);
  renderActivity(p);
  if (p.stage) S.lastStage = p.stage;

  if (p.scraped) renderReference(p);

  if (p.reviewed_prompts && Object.keys(p.reviewed_prompts).length) {
    renderPrompts(p);
  }

  if (Object.keys(p.chosen_models || {}).length) {
    show("modelCard");
    $("models").innerHTML = Object.entries(p.chosen_models).map(([c, m]) =>
      `<div class="model-card ready"><div class="model-icon">${
        c === "copy" ? "Aa" : c === "image" ? "◆" : "▶"
      }</div><div class="model-info"><b>${c.toUpperCase()}</b><span>${esc(m.label || m.id)}</span></div></div>`
    ).join("");
  }

  if (
    p.verified || p.stage === "generating" || p.stage === "generated" ||
    p.stage === "final_reviewed" || p.stage === "generation_failed"
  ) {
    if (Object.keys(p.outputs || {}).length || p.stage === "generating" || p.stage === "generation_failed") {
      show("verifyCard");
      renderGenSteps(p.stage);
    }
  }

  if (Object.keys(p.outputs || {}).length) {
    show("modelCard");
    renderOutputs(p);
  }

  /* Campaign bundle (Autopilot result) */
  if (p.campaign_bundle) {
    renderCampaignBundle(p.campaign_bundle);
  }
}

async function refreshMemory() {
  try {
    const d = await api("/api/learning");
    const s = d.stats || {};
    $("memory").innerHTML = `<b>${s.reviews || 0}</b> reviews<br><b>${s.approved || 0}</b> approved · <b>${
      s.rejected || 0
    }</b> refined`;
  } catch (e) {
    $("memory").textContent = "Memory active.";
  }
}

/* ------------------------------------------------------------------ */
/* Generation wait (Socket.IO + polling fallback)                      */
/* ------------------------------------------------------------------ */

async function waitForGeneration() {
  show("verifyCard");
  for (;;) {
    await new Promise(r => setTimeout(r, 2000));
    const p = await api("/api/projects/" + S.pid);
    render(p);
    if (["generated", "final_reviewed"].includes(p.stage)) {
      $("genStatus").textContent = "✓ Generation completed successfully.";
      $("status").className      = "status active";
      $("status").textContent    = "Campaign generated — review your deliverables below.";
      return p;
    }
    if (p.stage === "generation_failed") {
      throw Error(p.generation_error || "Generation failed.");
    }
  }
}

function setLoading(btn, loading) {
  if (!btn) return;
  if (loading) {
    btn.dataset.origText = btn.innerHTML;
    btn.disabled         = true;
    btn.classList.add("loading");
    S.busy = true;
  } else {
    btn.disabled = false;
    btn.classList.remove("loading");
    if (btn.dataset.origText) btn.innerHTML = btn.dataset.origText;
    S.busy = false;
  }
}

function setStatus(msg, kind = "active") {
  const el = $("status");
  el.className      = "status " + kind;
  el.textContent    = msg;
  el.classList.remove("hidden");
}

/* ------------------------------------------------------------------ */
/* Actions (7-step manual pipeline)                                    */
/* ------------------------------------------------------------------ */

async function createProject() {
  const types = selectedTypes();
  if (!types.length) throw Error("Tick at least one creative type (Copy / Image / Video).");
  const brief = $("brief").value.trim();
  if (!brief)  throw Error("Please provide a campaign brief.");
  return await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({
      url:               $("url").value.trim(),
      brief,
      creative_types:    types,
      creative_settings: currentSettings(),
    }),
  });
}

async function uploadReferences(p) {
  const files = [...($("files")?.files || [])];
  let n = 0;
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch("/api/projects/" + p.id + "/upload-reference", { method: "POST", body: fd });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw Error(d.error || `Upload failed: ${f.name}`);
    p = d;
    n++;
    if ($("uploadStatus")) $("uploadStatus").textContent = `Uploaded ${n}/${files.length}: ${f.name}`;
  }
  if (n && $("uploadStatus")) $("uploadStatus").textContent = `✓ ${n} reference file(s) uploaded.`;
  return p;
}

/* Full campaign: create → upload → auto-run (ingest → analyze → generate) */
$("autoLaunchBtn").onclick = async function () {
  const btn = this;
  if (S.busy) return;
  try {
    setLoading(btn, true);
    setStatus("Initializing creative workspace…");
    let p = await createProject();
    S.pid = p.id;
    render(p);
    p = await uploadReferences(p);
    render(p);
    joinProjectRoom(S.pid);
    setStatus("Launching sequenced pipeline: ingest → analyze → prompt → verify → generate…");
    p = await api("/api/projects/" + S.pid + "/auto-run", {
      method: "POST",
      body: JSON.stringify({
        models:            selectedModelsMap(),
        creative_settings: currentSettings(),
      }),
    });
    render(p);
    p = await waitForGeneration();
    $("outputCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    setStatus("Error: " + e.message, "warn");
    if (S.pid) {
      try { render(await api("/api/projects/" + S.pid)); } catch (_) {}
    }
    alert(e.message);
  } finally {
    setLoading(btn, false);
  }
};

/* Step-by-step: create → upload → ingest (pause for inspection) */
$("create").onclick = async function () {
  const btn = this;
  if (S.busy) return;
  try {
    setLoading(btn, true);
    setStatus("Extracting reference intelligence (HTML / images / videos / frames)…");
    let p = await createProject();
    S.pid = p.id;
    p     = await api("/api/projects/" + S.pid + "/scrape", { method: "POST" });
    p     = await uploadReferences(p);
    render(p);
    /* After scrape: fetch & render Brand DNA */
    fetchBrandDna(S.pid);
    setStatus("✓ Step 2 complete — references ingested. Continue to analyze & prompt.");
    $("referenceCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    setStatus(e.message, "warn");
  } finally {
    setLoading(btn, false);
  }
};

$("promptBtn").onclick = async function () {
  const btn = this;
  if (S.busy || !S.pid) return;
  try {
    setLoading(btn, true);
    setStatus("Analyzing visuals + metadata and engineering prompts…");
    const p = await api("/api/projects/" + S.pid + "/prompts", {
      method: "POST",
      body: JSON.stringify({ creative_settings: currentSettings() }),
    });
    render(p);
    setStatus("✓ Steps 3–4 — prompts ready. Review, then Verify & Generate.");
    $("promptCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    alert(e.message);
    setStatus(e.message, "warn");
  } finally {
    setLoading(btn, false);
  }
};

$("reviewBtn").onclick = async function () {
  const btn = this;
  if (S.busy || !S.pid) return;
  try {
    setLoading(btn, true);
    const edits = {}, feedback = {};
    for (const c of S.p.creative_types) {
      edits[c]    = $("p_" + c)?.value || "";
      feedback[c] = $("f_" + c)?.value || "";
    }
    const p = await api("/api/projects/" + S.pid + "/review", {
      method: "POST",
      body: JSON.stringify({ edited_prompts: edits, feedback }),
    });
    render(p);
    setStatus("✓ Step 4 — review saved & prompts refined. Verify when ready.");
  } catch (e) {
    alert(e.message);
  } finally {
    setLoading(btn, false);
  }
};

$("verifyBtn").onclick = async function () {
  const btn = this;
  if (S.busy || !S.pid) return;
  try {
    setLoading(btn, true);
    $("genStatus").textContent = "Verifying prompts and starting generation with your selected models…";
    setStatus("Steps 5–6 — verifying & generating…");
    joinProjectRoom(S.pid);
    const p = await api("/api/projects/" + S.pid + "/verify", {
      method: "POST",
      body: JSON.stringify({
        models:            selectedModelsMap(),
        creative_settings: currentSettings(),
      }),
    });
    render(p);
    await waitForGeneration();
    $("outputCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("genStatus").textContent = "Generation failed: " + e.message;
    setStatus("Generation failed: " + e.message, "warn");
    try { if (S.pid) render(await api("/api/projects/" + S.pid)); } catch (_) {}
  } finally {
    setLoading(btn, false);
  }
};

$("finalBtn").onclick = async function () {
  const btn = this;
  if (S.busy || !S.pid) return;
  try {
    setLoading(btn, true);
    const decisions = {};
    for (const c of Object.keys(S.p.outputs || {})) {
      decisions[c] = {
        approved: $("d_" + c).value === "true",
        notes:    $("n_" + c).value,
        rating:   Number($("r_" + c).value),
      };
    }
    const rejected = Object.values(decisions).filter(d => !d.approved).length;
    setStatus(rejected
      ? `${rejected} correction(s) requested — refining prompts and regenerating…`
      : "Saving approval to creative memory…");
    joinProjectRoom(S.pid);
    let p = await api("/api/projects/" + S.pid + "/final-review", {
      method: "POST",
      body: JSON.stringify({ decisions }),
    });
    render(p);
    if (p.stage === "generating") {
      p = await waitForGeneration();
      render(p);
      setStatus("✓ Corrections applied — updated deliverables ready for another review.");
    } else {
      setStatus("✓ Step 7 — review saved. Agent has learned from your feedback.");
    }
    await refreshMemory();
  } catch (e) {
    alert(e.message);
    setStatus(e.message, "warn");
  } finally {
    setLoading(btn, false);
  }
};

$("newBtn").onclick = () => location.reload();

$("mood")?.addEventListener("change", function () {
  const hint = $("moodHint");
  if (hint) hint.textContent = `Mood "${this.value}" will be folded into copy tone, image style and video look.`;
});

$("typeChecks")?.addEventListener("change", () => {
  const types = selectedTypes();
  document.querySelectorAll(".model-card[data-type]").forEach(card => {
    card.classList.toggle("muted-card", !types.includes(card.dataset.type));
  });
});

/* ------------------------------------------------------------------ */
/* Autopilot Engine                                                    */
/* ------------------------------------------------------------------ */

/** Supported output platforms */
const PLATFORMS = [
  { id: "instagram_feed",  label: "IG Feed",    icon: "◼" },
  { id: "instagram_story", label: "IG Story",   icon: "◼" },
  { id: "facebook_feed",   label: "Facebook",   icon: "◼" },
  { id: "linkedin",        label: "LinkedIn",   icon: "◼" },
  { id: "tiktok",          label: "TikTok",     icon: "◼" },
  { id: "twitter_x",       label: "Twitter/X",  icon: "◼" },
  { id: "google_display",  label: "Google",     icon: "◼" },
];

/** Autopilot runtime state */
const AP = {
  running:           false,
  selectedPlatforms: ["instagram_feed", "facebook_feed", "linkedin"],
  jobId:             null,
  log:               [],
  startTime:         null,
};

/* ---- Platform chip rendering ---- */

/**
 * Renders clickable platform chips inside #platformChips.
 * Each chip toggles AP.selectedPlatforms.
 */
function renderPlatformChips() {
  const box = $("platformChips");
  if (!box) return;
  box.innerHTML = PLATFORMS.map(pl => {
    const active = AP.selectedPlatforms.includes(pl.id);
    return `<button
      class="platform-chip${active ? " active" : ""}"
      data-platform="${esc(pl.id)}"
      aria-pressed="${active}"
      title="Toggle ${esc(pl.label)}"
    >${pl.icon} ${esc(pl.label)}</button>`;
  }).join("");

  box.querySelectorAll(".platform-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      const pid = btn.dataset.platform;
      const idx = AP.selectedPlatforms.indexOf(pid);
      if (idx === -1) {
        AP.selectedPlatforms.push(pid);
        btn.classList.add("active");
        btn.setAttribute("aria-pressed", "true");
      } else {
        AP.selectedPlatforms.splice(idx, 1);
        btn.classList.remove("active");
        btn.setAttribute("aria-pressed", "false");
      }
    });
  });
}

/* ---- Autopilot log ---- */

/**
 * Appends a timestamped line to #autopilotLog.
 * @param {string} message
 * @param {'info'|'ok'|'warn'|'err'} type
 */
function apLog(message, type = "info") {
  const box = $("autopilotLog");
  if (!box) return;
  const now  = new Date();
  const ts   = `${String(now.getHours()).padStart(2,"0")}:${String(now.getMinutes()).padStart(2,"0")}:${String(now.getSeconds()).padStart(2,"0")}`;
  const line = { ts, message, type };
  AP.log.push(line);
  if (AP.log.length > 50) AP.log.shift();

  const colorMap = { info: "var(--ap-cyan,#00d4ff)", ok: "var(--ap-green,#00e676)", warn: "var(--ap-yellow,#ffcc02)", err: "var(--ap-red,#ff5252)" };
  const color    = colorMap[type] || colorMap.info;
  const el       = document.createElement("div");
  el.className   = `ap-log-line ap-log-${type}`;
  el.innerHTML   = `<span class="ap-log-time">${ts}</span><span style="color:${color}">${esc(message)}</span>`;
  box.appendChild(el);

  /* Keep only last 50 DOM nodes */
  while (box.children.length > 50) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

/* ---- Autopilot progress tracker ---- */

/**
 * Updates the autopilot progress bar, step label, ETA, and log.
 * @param {{ step: string, message: string, percent: number, type: string, eta_seconds?: number }} data
 */
function updateAutopilotProgress(data) {
  const label   = $("autopilotStepLabel");
  const bar     = $("autopilotFill");
  const etaEl   = $("autopilotEta");
  const pctEl   = $("autopilotPct");
  const spinner = AP.running ? ' <span class="ap-spinner">⟳</span>' : "";

  if (label) label.innerHTML = esc(data.step || "Running…") + spinner;
  if (bar)   bar.style.width = Math.min(100, data.percent || 0) + "%";
  if (pctEl) pctEl.textContent = `${Math.round(data.percent || 0)}%`;

  if (etaEl && data.eta_seconds != null) {
    const secs = Math.round(data.eta_seconds);
    etaEl.textContent = secs > 60
      ? `ETA ~${Math.round(secs / 60)}m`
      : `ETA ~${secs}s`;
  } else if (etaEl) {
    etaEl.textContent = "";
  }

  apLog(data.message || data.step || "Progress update", data.type || "info");
}

/* ---- Autopilot launch ---- */

/**
 * Launches the Autopilot multi-platform campaign pipeline.
 */
async function launchAutopilot() {
  const btn = $("autopilotBtn");
  if (AP.running) return;

  const brief = $("brief")?.value?.trim();
  if (!brief) {
    alert("Please enter a campaign brief before launching Autopilot.");
    return;
  }
  if (!AP.selectedPlatforms.length) {
    alert("Select at least one platform.");
    return;
  }

  AP.running   = true;
  AP.startTime = Date.now();
  AP.log       = [];

  setLoading(btn, true);
  show("autopilotProgress");
  apLog("Autopilot launched — preparing multi-platform campaign…", "info");
  updateAutopilotProgress({ step: "Initialising", message: "Starting Autopilot engine…", percent: 2 });

  try {
    if (!S.pid) {
      apLog("Auto-creating campaign workspace…", "info");
      let p = await createProject();
      S.pid = p.id;
      p = await uploadReferences(p);
      render(p);
      joinProjectRoom(S.pid);
    }
    const payload = {
      project_id:      S.pid,
      creative_types:  selectedTypes().length ? selectedTypes() : ["copy", "image", "video"],
      platforms:       AP.selectedPlatforms,
      models:          selectedModelsMap(),
      creative_settings: currentSettings(),
    };

    const result = await api("/api/autopilot/launch", {
      method:  "POST",
      body:    JSON.stringify(payload),
      timeout: 600_000,  /* 10-minute timeout for full pipeline */
    });

    AP.jobId = result.job_id || null;
    apLog(`Job queued — ID: ${AP.jobId || "pending"}`, "ok");
    updateAutopilotProgress({ step: "Pipeline queued", message: "Server accepted the job. Waiting for progress…", percent: 5 });

  } catch (e) {
    AP.running = false;
    apLog("Launch failed: " + e.message, "err");
    updateAutopilotProgress({ step: "Error", message: e.message, percent: 0, type: "err" });
    setLoading(btn, false);
    setStatus("Autopilot error: " + e.message, "warn");
  }
}

/* ---- Brand DNA ---- */

/**
 * Fetches Brand DNA from the API for a given project.
 * @param {string} pid  Project ID
 */
async function fetchBrandDna(pid) {
  if (!pid) return;
  try {
    const dna = await api(`/api/brand/${pid}/dna`);
    renderBrandDna(dna);
  } catch (e) {
    /* Brand DNA is optional — silently ignore */
  }
}

/**
 * Renders brand DNA data into #brandDnaContent.
 * @param {object} dna
 */
function renderBrandDna(dna) {
  const box = $("brandDnaContent");
  if (!box || !dna) return;
  show("brandDnaCard");

  /* Palette swatches */
  const palette = (dna.palette || []).map(color =>
    `<span class="swatch" style="background:${esc(color)}" title="${esc(color)}"></span>`
  ).join("");

  /* Tone chips */
  const toneChips = (dna.tone || []).map(word =>
    `<span class="tone-chip">${esc(word)}</span>`
  ).join("");

  /* Do / Don't rules */
  const doRules   = (dna.rules?.do   || []).map(r => `<li class="rule-do">✓ ${esc(r)}</li>`).join("");
  const dontRules = (dna.rules?.dont || []).map(r => `<li class="rule-dont">✗ ${esc(r)}</li>`).join("");

  box.innerHTML = `
    <div class="dna-section">
      <h5>Brand Palette</h5>
      <div class="palette-row">${palette || '<span class="muted">No palette extracted</span>'}</div>
    </div>
    <div class="dna-section">
      <h5>Tone of Voice</h5>
      <div class="tone-chips">${toneChips || '<span class="muted">No tone words extracted</span>'}</div>
    </div>
    ${dna.composition ? `<div class="dna-section"><h5>Composition</h5><p>${esc(dna.composition)}</p></div>` : ""}
    ${dna.lighting    ? `<div class="dna-section"><h5>Lighting Style</h5><p>${esc(dna.lighting)}</p></div>`    : ""}
    ${doRules || dontRules ? `
    <div class="dna-section">
      <h5>Brand Rules</h5>
      <ul class="rule-list">${doRules}${dontRules}</ul>
    </div>` : ""}`;
}

/* ---- Campaign bundle renderer ---- */

/**
 * Renders the full Autopilot campaign bundle in #campaignBundle.
 * @param {object} bundle  Server-returned bundle object
 */
function renderCampaignBundle(bundle) {
  const box = $("campaignBundle");
  if (!box || !bundle) return;
  show("campaignBundleCard");

  const assets   = bundle.assets || [];
  const total    = assets.length;
  const avgScore = total
    ? Math.round(assets.reduce((s, a) => s + (a.quality_score || 0), 0) / total)
    : 0;
  const platforms = [...new Set(assets.flatMap(a => a.platforms || []))];

  /* Summary stats bar */
  const statsHtml = `
    <div class="bundle-stats">
      <div class="bundle-stat"><span>${total}</span><small>Total Assets</small></div>
      <div class="bundle-stat"><span>${qualityBadge(avgScore) || avgScore}</span><small>Avg Quality</small></div>
      <div class="bundle-stat"><span>${platforms.length}</span><small>Platforms</small></div>
    </div>`;

  /* Asset grid */
  const gridHtml = assets.map(asset => {
    const platTags = (asset.platforms || []).map(pl =>
      `<span class="platform-tag">${esc(pl.replace(/_/g, " "))}</span>`).join("");
    const thumb = asset.type === "image"
      ? `<img class="bundle-thumb" src="${esc(asset.url || "")}" alt="${esc(asset.filename || "asset")}" loading="lazy">`
      : asset.type === "video"
      ? `<video class="bundle-thumb" src="${esc(asset.url || "")}" muted loop playsinline></video>`
      : `<div class="bundle-copy-thumb"><span>${esc((asset.preview || "Copy asset").slice(0, 80))}</span></div>`;

    return `
      <div class="bundle-card">
        <div class="bundle-thumb-wrap">${thumb}</div>
        <div class="bundle-card-info">
          <div class="bundle-card-meta">
            <span class="bundle-type-badge">${esc(asset.type || "asset")}</span>
            ${qualityBadge(asset.quality_score)}
          </div>
          ${platTags ? `<div class="platform-tags">${platTags}</div>` : ""}
          ${asset.model_used ? `<div class="bundle-model">${esc(asset.model_used)}</div>` : ""}
          <div class="bundle-actions">
            ${asset.url ? `<a class="btn-action btn-sm" href="${esc(asset.url)}" download="${esc(asset.filename || "asset")}">↓</a>` : ""}
          </div>
        </div>
      </div>`;
  }).join("");

  box.innerHTML = `
    ${statsHtml}
    <div class="bundle-grid">${gridHtml || '<span class="muted">No assets in this bundle.</span>'}</div>
    <div class="bundle-footer">
      <button class="btn-action" id="downloadBundleBtn">⬇ Download All (Manifest)</button>
    </div>`;

  /* Store bundle on AP for download */
  AP._bundle = bundle;
  $("downloadBundleBtn")?.addEventListener("click", downloadBundle);
}

/* ---- Bulk download ---- */

/**
 * Creates and triggers a JSON manifest download for all bundle assets.
 */
function downloadBundle() {
  const bundle  = AP._bundle;
  if (!bundle)  return;
  const manifest = {
    generated_at: new Date().toISOString(),
    project_id:   S.pid,
    assets:       (bundle.assets || []).map(a => ({
      filename:      a.filename,
      url:           a.url,
      type:          a.type,
      platforms:     a.platforms,
      quality_score: a.quality_score,
      model_used:    a.model_used,
    })),
  };
  const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `campaign_bundle_${S.pid || "export"}_${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  apLog("Manifest downloaded.", "ok");
}

/* ------------------------------------------------------------------ */
/* Socket.IO real-time progress                                        */
/* ------------------------------------------------------------------ */

let socket = null;

function initSocket() {
  if (typeof io === "undefined") return;
  try {
    socket = io({ transports: ["websocket", "polling"] });

    socket.on("connect", () => {
      if (S.pid) socket.emit("join_project", { project_id: S.pid });
    });

    /* Core pipeline events */
    socket.on("progress",    data => handleProgressUpdate(data));
    socket.on("joined",      () => {});

    /* Autopilot events */
    socket.on("autopilot_progress", data => handleAutopilotProgress(data));
    socket.on("autopilot_quality",  data => handleAutopilotQuality(data));
    socket.on("autopilot_complete", data => handleAutopilotComplete(data));
    socket.on("autopilot_error",    data => handleAutopilotError(data));

  } catch (e) {
    /* polling fallback still works */
  }
}

function handleProgressUpdate(data) {
  if (!data || (S.pid && data.project_id !== S.pid)) return;
  if (data.stage) {
    steps(data.stage);
    renderGenSteps(data.stage);
  }
  if (data.message) {
    const g = $("genStatus");
    if (g) g.textContent = data.message;
    setStatus(data.message, data.stage === "generation_failed" ? "warn" : "active");
  }
  if (["scraped","prompts_generated","reviewed","verified","generated","final_reviewed"].includes(data.stage)) {
    api("/api/projects/" + S.pid).then(p => render(p)).catch(() => {});
  }
  if (data.stage === "generation_failed") {
    $("genStatus").textContent = "Generation failed: " + (data.message || "Unknown error");
  }
}

/** Handle autopilot_progress SocketIO event */
function handleAutopilotProgress(data) {
  if (!data) return;
  updateAutopilotProgress({
    step:        data.step    || "Running…",
    message:     data.message || "",
    percent:     data.percent || 0,
    type:        data.type    || "info",
    eta_seconds: data.eta_seconds,
  });
}

/** Handle autopilot_quality SocketIO event — show quality badge on latest asset */
function handleAutopilotQuality(data) {
  if (!data) return;
  const { filename, score, type } = data;
  apLog(`Quality check: ${filename} → ${score}/100 (${type || "asset"})`, score >= 60 ? "ok" : "warn");

  /* Attempt to inject badge into an existing bundle card if rendered */
  const cards = document.querySelectorAll(".bundle-card");
  cards.forEach(card => {
    if (card.querySelector(".bundle-model")?.textContent?.includes(filename)) {
      const metaEl = card.querySelector(".bundle-card-meta");
      if (metaEl && !metaEl.querySelector(".quality-badge")) {
        metaEl.insertAdjacentHTML("beforeend", qualityBadge(score));
      }
    }
  });
}

/** Handle autopilot_complete SocketIO event — render full campaign bundle */
function handleAutopilotComplete(data) {
  AP.running = false;
  setLoading($("autopilotBtn"), false);
  apLog("✓ Autopilot complete — campaign bundle ready.", "ok");
  updateAutopilotProgress({ step: "Complete", message: "Campaign bundle generated.", percent: 100, type: "ok" });
  if (data?.bundle) {
    renderCampaignBundle(data.bundle);
    $("campaignBundleCard")?.scrollIntoView({ behavior: "smooth" });
  }
  setStatus("✓ Autopilot complete — all platform assets generated.", "active");
}

/** Handle autopilot_error SocketIO event */
function handleAutopilotError(data) {
  AP.running = false;
  setLoading($("autopilotBtn"), false);
  const msg = data?.message || "Autopilot encountered an unknown error.";
  apLog("Error: " + msg, "err");
  updateAutopilotProgress({ step: "Error", message: msg, percent: 0, type: "err" });
  setStatus("Autopilot error: " + msg, "warn");

  /* Show error badge in progress card */
  const label = $("autopilotStepLabel");
  if (label) label.innerHTML = `<span style="color:var(--ap-red,#ff5252)">⚠ ${esc(msg)}</span>`;
}

function joinProjectRoom(pid) {
  if (socket && socket.connected) socket.emit("join_project", { project_id: pid });
}

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */

(async () => {
  try {
    initSocket();
    $("saveProviderKeys")?.addEventListener("click", saveProviderKeys);
    $("autopilotBtn")?.addEventListener("click", launchAutopilot);

    await loadModels();
    renderPlatformChips();

    const h = await api("/api/health");
    const p = h.providers || {};
    updateProviderStatus(p);

    const orReady = p.openrouter?.configured;
    const agReady = p.agnes?.configured;
    let statusText = "Add API keys in .env to begin";
    if      (orReady && agReady) statusText = "All providers ready";
    else if (orReady)            statusText = "OpenRouter ready · Agnes needs key";
    else if (agReady)            statusText = "Agnes ready · OpenRouter needs key";
    $("health").innerHTML = `<span class="dot ${orReady || agReady ? "connected" : "disconnected"}"></span> ${statusText}`;

    await refreshMemory();
    steps("created");
  } catch (e) {
    $("health").innerHTML = '<span class="dot disconnected"></span> Backend unavailable';
  }
})();
