const S = { pid: null, p: null };
const $ = id => document.getElementById(id);
const show = id => $(id).classList.remove('hidden');

async function api(path, opt = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opt.timeout || 300000);
    try {
        const r = await fetch(path, {
            ...opt,
            signal: controller.signal,
            headers: { 'Content-Type': 'application/json', ...(opt.headers || {}) }
        });
        const d = await r.json();
        if (!r.ok) throw Error(d.error || 'Request failed');
        return d;
    } finally {
        clearTimeout(timer);
    }
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s ?? '';
    return d.innerHTML;
}

function steps(stage) {
    const a = [
        ['created', 'Brief'],
        ['scraped', 'Extract'],
        ['prompts_generated', 'Prompt'],
        ['generating', 'Generate'],
        ['generated', 'Deliver'],
        ['final_reviewed', 'Learn']
    ];
    let n = a.findIndex(x => x[0] === stage);
    if (stage === 'reviewed') n = 2;
    if (stage === 'prompting') n = 2;
    if (stage === 'scraping') n = 1;
    $('steps').innerHTML = a.map((x, i) =>
        '<div class="step ' + (i < n ? 'done' : '') + ' ' + (i === n ? 'active' : '') + '">' + (i + 1) + '. ' + x[1] + '</div>'
    ).join('');
}

async function copyTextToClipboard(text, btnId) {
    try {
        await navigator.clipboard.writeText(text);
        const btn = $(btnId);
        if (btn) {
            const orig = btn.innerHTML;
            btn.innerHTML = '✅ Copied!';
            setTimeout(() => { btn.innerHTML = orig; }, 2000);
        }
    } catch (e) {
        alert('Could not copy to clipboard. Select text manually.');
    }
}

function renderOutputs(p) {
    const outputs = p.outputs || {};
    $('outputs').innerHTML = Object.entries(outputs).map(([c, o]) => {
        let mediaHtml = '';
        let actionBtn = '';
        if (c === 'image') {
            mediaHtml = `<img class="preview" src="${o.url}" alt="Campaign Image">`;
            actionBtn = `<a class="btn-action" href="${o.url}" download="${o.filename}">⬇ Download Image</a>`;
        } else if (c === 'video') {
            mediaHtml = `<video class="preview" controls autoplay muted loop src="${o.url}"></video>`;
            actionBtn = `<a class="btn-action" href="${o.url}" download="${o.filename}">⬇ Download Video (MP4)</a>`;
        } else {
            // copy
            const copyContent = p.reviewed_prompts?.[c] || '';
            mediaHtml = `<div class="copy-box" id="copyText_${c}">Loading campaign copy...</div>`;
            actionBtn = `<button class="btn-action" id="copyBtn_${c}">📋 Copy to Clipboard</button>
                         <a class="btn-action" href="${o.url}" download="${o.filename}" target="_blank">📄 View Raw</a>`;
            // Fetch content if needed
            fetch(o.url).then(r => r.text()).then(t => {
                const box = $(`copyText_${c}`);
                if (box) box.textContent = t;
                const copyBtn = $(`copyBtn_${c}`);
                if (copyBtn) copyBtn.onclick = () => copyTextToClipboard(t, `copyBtn_${c}`);
            }).catch(() => {
                const box = $(`copyText_${c}`);
                if (box) box.textContent = copyContent;
            });
        }

        return `<div class="output">
            <h4>
                <span>${c.toUpperCase()} DELIVERABLE</span>
                <span class="output-meta">${esc(o.model_used)}</span>
            </h4>
            ${mediaHtml}
            <div class="output-actions">${actionBtn}</div>
            ${o.warning ? '<p class="warn">Note: ' + esc(o.warning) + '</p>' : ''}
            <div class="review-row">
                <label>Deliverable Rating
                    <select id="d_${c}">
                        <option value="true">Approved (Production Ready)</option>
                        <option value="false">Needs Correction & Regenerate</option>
                    </select>
                </label>
                <label>Feedback & Corrections
                    <textarea id="n_${c}" rows="2" placeholder="Specific notes to teach the agent..."></textarea>
                </label>
            </div>
            <label>Rating (1-5): <input id="r_${c}" type="number" min="1" max="5" value="5" style="width:70px;display:inline-block;margin-left:8px;"></label>
        </div>`;
    }).join('');
}

