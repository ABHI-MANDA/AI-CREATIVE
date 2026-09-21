const S = { pid: null, p: null, models: {}, selectedModels: {} };
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
      headers: { "Content-Type": "application/json", ...(opt.headers || {}) },
    });
    const d = await r.json();
    if (!r.ok) throw Error(d.error || "Request failed");
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

function steps(stage) {
  const a = [
    ["created", "Brief"],
    ["scraped", "Extract"],
    ["prompts_generated", "Prompt"],
    ["generating", "Generate"],
    ["generated", "Deliver"],
    ["final_reviewed", "Learn"],
  ];
  let n = a.findIndex(x => x[0] === stage);
  if (["reviewed", "prompting"].includes(stage)) n = 2;
  if (stage === "scraping") n = 1;
  $("steps").innerHTML = a
    .map(
      (x, i) =>
        `<div class="step ${i < n ? "done" : ""} ${i === n ? "active" : ""}"><i>${i + 1}</i>${x[1]}</div>`
    )
    .join("");
}

function selectedTypes() {
  return [...document.querySelectorAll(".checks input:checked")].map(x => x.value);
}

function selectedModel(type) {
  return S.selectedModels[type] || $(`model_${type}`)?.value || null;
}

function selectedModelsMap() {
  const types = selectedTypes().length ? selectedTypes() : ["copy", "image", "video"];
  return Object.fromEntries(types.map(c => [c, selectedModel(c)]));
}

function currentSettings() {
  return {
    copy: { mood: $("mood").value },
    image: { image_size: $("imageSize").value, mood: $("mood").value },
    video: {
      duration: Number($("videoDuration").value || 6),
      aspect_ratio: $("videoRatio").value,
      resolution: $("videoResolution").value,
      mood: $("mood").value,
    },
  };
}

function refreshDuration() {
  const model = S.models.video?.find(m => m.id === selectedModel("video"));
  const durations = model?.durations || [4, 6, 8];
  const sel = $("videoDuration");
  const old = Number(sel.value);
  sel.innerHTML = durations.map(x => `<option value="${x}">${x} seconds</option>`).join("");
  if (durations.includes(old)) {
    sel.value = String(old);
  } else if (durations.includes(6)) {
    sel.value = "6";
  } else {
    sel.value = String(durations[0]);
  }
  const note = model?.note || "";
  $("durationHint").textContent = model
    ? `${model.label} supports: ${durations.join(", ")}s${note ? " \u2014 " + note : ""}`
    : "Select a video model to see supported durations.";
  if (old && !durations.includes(old)) {
    $("durationHint").classList.add("hint-adjusted");
    $("durationHint").textContent += ` (adjusted from ${old}s)`;
  } else {
    $("durationHint").classList.remove("hint-adjusted");
  }
}

