"use strict";
const $ = (id) => document.getElementById(id);
const els = {
  health: $("health"), healthText: $("healthText"), themeBtn: $("themeBtn"),
  contract: $("contract"), charCount: $("charCount"),
  reviewBtn: $("reviewBtn"), clearBtn: $("clearBtn"), fileInput: $("fileInput"),
  sampleBtns: $("sampleBtns"),
  setupCard: $("setupCard"), setupBase: $("setupBase"), setupKey: $("setupKey"),
  setupModel: $("setupModel"), modelList: $("modelList"),
  setupTest: $("setupTest"), setupSave: $("setupSave"), setupMsg: $("setupMsg"),
  error: $("error"),
  summaryBar: $("summaryBar"), summaryText: $("summaryText"),
  counts: $("counts"), riskLine: $("riskLine"),
  progress: $("progress"), costLine: $("costLine"),
  filterRow: $("filterRow"), search: $("search"),
  nav: $("nav"), results: $("results"), empty: $("empty"),
  playbook: $("playbook"), playbookMeta: $("playbookMeta"),
};

let state = { clauses: [], total: 0, done: false, filter: "all", query: "" };

/* ---------- theme ---------- */
function initTheme() {
  const saved = localStorage.getItem("precedent-theme");
  const theme = saved || "dark";
  document.documentElement.dataset.theme = theme;
  els.themeBtn.textContent = theme === "dark" ? "Light" : "Dark";
}
els.themeBtn.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("precedent-theme", next);
  els.themeBtn.textContent = next === "dark" ? "Light" : "Dark";
});

/* ---------- helpers ---------- */
async function api(path, opts) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function setError(msg) {
  if (!msg) { els.error.hidden = true; els.error.textContent = ""; return; }
  els.error.hidden = false; els.error.textContent = msg;
}
function fmtTokens(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
}

/* ---------- word diff (redline) ---------- */
function wordDiff(a, b) {
  const aw = String(a || "").split(/\s+/).filter(Boolean);
  const bw = String(b || "").split(/\s+/).filter(Boolean);
  const n = aw.length, m = bw.length;
  if (!n || !m) return esc(b || a || "");
  // LCS on capped sizes to stay fast.
  const N = Math.min(n, 400), M = Math.min(m, 400);
  const dp = Array.from({ length: N + 1 }, () => new Uint16Array(M + 1));
  for (let i = N - 1; i >= 0; i--)
    for (let j = M - 1; j >= 0; j--)
      dp[i][j] = aw[i] === bw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let out = "", i = 0, j = 0;
  while (i < N && j < M) {
    if (aw[i] === bw[j]) { out += esc(aw[i]) + " "; i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out += `<del>${esc(aw[i])}</del> `; i++; }
    else { out += `<ins>${esc(bw[j])}</ins> `; j++; }
  }
  while (i < n) out += `<del>${esc(aw[i++])}</del> `;
  while (j < m) out += `<ins>${esc(bw[j++])}</ins> `;
  return out;
}

/* ---------- health + setup ---------- */
async function refreshHealth() {
  try {
    const h = await api("/api/health");
    const mode = h.mode || "live";
    els.health.className = "health " + (mode === "demo" ? "demo" : "ok");
    els.healthText.textContent = `${h.status} · ${mode} · ${h.model} · ${h.playbook_topics} topics`;
    els.setupCard.hidden = mode !== "demo";
  } catch (e) {
    els.health.className = "health bad";
    els.healthText.textContent = "server unreachable";
  }
}
async function setupCall(save) {
  els.setupMsg.textContent = save ? "Saving…" : "Testing…";
  try {
    const data = await api("/api/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        base_url: els.setupBase.value.trim(),
        api_key: els.setupKey.value.trim(),
        model: els.setupModel.value.trim(),
        save,
      }),
    });
    els.modelList.innerHTML = (data.models || []).map((m) => `<option value="${esc(m)}">`).join("");
    els.setupMsg.textContent = save
      ? `Connected (${data.models.length} models).`
      : `Reachable (${data.models.length} models). Pick one, then Save.`;
    if (save) { els.setupKey.value = ""; await refreshHealth(); }
  } catch (e) { els.setupMsg.textContent = `Failed: ${e.message}`; }
}
els.setupTest.addEventListener("click", () => setupCall(false));
els.setupSave.addEventListener("click", () => setupCall(true));