function render(p) {
    S.p = p;
    steps(p.stage);

    if (p.scraped) {
        show('referenceCard');
        const assets = p.scraped.assets || [];
        const validImgs = assets.filter(a => a.type === 'image' && !a.error);
        const validVids = assets.filter(a => a.type === 'video' && !a.error);
        $('reference').innerHTML = `
            <div class="reference">
                <div class="stat"><b>${esc(p.scraped.title || 'Brand Intelligence')}</b><br><span class="muted">${esc((p.scraped.meta_description || p.scraped.text || '').slice(0, 150))}...</span></div>
                <div class="stat"><b>${validImgs.length}</b><br>Images Ingested</div>
                <div class="stat"><b>${validVids.length}</b><br>Videos / Frames</div>
            </div>
            <div class="asset-grid">
                ${assets.filter(a => !a.error).map(a => {
                    const fn = (a.path || a.frame_paths?.[0] || '').replaceAll('\\', '/').split('/').pop();
                    return `<div class="asset">
                        ${a.type === 'image' ? `<img src="/reference/${p.id}/${encodeURIComponent(fn)}">` : '<div class="stat" style="text-align:center;padding:35px 0;">🎥 VIDEO FRAME</div>'}
                        <small>${a.type} • ${esc(fn.slice(0, 20))}</small>
                    </div>`;
                }).join('')}
            </div>
        `;
    }

    if (p.reviewed_prompts && Object.keys(p.reviewed_prompts).length) {
        show('promptCard');
        $('prompts').innerHTML = Object.entries(p.reviewed_prompts).map(([c, v]) =>
            `<div class="prompt">
                <h4>${c.toUpperCase()} DIRECTIVE</h4>
                <textarea id="p_${c}" rows="8">${esc(v)}</textarea>
                <label>Reviewer Feedback & Constraints</label>
                <textarea id="f_${c}" rows="2" placeholder="e.g. emphasize international luxury and gold color accents...">${esc(p.review_feedback?.[c] || '')}</textarea>
            </div>`
        ).join('');
    }

    if (p.verified || Object.keys(p.outputs || {}).length) {
        show('modelCard');
        show('outputCard');
        renderOutputs(p);
    }

    if (Object.keys(p.chosen_models || {}).length) {
        $('models').innerHTML = Object.entries(p.chosen_models).map(([c, m]) =>
            `<div class="stat"><b>${c.toUpperCase()}</b> — ${esc(m.label || m.id)}</div>`
        ).join('');
    }
}

async function refreshMemory() {
    try {
        const d = await api('/api/learning');
        const s = d.stats || {};
        $('memory').innerHTML = `<b>${s.reviews || 0}</b> total reviews<br><b>${s.approved || 0}</b> approved · <b>${s.rejected || 0}</b> refined`;
    } catch (e) {
        $('memory').textContent = 'Memory active.';
    }
}

async function waitForGeneration() {
    show('verifyCard');
    for (;;) {
        await new Promise(r => setTimeout(r, 1500));
        const p = await api('/api/projects/' + S.pid);
        render(p);
        if (p.history && p.history.length) {
            const lastMsg = p.history[p.history.length - 1].message;
            $('genStatus').textContent = lastMsg;
            $('status').textContent = lastMsg;
        }
        if (p.stage === 'generated' || p.stage === 'final_reviewed') {
            $('genStatus').textContent = '✅ Full campaign generation completed successfully!';
            $('status').textContent = '✅ Campaign launch completed! Check your deliverables below.';
            return;
        }
        if (p.stage === 'generation_failed') {
            throw Error(p.generation_error || 'Generation encountered an unexpected error.');
        }
    }
}

// 1-CLICK AUTOMATED CAMPAIGN
$('autoLaunchBtn').onclick = async () => {
    try {
        const types = [...document.querySelectorAll('.checks input:checked')].map(x => x.value);
        if (!types.length) throw Error('Select at least one output format (Copy, Image, or Video).');
        const brief = $('brief').value.trim();
        if (!brief) throw Error('Please provide a campaign brief or description.');

        $('status').className = 'status active';
        $('status').textContent = 'Step 1/3: Initializing campaign workspace...';

        let p = await api('/api/projects', {
            method: 'POST',
            body: JSON.stringify({
                url: $('url').value.trim(),
                brief: brief,
                creative_types: types
            })
        });
        S.pid = p.id;
        render(p);

        const files = [...$('files').files];
        if (files.length) {
            $('status').textContent = `Uploading ${files.length} reference asset(s)...`;
            for (const f of files) {
                const fd = new FormData();
                fd.append('file', f);
                const r = await fetch('/api/projects/' + S.pid + '/upload-reference', { method: 'POST', body: fd });
                const d = await r.json();
                if (!r.ok) throw Error(d.error || 'Upload failed');
                p = d;
            }
            render(p);
        }

        $('status').textContent = '🚀 Launching automated multi-modal campaign pipeline...';
        p = await api('/api/projects/' + S.pid + '/auto-run', { method: 'POST' });
        render(p);

        await waitForGeneration();
        $('outputCard').scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        $('status').className = 'status warn';
        $('status').textContent = 'Error: ' + e.message;
        alert(e.message);
    }
};