function renderModelControls() {
  const types = ["copy", "image", "video"];
  $("modelControls").innerHTML = types
    .map(type => {
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
        <div class="model-icon">${type === "copy" ? "Aa" : type === "image" ? "\u25C8" : "\u25B6"}</div>
        <div class="model-info">
          <b>${type.toUpperCase()}</b>
          <span>${usableCount
            ? usableCount + " of " + totalCount + " engine" + (totalCount > 1 ? "s" : "") + " available"
            : "Configure API keys in .env to unlock"}</span>
        </div>
        <div class="model-select-wrap">
          <select id="model_${type}" ${totalCount ? "" : "disabled"}>
            ${list
              .map(
                m =>
                  `<option value="${esc(m.id)}" ${m.usable ? "" : "disabled"} ${m.id === currentVal ? "selected" : ""}>${esc(m.label)}${
                    m.usable ? "" : " \u2014 " + (m.unavailable_reason || "unavailable")
                  }</option>`
              )
              .join("")}
          </select>
        </div>
      </div>`;
    })
    .join("");
  types.forEach(type => {
    const sel = $(`model_${type}`);
    if (!sel) return;
    sel.addEventListener("change", function () {
      S.selectedModels[type] = this.value;
    });
  });
  const video = $("model_video");
  if (video) video.addEventListener("change", function () {
    S.selectedModels["video"] = this.value;
    refreshDuration();
  });
  refreshDuration();
}

async function loadModels() {
  S.models = await api("/api/models");
  renderModelControls();
}

async function copyTextToClipboard(text, btnId) {
  try {
    await navigator.clipboard.write(text);
    const b = $(btnId);
    if (b) {
      const o = b.innerHTML;
      b.innerHTML = "\u2713 Copied";
      setTimeout(() => (b.innerHTML = o), 1600);
    }
  } catch (e) {
    alert("Could not copy to clipboard.");
  }
}

function renderOutputs(p) {
  const outputs = p.outputs || {};
  $("outputs").innerHTML = Object.entries(outputs)
    .map(([c, o]) => {
      let media = "", action = "";
      if (c === "image") {
        media = `<img class="preview" src="${o.url}" alt="Generated campaign image">`;
        action = `<a class="btn-action" href="${o.url}" download="${o.filename}">\u2193 Download image</a>`;
      } else if (c === "video") {
        media = `<video class="preview" controls autoplay muted loop src="${o.url}"></video>`;
        action = `<a class="btn-action" href="${o.url}" download="${o.filename}">\u2193 Download video</a>`;
      } else {
        media = `<div class="copy-box" id="copyText_${c}">Loading campaign copy\u2026</div>`;
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
      return `<div class="output">
        <h4><span>${c.toUpperCase()} DELIVERABLE</span><span class="output-meta">${esc(o.model_used)}</span></h4>
        ${media}
        <div class="output-actions">${action}</div>
        ${o.warning ? `<p class="warn">Provider note: ${esc(o.warning)}</p>` : ""}
        ${o.duration ? `<div class="meta-line">Generated duration: ${o.duration}s</div>` : ""}
        <div class="review-row">
          <label>Decision<select id="d_${c}"><option value="true">Approved</option><option value="false">Needs correction</option></select></label>
          <label>Feedback<textarea id="n_${c}" rows="2" placeholder="Specific correction or creative direction\u2026"></textarea></label>
        </div>
        <label>Rating (1\u20135)<input id="r_${c}" type="number" min="1" max="5" value="5" style="width:80px"></label>
      </div>`;
    })
    .join("");
}

function render(p) {
  S.p = p;
  steps(p.stage);
  if (p.scraped) {
    show("referenceCard");
    const a = p.scraped.assets || [],
      imgs = a.filter(x => x.type === "image" && !x.error),
      vids = a.filter(x => x.type === "video" && !x.error);
    $("reference").innerHTML = `
      <div class="reference">
        <div class="stat"><b>${esc(p.scraped.title || "Brand intelligence")}</b><br><span class="muted">${esc(
      (p.scraped.meta_description || p.scraped.text || "").slice(0, 150)
    )}\u2026</span></div>
        <div class="stat"><b>${imgs.length}</b><br>Images ingested</div>
        <div class="stat"><b>${vids.length}</b><br>Videos / frames</div>
      </div>
      <div class="asset-grid">${a
        .filter(x => !x.error)
        .map(x => {
          const fn = (x.path || x.frame_paths?.[0] || "").replaceAll("\\", "/").split("/").pop();
          return `<div class="asset">${
            x.type === "image"
              ? `<img src="/reference/${p.id}/${encodeURIComponent(fn)}">`
              : `<div class="video-tile">VIDEO / FRAMES</div>`
          }<small>${x.type} \u2022 ${esc(fn.slice(0, 28))}</small></div>`;
        })
        .join("")}</div>`;
  }
  if (p.reviewed_prompts && Object.keys(p.reviewed_prompts).length) {
    show("promptCard");
    $("prompts").innerHTML = Object.entries(p.reviewed_prompts)
      .map(
        ([c, v]) =>
          `<div class="prompt"><h4>${c.toUpperCase()} DIRECTIVE</h4><textarea id="p_${c}" rows="8">${esc(
            v
          )}</textarea><label>Reviewer feedback</label><textarea id="f_${c}" rows="2" placeholder="Add constraints or refinements\u2026">${esc(
            p.review_feedback?.[c] || ""
          )}</textarea></div>`
      )
      .join("");
  }
  if (p.verified || Object.keys(p.outputs || {}).length) {
    show("modelCard");
    show("outputCard");
    renderOutputs(p);
  }
  if (Object.keys(p.chosen_models || {}).length)
    $("models").innerHTML = Object.entries(p.chosen_models)
      .map(
        ([c, m]) =>
          `<div class="model-card ready"><div class="model-icon">${
            c === "copy" ? "Aa" : c === "image" ? "\u25C8" : "\u25B6"
          }</div><div class="model-info"><b>${c.toUpperCase()}</b><span>${esc(m.label || m.id)}</span></div></div>`
      )
      .join("");
}

async function refreshMemory() {
  try {
    const d = await api("/api/learning"),
      s = d.stats || {};
    $("memory").innerHTML = `<b>${s.reviews || 0}</b> reviews<br><b>${s.approved || 0}</b> approved \u00b7 <b>${
      s.rejected || 0
    }</b> refined`;
  } catch (e) {
    $("memory").textContent = "Memory active.";
  }
}

async function waitForGeneration() {
  show("verifyCard");
  for (;;) {
    await new Promise(r => setTimeout(r, 1500));
    const p = await api("/api/projects/" + S.pid);
    render(p);
    if (p.history?.length) {
      const m = p.history[p.history.length - 1].message;
      $("genStatus").textContent = m;
      $("status").textContent = m;
    }
    if (["generated", "final_reviewed"].includes(p.stage)) {
      $("genStatus").textContent = "\u2713 Generation completed successfully.";
      $("status").className = "status active";
      $("status").textContent = "Campaign generated \u2014 review your deliverables below.";
      return;
    }
    if (p.stage === "generation_failed") throw Error(p.generation_error || "Generation failed.");
  }
}

async function createProject() {
  const types = selectedTypes();
  if (!types.length) throw Error("Select at least one output format.");
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
  for (const f of [...$("files").files]) {
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch("/api/projects/" + p.id + "/upload-reference", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw Error(d.error || "Upload failed");
    p = d;
  }
  return p;
}

function setLoading(btn, loading) {
  if (!btn) return;
  if (loading) {
    btn.dataset.origText = btn.innerHTML;
    btn.disabled = true;
    btn.classList.add("loading");
  } else {
    btn.disabled = false;
    btn.classList.remove("loading");
    if (btn.dataset.origText) btn.innerHTML = btn.dataset.origText;
  }
}

$("autoLaunchBtn").onclick = async function () {
  const btn = this;
  try {
    setLoading(btn, true);
    $("status").className = "status active";
    $("status").textContent = "Initializing creative workspace\u2026";
    $("status").classList.remove("hidden");
    let p = await createProject();
    S.pid = p.id;
    render(p);
    p = await uploadReferences(p);
    render(p);
    $("status").textContent = "Launching multi-model campaign pipeline\u2026";
    p = await api("/api/projects/" + S.pid + "/auto-run", {
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
    $("status").className = "status warn";
    $("status").textContent = "Error: " + e.message;
    alert(e.message);
  } finally {
    setLoading(btn, false);
  }
};

$("create").onclick = async function () {
  const btn = this;
  try {
    setLoading(btn, true);
    $("status").className = "status active";
    $("status").textContent = "Extracting reference intelligence\u2026";
    $("status").classList.remove("hidden");
    let p = await createProject();
    S.pid = p.id;
    p = await api("/api/projects/" + S.pid + "/scrape", { method: "POST" });
    p = await uploadReferences(p);
    render(p);
    $("status").textContent = "Reference extraction complete. Build prompts next.";
    $("referenceCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("status").className = "status warn";
    $("status").textContent = e.message;
  } finally {
    setLoading(btn, false);
  }
};

$("promptBtn").onclick = async function () {
  const btn = this;
  try {
    setLoading(btn, true);
    $("status").textContent = "Synthesizing production prompts\u2026";
    $("status").className = "status active";
    const p = await api("/api/projects/" + S.pid + "/prompts", { method: "POST" });
    render(p);
    $("promptCard").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    alert(e.message);
  } finally {
    setLoading(btn, false);
  }
};

$("reviewBtn").onclick = async function () {
  const btn = this;
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
    $("status").className = "status active";
    $("status").textContent = "Prompts refined and ready for generation.";
  } catch (e) {
    alert(e.message);
  } finally {
    setLoading(btn, false);
  }
};

$("verifyBtn").onclick = async function () {
  const btn = this;
  try {
    setLoading(btn, true);
    $("genStatus").textContent = "Starting generation with your selected models\u2026";
    $("status").className = "status active";
    $("status").textContent = "Generating campaign creatives\u2026";
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
    $("status").className = "status warn";
    $("status").textContent = "Generation failed: " + e.message;
  } finally {
    setLoading(btn, false);
  }
};

$("finalBtn").onclick = async function () {
  const btn = this;
  try {
    setLoading(btn, true);
    const decisions = {};
    for (const c of Object.keys(S.p.outputs || {}))
      decisions[c] = {
        approved: $("d_" + c).value === "true",
        notes: $("n_" + c).value,
        rating: Number($("r_" + c).value),
      };
    const p = await api("/api/projects/" + S.pid + "/final-review", {
      method: "POST",
      body: JSON.stringify({ decisions }),
    });
    render(p);
    await refreshMemory();
    $("status").className = "status active";
    $("status").textContent = "Review saved. Agent has learned from your feedback.";
  } catch (e) {
    alert(e.message);
  } finally {
    setLoading(btn, false);
  }
};

$("newBtn").onclick = () => location.reload();

(async () => {
  try {
    await loadModels();
    const h = await api("/api/health");
    const p = h.providers || {};
    $("providerStatus").innerHTML = Object.entries(p)
      .map(
        ([k, v]) =>
          `<span class="provider-pill ${v.configured ? "on" : "off"}">${
            k === "openrouter" ? "OpenRouter" : "Agnes"
          } ${v.configured ? "\u25CF" : "\u25CB"}</span>`
      )
      .join("");
    const orReady = p.openrouter?.configured;
    const agReady = p.agnes?.configured;
    let statusText = "";
    if (orReady && agReady) {
      statusText = "All providers ready";
    } else if (orReady) {
      statusText = "OpenRouter ready \u00b7 Agnes needs key";
    } else if (agReady) {
      statusText = "Agnes ready \u00b7 OpenRouter needs key";
    } else {
      statusText = "Add API keys in .env to begin";
    }
    $("health").innerHTML = `<span class="dot ${
      orReady || agReady ? "connected" : "disconnected"
    }"></span> ${statusText}`;
    await refreshMemory();
  } catch (e) {
    $("health").innerHTML = '<span class="dot disconnected"></span> Backend unavailable';
  }
})();