/* ---------- samples + playbook ---------- */
async function loadSample(name) {
  setError("");
  try {
    const data = await api(`/api/sample/${encodeURIComponent(name)}`);
    els.contract.value = data.contract || "";
    els.charCount.textContent = `${els.contract.value.length} chars`;
  } catch (e) { setError(`Could not load sample: ${e.message}`); }
}
async function refreshSamples() {
  try {
    const data = await api("/api/samples");
    for (const name of data.samples || []) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn ghost small-btn";
      btn.textContent = name.replace("_MSA_draft.txt", "");
      btn.addEventListener("click", () => loadSample(name));
      els.sampleBtns.appendChild(btn);
    }
  } catch { /* ignore */ }
}
async function refreshPlaybook() {
  try {
    const pb = await api("/api/playbook");
    const topics = pb.topics || [];
    els.playbookMeta.textContent = `${pb.firm || "Firm"} · ${topics.length} topics`;
    els.playbook.innerHTML = topics.map((t) => {
      const fb = (t.fallbacks || []).map((f) =>
        `<li><b>Fallback:</b> ${esc(f.position)} <span class="muted">(${esc(f.conditions || "no conditions")}; approved by ${esc(f.approved_by || "unrecorded")})</span></li>`
      ).join("");
      const never = (t.never_accept || []).map((n) =>
        `<li><b>Never accept:</b> ${esc(n.position)}</li>`
      ).join("");
      const escWho = t.escalation && (t.escalation.who || t.escalation.when)
        ? `<div class="note"><b>Escalation:</b> ${esc(t.escalation.who || "")} - ${esc(t.escalation.when || "")}</div>` : "";
      return `<details class="topic">
        <summary>${esc(t.topic)}</summary>
        <div class="topic-body">
          <div><b>Standard:</b> ${esc(t.standard_position)}</div>
          ${t.standard_language ? `<div class="lang">${esc(t.standard_language)}</div>` : ""}
          ${fb || never ? `<ul>${fb}${never}</ul>` : ""}
          ${escWho}
        </div>
      </details>`;
    }).join("");
  } catch (e) { els.playbookMeta.textContent = "could not load playbook"; }
}

/* ---------- review rendering ---------- */
function clauseVisible(c) {
  const q = state.query.trim().toLowerCase();
  if (state.filter === "high" ? c.risk !== "high" : state.filter !== "all" && c.disposition !== state.filter) return false;
  if (q && !(`${c.clause} ${c.rationale}`.toLowerCase().includes(q))) return false;
  return true;
}
function cardHTML(c, idx) {
  const d = esc(c.disposition || "escalate");
  const conf = c.confidence != null ? `<span> · conf ${esc(c.confidence)}</span>` : "";
  const risk = c.risk ? `<span class="risk-${esc(c.risk)}">${esc(c.risk)} risk</span>` : "";
  const cites = (c.citations || []).map((x) => `<code>${esc(x)}</code>`).join("");
  const lang = c.proposed_language
    ? `<div class="lang"><b>Proposed language:</b>\n${esc(c.proposed_language)}</div>` : "";
  const diff = (c.disposition === "counter" && c.text && c.proposed_language)
    ? `<details class="diff"><summary>Redline (draft vs proposal)</summary><div>${wordDiff(c.text, c.proposed_language)}</div></details>` : "";
  const note = c.approval_note ? `<div class="note"><b>Approval:</b> ${esc(c.approval_note)}</div>` : "";
  const ev = (c.evidence || []).map((e) =>
    `<blockquote>${esc(e.excerpt)}<cite>${esc(e.citation)}</cite></blockquote>`
  ).join("");
  const evBlock = ev ? `<details class="evidence"><summary>Evidence (${c.evidence.length})</summary>${ev}</details>` : "";
  return `<article class="card clause ${d}" id="clause-${idx}" data-idx="${idx}">
    <h3><span>${esc(c.clause)}</span><span class="disp ${d}">${d}</span></h3>
    <div class="meta-line">${risk}${conf}</div>
    <div>${esc(c.rationale)}</div>
    ${lang}${diff}${note}${evBlock}
    <div class="cites">${cites}</div>
  </article>`;
}
function renderAll() {
  const order = state.clauses.map((c, i) => i).filter((i) => clauseVisible(state.clauses[i]));
  els.results.innerHTML = order.map((i) => cardHTML(state.clauses[i], i)).join("");
  els.nav.innerHTML = state.clauses.map((c, i) => {
    const d = esc(c.disposition || "escalate");
    return `<button type="button" data-i="${i}" class="d-${d}" style="${clauseVisible(c) ? "" : "display:none"}">${esc(c.clause)}</button>`;
  }).join("");
  els.nav.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      els.nav.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const card = $(`clause-${b.dataset.i}`);
      if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
    })
  );
  els.nav.hidden = !state.clauses.length;
  els.filterRow.hidden = !state.clauses.length && !state.streaming;
}
function renderSummary(review) {
  const counts = review.overall_counts || {};
  const risks = review.risk_counts || {};
  els.summaryBar.hidden = false;
  els.summaryText.textContent = review.summary || "";
  els.counts.innerHTML = ["accept", "counter", "escalate"]
    .map((k) => `<b>${counts[k] ?? 0}</b> ${k}`).join(" · ");
  els.riskLine.textContent = `risk: ${risks.high ?? 0} high · ${risks.medium ?? 0} med · ${risks.low ?? 0} low`;
  const usage = review.usage || {};
  const cost = usage.estimated_cost_usd != null ? ` · ~$${usage.estimated_cost_usd}` : "";
  els.costLine.textContent = usage.total_tokens
    ? `${fmtTokens(usage.total_tokens)} tokens${cost} · ${esc(review.model || "")}` : "";
}
function showSkeletons() {
  els.results.innerHTML = [0, 1, 2].map(() =>
    `<div class="card skeleton"><div class="skel" style="width:40%"></div><div class="skel"></div><div class="skel" style="width:70%"></div></div>`
  ).join("");
}