// STEP-BY-STEP MODE
$('create').onclick = async () => {
    try {
        const types = [...document.querySelectorAll('.checks input:checked')].map(x => x.value);
        if (!types.length) throw Error('Select at least one creative format.');
        const brief = $('brief').value.trim();
        if (!brief) throw Error('Please enter a brief.');

        $('status').className = 'status active';
        $('status').textContent = 'Extracting website intelligence & assets...';

        let p = await api('/api/projects', {
            method: 'POST',
            body: JSON.stringify({ url: $('url').value.trim(), brief, creative_types: types })
        });
        S.pid = p.id;

        p = await api('/api/projects/' + S.pid + '/scrape', { method: 'POST' });
        for (const f of [...$('files').files]) {
            const fd = new FormData();
            fd.append('file', f);
            const r = await fetch('/api/projects/' + S.pid + '/upload-reference', { method: 'POST', body: fd });
            const d = await r.json();
            if (!r.ok) throw Error(d.error || 'Upload failed');
            p = d;
        }
        render(p);
        $('status').textContent = 'Reference extraction complete. Proceed to Step 2.';
        $('referenceCard').scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        $('status').className = 'status warn';
        $('status').textContent = e.message;
    }
};

$('promptBtn').onclick = async () => {
    try {
        $('status').textContent = 'Synthesizing creative prompts...';
        const p = await api('/api/projects/' + S.pid + '/prompts', { method: 'POST' });
        render(p);
        $('promptCard').scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        alert(e.message);
    }
};

$('reviewBtn').onclick = async () => {
    try {
        const edits = {}, feedback = {};
        for (const c of S.p.creative_types) {
            edits[c] = $('p_' + c)?.value || '';
            feedback[c] = $('f_' + c)?.value || '';
        }
        const p = await api('/api/projects/' + S.pid + '/review', {
            method: 'POST',
            body: JSON.stringify({ edited_prompts: edits, feedback })
        });
        render(p);
        alert('Prompts refined and ready for generation!');
    } catch (e) {
        alert(e.message);
    }
};

$('verifyBtn').onclick = async () => {
    try {
        $('genStatus').textContent = 'Initiating multi-modal generation...';
        const p = await api('/api/projects/' + S.pid + '/verify', {
            method: 'POST',
            body: JSON.stringify({ models: {} })
        });
        render(p);
        await waitForGeneration();
        $('outputCard').scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
        $('genStatus').textContent = 'Generation failed: ' + e.message;
        alert(e.message);
    }
};

$('finalBtn').onclick = async () => {
    try {
        const decisions = {};
        for (const c of Object.keys(S.p.outputs || {})) {
            decisions[c] = {
                approved: $('d_' + c).value === 'true',
                notes: $('n_' + c).value,
                rating: Number($('r_' + c).value)
            };
        }
        const p = await api('/api/projects/' + S.pid + '/final-review', {
            method: 'POST',
            body: JSON.stringify({ decisions })
        });
        render(p);
        await refreshMemory();
        alert('Review saved to agent memory! Any rejected deliverables have been regenerated.');
    } catch (e) {
        alert(e.message);
    }
};

$('newBtn').onclick = () => location.reload();

// INITIALIZE HEALTH & MEMORY
(async () => {
    try {
        const h = await api('/api/health');
        if (h.openrouter && h.openrouter.connected) {
            $('health').innerHTML = `<span class="dot connected"></span> OpenRouter: Active (${h.openrouter.primary_model || 'Connected'})`;
        } else if (h.openrouter_configured) {
            $('health').innerHTML = `<span class="dot connected"></span> OpenRouter: Key Loaded`;
        } else {
            $('health').innerHTML = `<span class="dot disconnected"></span> OpenRouter: Offline`;
        }
        await refreshMemory();
    } catch (e) {
        $('health').innerHTML = `<span class="dot disconnected"></span> Backend Unavailable`;
    }
})();

