# Precedent Repo — Simple Guide

## What this repo is
This is a small legal-AI service for reviewing **MSA contracts**.

It does 2 things:
1. **Builds a negotiation playbook** from a firm's past files (`corpus/`).
2. **Reviews new inbound contracts** (`POST /api/review`) using that playbook.

---

## What problem was asked
The challenge asks you to build a runnable service that:
- Starts fast (within 5 minutes),
- Derives a fresh playbook from the current corpus at startup,
- Reviews contract clauses and gives exactly one disposition per clause:
  - `accept`
  - `counter`
  - `escalate`
- Cites real corpus filenames as evidence,
- Is deterministic (same input -> same result).

---

## What was built here
This implementation delivers:
- A `./start` launcher script,
- An HTTP server with:
  - `GET /` for readiness,
  - `POST /api/review` for clause-by-clause review,
- Playbook generation and persistence in `playbook/playbook.json` and `playbook/PLAYBOOK.md`,
- Review result caching for deterministic repeated responses,
- Offline unit tests for corpus loading, segmentation, review validation/repair, caching, and HTTP behavior.

---

## How it works (flow)
1. `start` installs Python dependencies and runs `server.py`.
2. `server.py` loads credentials and corpus documents.
3. `playbook.py` creates (or reuses) a corpus fingerprinted playbook.
4. `Reviewer` receives contract text, splits it into clauses, calls the LLM, validates/repairs output, fills missing clauses, enforces never-accept rules, and returns JSON.
5. Response includes per-clause disposition, rationale, citations, and optional counter language.

---

## File-by-file guide

### Core app files
- `server.py` — service bootstrap + HTTP API handlers.
- `playbook.py` — Stage 1 logic: build/load/save playbook.
- `reviewer.py` — Stage 2 logic: clause extraction, LLM review, strict disposition shaping, caching.
- `corpus.py` — reads corpus docs (`.txt`, `.md`, `.csv`, `.pdf`, `.xlsx`) into normalized text docs.
- `llm.py` — OpenAI-compatible client, model discovery/rotation, retry handling, JSON extraction.
- `config.py` — paths, env loading (`.env`), credentials, port.

### Run / validation / dependencies
- `start` — executable entrypoint expected by evaluator.
- `requirements.txt` — Python dependencies.
- `validate.sh` — end-to-end contract checker used for grading shape.
- `tests/test_offline.py` — local tests without external API calls.

### Inputs and artifacts
- `corpus/` — source-of-truth historical legal files used to infer policy.
- `inbound/` — sample incoming drafts to review.
- `playbook/playbook.json` — machine-readable generated playbook artifact.
- `playbook/PLAYBOOK.md` — human-readable generated playbook artifact.
- `review_cache/` (runtime) — cached review results by fingerprint + contract hash.

### Other files in repo root
- `_verify.py`, `_verify2.py`, `_rerender.py` — local helper/debug scripts.
- `precedent_submission.zip` — packaged submission artifact.

---

## Why this structure is necessary
- **Separation of stages** keeps generation (policy extraction) and review (decisioning) clean.
- **Citations + strict dispositions** satisfy legal-review expectations and challenge hard rules.
- **Fingerprint + cache** supports determinism and performance on repeat runs.
- **Generated playbook artifacts** make reasoning auditable outside runtime.

---

## In one line
You built a deterministic legal contract-review microservice that learns a firm’s negotiation behavior from historical files and applies it clause-by-clause to new drafts with evidence-backed decisions.
