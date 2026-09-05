"use strict";
const $ = (id) => document.getElementById(id);
const els = {
  health: $("health"), healthText: $("healthText"),
  contract: $("contract"), charCount: $("charCount"),
  reviewBtn: $("reviewBtn"), clearBtn: $("clearBtn"),
  sampleBtns: $("sampleBtns"),
  error: $("error"), summary: $("summary"),
  summaryText: $("summaryText"), counts: $("counts"),
  results: $("results"), empty: $("empty"),
  playbook: $("playbook"), playbookMeta: $("playbookMeta"),
};

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

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    const mode = h.mode || "live";
    els.health.className = "health " + (mode === "demo" ? "demo" : "ok");
    els.healthText.textContent =
      `${h.status} · ${mode} · ${h.model} · ${h.playbook_topics} topics`;
  } catch (e) {
    els.health.className = "health bad";
    els.healthText.textContent = "server unreachable";
  }
}

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
    const names = data.samples || [];
    for (const name of names) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn ghost small-btn";
      btn.textContent = name.replace("_MSA_draft.txt", "");
      btn.addEventListener("click", () => loadSample(name));
      els.sampleBtns.appendChild(btn);
    }
    // Leave the textarea empty; one click on a sample loads it.
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
  } catch (e) {
    els.playbookMeta.textContent = "could not load playbook";
  }
}

function setError(msg) {
  if (!msg) { els.error.hidden = true; els.error.textContent = ""; return; }
  els.error.hidden = false; els.error.textContent = msg;
}

function renderReview(review) {
  const clauses = review.clauses || [];
  const counts = review.overall_counts || {};
  els.empty.hidden = true;
  els.summary.hidden = false;
  els.summaryText.textContent = review.summary || "";
  els.counts.innerHTML = ["accept", "counter", "escalate"]
    .map((k) => `<b>${counts[k] ?? 0}</b> ${k}`).join(" · ");
  els.results.innerHTML = clauses.map((c) => {
    const d = esc(c.disposition);
    const cites = (c.citations || []).map((x) => `<code>${esc(x)}</code>`).join("");
    const lang = c.proposed_language
      ? `<div class="lang"><b>Proposed language:</b>\n${esc(c.proposed_language)}</div>` : "";
    const note = c.approval_note ? `<div class="note"><b>Approval:</b> ${esc(c.approval_note)}</div>` : "";
    return `<article class="card clause ${d}">
      <h3><span>${esc(c.clause)}</span><span class="disp ${d}">${d}</span></h3>
      <div>${esc(c.rationale)}</div>
      ${lang}${note}
      <div class="cites">${cites}</div>
    </article>`;
  }).join("");
}

els.contract.addEventListener("input", () => {
  els.charCount.textContent = `${els.contract.value.length} chars`;
});

els.clearBtn.addEventListener("click", () => {
  els.contract.value = "";
  els.charCount.textContent = "0 chars";
  setError("");
});

els.reviewBtn.addEventListener("click", async () => {
  const contract = els.contract.value.trim();
  if (!contract) { setError("Paste a contract draft first."); return; }
  setError("");
  els.reviewBtn.disabled = true;
  els.reviewBtn.textContent = "Reviewing…";
  try {
    const review = await api("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contract }),
    });
    renderReview(review);
  } catch (e) {
    setError(`Review failed: ${e.message}`);
  } finally {
    els.reviewBtn.disabled = false;
    els.reviewBtn.textContent = "Review contract";
  }
});

refreshHealth();
refreshSamples();
refreshPlaybook();
setInterval(refreshHealth, 15000);
