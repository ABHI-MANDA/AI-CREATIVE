const S = { pid: null, p: null, models: {}, selectedModels: {}, lastStage: null, busy: false };
const $ = id => document.getElementById(id);
const show = id => $(id)?.classList.remove("hidden");
const hide = id => $(id)?.classList.add("hidden");

async function api(path, opt = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opt.timeout || 300000);
  try {
    const r = await fetch(path, {
      ...opt,
      signal: controller.signal,
      headers: opt.body instanceof FormData ? (opt.headers || {}) : { "Content-Type": "application/json", ...(opt.headers || {}) },
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw Error(d.error || `Request failed (${r.status})`);
    return d;
  } finally {
    clearTimeout(timer);
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

/* ------------------------------------------------------------------ */
/* Pipeline step tracker (7 sequenced stages)                          */
/* ------------------------------------------------------------------ */

const STEP_DEFS = [
  { key: "created", label: "Brief", sub: "URL · upload · mood" },
  { key: "scraped", label: "Ingest", sub: "HTML · images · videos · frames" },
  { key: "prompts_generated", label: "Analyze & Prompt", sub: "Visual analysis → prompts" },
  { key: "reviewed", label: "Review", sub: "Human edits & feedback" },
  { key: "verified", label: "Verify & Generate", sub: "Best-model routing" },
  { key: "generated", label: "Deliver", sub: "Outputs ready" },
  { key: "final_reviewed", label: "Final Review", sub: "Approve · memory · refine" },
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
  { match: ["scraping", "scraped"], label: "Reference ingestion (HTML / images / videos / frames)" },
  { match: ["analyzing", "prompting", "prompts_generated"], label: "Visual + metadata analysis → prompt engineering" },
  { match: ["reviewed", "verified"], label: "Human review & verification gate" },
  { match: ["generating", "generated", "generation_failed"], label: "Best-model generation (parallel creatives)" },
  { match: ["final_reviewed"], label: "Final review → memory / refine loop" },
];

function stageIndex(stage) {
  if (stage in STAGE_STEP) return STAGE_STEP[stage];
  return 0;
}

function steps(stage) {
  const n = stageIndex(stage);
  const failed = stage === "generation_failed";
  $("steps").innerHTML = STEP_DEFS.map((s, i) => {
    const done = i < n || (stage === "final_reviewed" && i <= n);
    const active = i === n && stage !== "final_reviewed";
    const err = active && failed;
    return `<div class="step ${done ? "done" : ""} ${active ? "active" : ""} ${err ? "err" : ""}">
      <i>${done && !active ? "✓" : i + 1}</i>
      <span>${esc(s.label)}<small>${esc(s.sub)}</small></span>
    </div>`;
  }).join("");

  const pct = stage === "final_reviewed" ? 100 : Math.round(((n + 1) / STEP_DEFS.length) * 100);
  const fill = $("pipelineFill");
  const pctEl = $("pipelinePct");
  if (fill) fill.style.width = pct + "%";
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
    const t = new Date((h.timestamp || 0) * 1000);
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
    copy: { mood },
    image: { image_size: $("imageSize").value, mood },
    video: {
      duration: Number($("videoDuration").value || 6),
      aspect_ratio: $("videoRatio").value,
      resolution: $("videoResolution").value,
      mood,
    },
  };
}

function refreshDuration() {
  const model = S.models.video?.find(m => m.id === selectedModel("video"));
  const durations = model?.durations || [4, 6, 8];
  const sel = $("videoDuration");
  if (!sel) return;
  const old = Number(sel.value);
  sel.innerHTML = durations.map(x => `<option value="${x}">${x} seconds</option>`).join("");
  if (durations.includes(old)) sel.value = String(old);
  else if (durations.includes(6)) sel.value = "6";
  else sel.value = String(durations[0]);
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
  const box = $("modelControls");
  if (!box) return;
  box.innerHTML = types.map(type => {
    const list = S.models[type] || [];
    const usableCount = list.filter(m => m.usable).length;
    const totalCount = list.length;
    const firstUsable = list.find(m => m.usable);
    const savedModel = S.selectedModels[type];
    const currentVal = savedModel && list.find(m => m.id === savedModel && m.usable)
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
          agnes: $("agnesKey").value,
        },
      }),
    });
    $("openrouterKey").value = "";
    $("agnesKey").value = "";
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
    await navigator.clipboard.write(text);
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
  const a = p.scraped.assets || [];
  const imgs = a.filter(x => x.type === "image" && !x.error);
  const vids = a.filter(x => x.type === "video" && !x.error);
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

