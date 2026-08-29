# Precedent Repository — Detailed Explanation (Simple Language)

## 1) What this repo is
This repository is a **mini legal contract-review product**.

Its goal is to imitate how a law firm reviews and negotiates Master Services Agreements (MSAs), using the firm’s own historical files as evidence.

So instead of “generic legal advice,” this system does:
- learn this specific firm’s negotiation behavior from old documents, then
- apply that behavior to a new incoming draft contract.

---

## 2) What question/problem was asked in the challenge
The challenge asks for a service with **two required stages**:

### Stage 1: Build a playbook from `corpus/`
At startup, the system must read all provided firm files (template, signed deals, redlines, memos, approvals log, clause matrix) and derive how the firm actually negotiates clause-by-clause:
- standard position,
- accepted fallbacks,
- never-accept rules,
- escalation/approval rules,
- conflicts between old and new guidance.

Then it must write that playbook to `./playbook/` as an artifact.

### Stage 2: Review a new inbound contract
Via HTTP API, the system receives contract text and must return clause-level decisions with **exactly one disposition per clause**:
- `accept`
- `counter`
- `escalate`

And every decision must cite corpus filenames as evidence.

### Hard constraints
- Startup + playbook generation must complete within 5 minutes.
- Review response must complete within 3 minutes.
- Deterministic behavior: same draft => same dispositions.
- No hardcoded firm positions; must derive from current corpus.

---

## 3) What has been built here (high-level)
This implementation provides:

1. **Executable bootstrap**: `./start`.
2. **HTTP service** in `server.py`:
   - `GET /` readiness
   - `POST /api/review` contract review
3. **Corpus ingestion pipeline** in `corpus.py` for `.txt`, `.md`, `.csv`, `.pdf`, `.xlsx`.
4. **Playbook engine** in `playbook.py`:
   - generate playbook using LLM,
   - sanitize citations,
   - save JSON + Markdown artifacts,
   - reuse if corpus unchanged (fingerprint).
5. **Review engine** in `reviewer.py`:
   - clause segmentation,
   - LLM review generation,
   - strict validation/repair,
   - fill missing clause outputs,
   - enforce strong “never accept” overrides,
   - deterministic cache.
6. **LLM wrapper** in `llm.py` with model discovery, ranking, retries, and JSON extraction.
7. **Offline unit tests** in `tests/test_offline.py`.

---

## 4) End-to-end runtime flow (exact sequence)

1. `./start` runs.
2. It ensures Python env/dependencies (`pip install -r requirements.txt`).
3. It executes `server.py`.
4. `server.py` initializes `PrecedentService`:
   - reads env credentials (`config.credentials()`),
   - creates `LLMClient`,
   - loads all corpus docs (`corpus.load_corpus()`),
   - loads or builds playbook (`playbook.load_or_build()`),
   - creates `Reviewer` with documents + playbook + fingerprint.
5. Service listens on `$PORT` (default `8000`).
6. `GET /` returns readiness JSON.
7. `POST /api/review` takes `{"contract": "..."}` and returns review JSON.

---

## 5) Stage 1 in depth: How playbook generation works

## 5.1 Corpus reading
`corpus.py` recursively scans `corpus/` and creates `Document` objects:
- `citation` = relative filename used in evidence,
- `category` = folder name,
- `text` = extracted text.

Supported parsing:
- `.txt`, `.md`: direct read
- `.csv`: rows flattened with `|`
- `.pdf`: extracted page text using `pypdf`
- `.xlsx/.xls`: all sheets and rows flattened with `openpyxl`

Empty docs are skipped.

## 5.2 Fingerprint and reuse
`playbook.corpus_fingerprint()` hashes every citation + document text and produces a short fingerprint.

`load_or_build()` behavior:
- if `playbook/playbook.json` exists and fingerprint matches: reuse existing playbook,
- otherwise: build a new playbook from current corpus.

This avoids rebuilding on unchanged corpus and helps deterministic behavior.

## 5.3 LLM prompt design for playbook
`PLAYBOOK_SYSTEM` + `PLAYBOOK_USER_TEMPLATE` instruct the model to:
- derive positions only from provided files,
- prefer actual executed deals and recent evidence,
- include conflicts,
- use exact filename citations,
- return strict JSON structure.

## 5.4 Post-processing / guardrails
After LLM output, `build()` sanitizes evidence fields through `_keep_known()` so only known corpus citations remain.

## 5.5 Persisted artifacts
`persist()` writes:
- `playbook/playbook.json` (machine-readable)
- `playbook/PLAYBOOK.md` (human-readable)

`render_markdown()` converts each topic to readable sections:
- standard position/language,
- approved fallbacks,
- never-accept rules,
- escalation,
- conflicts,
- notes.

---

## 6) Stage 2 in depth: How review generation works

## 6.1 API validation
`server.py` validates:
- endpoint path,
- JSON body correctness,
- non-empty `contract` string.

Invalid input returns `400` with explicit error.

## 6.2 Clause segmentation
`reviewer.segment_clauses()` attempts to split by numbered headings (e.g. `8. LIMITATION OF LIABILITY`).