/* ---------- review flow (SSE stream with fallback) ---------- */
async function runReview(contract) {
  setError("");
  state = { clauses: [], total: 0, done: false, filter: state.filter, query: state.query, streaming: true };
  els.empty.hidden = true;
  els.summaryBar.hidden = true;
  els.filterRow.hidden = true;
  els.nav.hidden = true;
  els.reviewBtn.disabled = true;
  els.reviewBtn.textContent = "Reviewing…";
  showSkeletons();
  try {
    await streamReview(contract);
  } catch (e) {
    // SSE unsupported/failed: fall back to the plain endpoint.
    try {
      const review = await api("/api/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contract }),
      });
      finishReview(review);
    } catch (e2) { setError(`Review failed: ${e2.message}`); els.empty.hidden = false; }
  } finally {
    els.reviewBtn.disabled = false;
    els.reviewBtn.textContent = "Review contract";
  }
}
async function streamReview(contract) {
  const res = await fetch("/api/review/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ contract }),
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "", event = "", data = "";
  const flush = () => {
    if (event === "clause" && data) {
      const msg = JSON.parse(data);
      state.clauses[msg.index] = msg.entry;
      state.total = msg.total;
      els.progress.textContent = `${state.clauses.filter(Boolean).length}/${msg.total} clauses…`;
      renderAll();
    } else if (event === "done" && data) {
      finishReview(JSON.parse(data));
    } else if (event === "error" && data) {
      throw new Error(JSON.parse(data).error || "stream error");
    }
    event = ""; data = "";
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      for (const line of buf.slice(0, cut).split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      buf = buf.slice(cut + 2);
      flush();
    }
  }
  if (!state.done) throw new Error("stream ended early");
}
function finishReview(review) {
  state.streaming = false;
  state.done = true;
  state.total = (review.clauses || []).length;
  state.clauses = review.clauses || [];
  els.progress.textContent = "";
  renderSummary(review);
  renderAll();
  els.filterRow.hidden = false;
  refreshHealth();
}

/* ---------- filters / search ---------- */
document.querySelectorAll(".chip").forEach((chip) =>
  chip.addEventListener("click", () => {
    document.querySelectorAll(".chip").forEach((c) => c.classList.remove("on"));
    chip.classList.add("on");
    state.filter = chip.dataset.f;
    renderAll();
  })
);
els.search.addEventListener("input", () => { state.query = els.search.value; renderAll(); });

/* ---------- inputs ---------- */
els.contract.addEventListener("input", () => {
  els.charCount.textContent = `${els.contract.value.length} chars`;
});
els.clearBtn.addEventListener("click", () => {
  els.contract.value = "";
  els.charCount.textContent = "0 chars";
  setError("");
});
els.reviewBtn.addEventListener("click", () => {
  const contract = els.contract.value.trim();
  if (!contract) { setError("Paste a contract draft first."); return; }
  runReview(contract);
});
els.fileInput.addEventListener("change", async () => {
  const file = els.fileInput.files[0];
  if (!file) return;
  setError("");
  els.reviewBtn.disabled = true;
  els.reviewBtn.textContent = "Uploading…";
  try {
    const res = await fetch("/api/review/file", {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream", "X-Filename": file.name },
      body: file,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    els.contract.value = data.preview && data.chars > data.preview.length
      ? data.preview + `\n\n[… ${data.chars} chars total, full text reviewed]`
      : (data.preview || "");
    els.charCount.textContent = `${data.chars} chars (${file.name})`;
    finishReview(data.review);
  } catch (e) { setError(`Upload failed: ${e.message}`); }
  finally {
    els.reviewBtn.disabled = false;
    els.reviewBtn.textContent = "Review contract";
    els.fileInput.value = "";
  }
});

initTheme();
refreshHealth();
refreshSamples();
refreshPlaybook();
setInterval(refreshHealth, 15000);