function renderOutputs(p) {
  show("outputCard");
  const outputs = p.outputs || {};
  $("outputs").innerHTML = Object.entries(outputs).map(([c, o]) => {
    let media = "", action = "";
    if (c === "image") {
      media = `<img class="preview" src="${o.url}" alt="Generated campaign image">`;
      action = `<a class="btn-action" href="${o.url}" download="${o.filename}">↓ Download image</a>`;
    } else if (c === "video") {
      media = `<video class="preview" controls autoplay muted loop src="${o.url}"></video>`;
      action = `<a class="btn-action" href="${o.url}" download="${o.filename}">↓ Download video</a>`;
    } else {
      media = `<div class="copy-box" id="copyText_${c}">Loading campaign copy…</div>`;
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
    }
    const prior = p.final_review?.[c] || {};
    return `<div class="output">
      <h4><span>${c.toUpperCase()} DELIVERABLE</span><span class="output-meta">${esc(o.model_used)}</span></h4>
      ${media}
      <div class="output-actions">${action}</div>
      ${o.warning ? `<p class="warn">Provider note: ${esc(o.warning)}</p>` : ""}
      ${o.duration ? `<div class="meta-line">Generated duration: ${o.duration}s</div>` : ""}
      <div class="review-row">
        <label>Decision
          <select id="d_${c}">
            <option value="true" ${prior.approved !== false ? "selected" : ""}>✓ Approved</option>
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

  if (p.verified || p.stage === "generating" || p.stage === "generated" ||
      p.stage === "final_reviewed" || p.stage === "generation_failed") {
    if (Object.keys(p.outputs || {}).length || p.stage === "generating" || p.stage === "generation_failed") {
      show("verifyCard");
      renderGenSteps(p.stage);
    }
  }

  if (Object.keys(p.outputs || {}).length) {
    show("modelCard");
    renderOutputs(p);
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
      $("status").className = "status active";
      $("status").textContent = "Campaign generated — review your deliverables below.";
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
    btn.disabled = true;
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
  el.className = "status " + kind;
  el.textContent = msg;
  el.classList.remove("hidden");
}

/* ------------------------------------------------------------------ */
/* Actions                                                             */
/* ------------------------------------------------------------------ */

async function createProject() {
  const types = selectedTypes();
  if (!types.length) throw Error("Tick at least one creative type (Copy / Image / Video).");
  const brief = $("brief").value.trim();
  if (!brief) throw Error("Please provide a campaign brief.");
  return await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({
      url: $("url").value.trim(),
      brief,
      creative_types: types,
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
        models: selectedModelsMap(),
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
    p = await api("/api/projects/" + S.pid + "/scrape", { method: "POST" });
    p = await uploadReferences(p);
    render(p);
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
      edits[c] = $("p_" + c)?.value || "";
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
        models: selectedModelsMap(),
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
        notes: $("n_" + c).value,
        rating: Number($("r_" + c).value),
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
  const hint = $("typeChecks")?.parentElement?.querySelector("span");
  // keep model cards in sync visually (disabled state is cosmetic)
  document.querySelectorAll(".model-card[data-type]").forEach(card => {
    card.classList.toggle("muted-card", !types.includes(card.dataset.type));
  });
});

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
    socket.on("progress", data => handleProgressUpdate(data));
    socket.on("joined", () => {});
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
  if (["scraped", "prompts_generated", "reviewed", "verified", "generated", "final_reviewed"].includes(data.stage)) {
    api("/api/projects/" + S.pid).then(p => render(p)).catch(() => {});
  }
  if (data.stage === "generation_failed") {
    $("genStatus").textContent = "Generation failed: " + (data.message || "Unknown error");
  }
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
    await loadModels();
    const h = await api("/api/health");
    const p = h.providers || {};
    updateProviderStatus(p);
    const orReady = p.openrouter?.configured;
    const agReady = p.agnes?.configured;
    let statusText = "Add API keys in .env to begin";
    if (orReady && agReady) statusText = "All providers ready";
    else if (orReady) statusText = "OpenRouter ready · Agnes needs key";
    else if (agReady) statusText = "Agnes ready · OpenRouter needs key";
    $("health").innerHTML = `<span class="dot ${orReady || agReady ? "connected" : "disconnected"}"></span> ${statusText}`;
    await refreshMemory();
    steps("created");
  } catch (e) {
    $("health").innerHTML = '<span class="dot disconnected"></span> Backend unavailable';
  }
})();
