# Precedent — legal playbook + contract review (localhost web app)

A small RAG-style legal assistant:

1. **Stage 1 — build playbook:** reads every file in `precedent/corpus/` (standard form, executed deals, redlines, memos, approvals log, clause matrix) and derives how the firm actually negotiates. Writes `precedent/playbook/playbook.json` + `PLAYBOOK.md`.
2. **Stage 2 — review drafts:** `POST /api/review` with `{"contract": "..."}` returns a per-clause verdict — exactly one of `accept` / `counter` / `escalate` — with rationale, proposed firm language for counters, approval notes, and corpus filename citations.
3. **Web UI:** open `http://127.0.0.1:8000/` — paste a draft, load a sample, review, browse results. Same Python process serves UI + API.

## Run on Windows (localhost)

```powershell
# PowerShell (recommended)
.\start.ps1
# or
cd precedent; .\start.ps1
```

```bat
REM cmd.exe
start.bat
```

Then open **http://127.0.0.1:8000/**.

On Git Bash / Linux / macOS:

```bash
cd precedent && chmod +x start && ./start
```

## Configuration

Copy `precedent/.env.example` to `precedent/.env`:

| Var | Meaning |
|---|---|
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | Any OpenAI-compatible endpoint. `LITMUS_AI_*` also accepted. |
| `OPENAI_MODEL` | Optional model pin. Otherwise auto-discovered. |
| `DEMO_MODE=true` | Force offline heuristic (no LLM calls). Auto-enabled when no keys are set. |
| `PORT` | Default `8000`. |

No keys? The app still works in **demo mode**: it reuses the committed `playbook.json` and runs a deterministic heuristic reviewer so every clause still gets a valid cited disposition.

## API

- `GET /` → web UI (`index.html`); falls back to JSON health if `web/` missing.
- `GET /api/health` → `{status, mode, model, playbook_fingerprint, playbook_topics, corpus_documents}`.
- `GET /api/playbook` → full playbook JSON.
- `GET /api/samples` → inbound sample names; `GET /api/sample/<name>` → draft text.
- `POST /api/review` → `{summary, overall_counts, clauses[], playbook_fingerprint}`.

## Layout

```
precedent/
  corpus/     firm files (source of truth)
  inbound/    sample counterparty drafts
  playbook/   generated artifact (committed so demo works)
  web/        localhost UI (index.html, app.js, styles.css)
  server.py   HTTP service + static UI
  reviewer.py clause split + review + guardrails + heuristic fallback
  playbook.py playbook build/reuse/persist
  corpus.py   txt/csv/pdf/xlsx ingestion
  llm.py      OpenAI-compatible client
  config.py   env + paths + demo mode
```

## Tests

```powershell
cd precedent
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Notes

- Corpus documents are adapted from Common Paper / Bonterms (CC BY 4.0) with modifications.
- Every disposition cites real corpus filenames; unknown clauses escalate; same draft → same result (disk cache in `review_cache/`).