If no proper headings are found, it falls back to one clause: `Contract`.

## 6.3 Deterministic cache
Before calling LLM, reviewer checks cache path based on:
- playbook fingerprint,
- contract text hash.

If cache exists, response is returned immediately.

## 6.4 LLM review prompt design
The review prompt includes:
- allowed corpus filenames,
- full playbook JSON,
- pre-split clauses,
- strict output schema and rules.

The model is explicitly forced to choose exactly one disposition per clause:
- `accept`, `counter`, or `escalate`.

## 6.5 Validation and repair
`_validated()` accepts only entries that satisfy rules:
- valid disposition,
- non-empty rationale,
- `counter` must include `proposed_language`,
- citations must be from known corpus files.

`_repair()` makes a second LLM pass if entries are broken or uncited.

## 6.6 Coverage guarantees
`_cover_gaps()` ensures every input clause has an output entry.
Missing ones become `escalate` with a safe rationale.

## 6.7 Never-accept override
`_force_never_accept()` applies hard escalation when playbook marks a topic as never accepted “in any form/any kind.”
If matched, it overrides non-escalate outputs to `escalate`, appends supporting evidence, and adds approval note.

## 6.8 Final response shape
`_compose()` returns:
- `summary`
- `overall_counts` per disposition
- `clauses` array
- `playbook_fingerprint`

---

## 7) LLM subsystem details (`llm.py`)

- Discovers models from `base_url/models` (or `/v1/models`).
- Ranks available models using preference order.
- Uses temperature `0` for consistency.
- Retries failures with exponential backoff and model rotation.
- Parses model text and extracts first JSON object/array safely.

Why this matters:
- better robustness to model endpoint issues,
- higher determinism,
- strict machine-parseable output for downstream logic.

---

## 8) Determinism strategy (important)
Determinism is achieved through multiple layers:
1. Temperature fixed at 0.
2. Strict schemas and post-validation.
3. Coverage repair for missing clauses.
4. Never-accept hard override.
5. Cache keyed by playbook fingerprint + contract hash.

So repeated same input under same playbook tends to return the same result exactly.

---

## 9) Validation and testing

## 9.1 Offline tests (`tests/test_offline.py`)
Covers:
- corpus loading of all file types,
- PDF/XLSX readability,
- clause segmentation behavior,
- reviewer validation + repair logic,
- full clause coverage,
- cache behavior,
- HTTP endpoint correctness.

## 9.2 End-to-end validator (`validate.sh`)
Checks expected contract with a clean staged copy:
- executable `start`,
- boot readiness timeout,
- non-empty `playbook/` generated,
- successful `/api/review` response,
- rough disposition vocabulary presence.

---

## 10) Folder and file purpose (complete map)

### Repository root
- `.gitignore` — ignores envs, caches, tool state.
- `_verify.py`, `_verify2.py` — local helper verification scripts.
- `_rerender.py` — rerenders playbook markdown from JSON.
- `precedent_submission.zip` — submission package.
- `precedent/` — main deliverable project.

### `precedent/` core
- `README.md` — challenge description and requirements.
- `RUNBOOK.md` — quick run instructions.
- `LITMUS-AI-NOTICE.md` — capture/tracking note.
- `config.py` — env + path + credential configuration.
- `corpus.py` — corpus document ingestion.
- `llm.py` — model API client and JSON completion utilities.
- `playbook.py` — playbook generation/persistence/rendering.
- `reviewer.py` — contract review engine and enforcement logic.
- `server.py` — web service entrypoint and handlers.
- `start` — executable startup script.
- `requirements.txt` — dependencies.
- `validate.sh` — local contract validator.
- `.gitignore` — project-level ignore rules.
- `tests/test_offline.py` — unit tests.

### `precedent/corpus/` data sources
- `template/` — standard form baseline language.
- `deals/` — executed agreements (actual accepted outcomes).
- `redlines/` — negotiation turn examples.
- `memos/` — internal guidance and policy rationale.
- `policies/` — approvals log + older clause matrix.

### `precedent/inbound/`
- sample incoming drafts used to test review behavior.

### `precedent/playbook/`
- generated output artifact consumed by humans/evaluation.

### `precedent/review_cache/`
- runtime cache for deterministic replay and speed.

---

## 11) Why each major design choice is necessary
- **Corpus-driven derivation**: ensures positions are firm-specific, not generic.
- **Artifacts on disk**: makes outputs auditable and inspectable.
- **Strict dispositions**: directly matches evaluator hard rules.
- **Citation filtering**: prevents hallucinated evidence references.
- **Repair + coverage**: avoids incomplete/malformed review output.
- **Never-accept override**: enforces high-risk policy boundaries.
- **Cache + fingerprint**: balances correctness, speed, and repeatability.

---

## 12) Practical meaning: what you built in plain words
You built an AI contract-review service that behaves like a firm’s negotiation memory:
- it learns the firm’s real historic positions,
- turns those into a reusable playbook,
- and applies that playbook to new contracts with clear, evidence-backed accept/counter/escalate decisions per clause.
