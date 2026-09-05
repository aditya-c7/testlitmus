# Precedent

Your firm's negotiation memory — turned into software.

Law firms argue the same clauses over and over: payment terms, liability caps, governing law. Senior lawyers carry the answers in their heads — *"we never sign that," "we caved on this once, but only with partner approval."* Precedent bottles that judgment.

**How it works:**

1. **It studies your past deals.** Point it at a folder of old agreements, redlines, and memos. It figures out your standard positions, the concessions you've actually approved (and who approved them), and the terms you refuse in any form.
2. **It reviews new drafts against that playbook.** Paste in a counterparty's contract and every clause gets one clear verdict — **accept**, **counter** (with your own fallback language), or **escalate** to a lawyer — each backed by citations to your real files.

No hardcoded rules. Swap in a different firm's folder and it learns different positions.

## Run it (2 minutes)

**1. Get a key** — this project uses [OpenRouter](https://openrouter.ai/) as its LLM backend (one key, hundreds of models). Grab a key at [openrouter.ai/keys](https://openrouter.ai/keys), then:

```powershell
cd precedent
copy .env.example .env   # then paste your key into OPENAI_API_KEY
```

Your `.env` looks like this:

```
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-...
OPENAI_MODEL=gpt-4o-mini   # cheap and good at structured output
```

No key? It still runs — in offline **demo mode** with a built-in heuristic reviewer.

**2. Start it:**

```powershell
.\start.ps1        # PowerShell, from the repo root or precedent/
```

**3. Open http://127.0.0.1:8000/** — click a sample draft (Windrow, Marchetti), hit **Review contract**, and expand the **Playbook** panel to see what the firm believes.

## API

| Endpoint | What |
|---|---|
| `GET /api/health` | status, mode (`live`/`demo`), model, playbook stats |
| `GET /api/playbook` | the full derived playbook as JSON |
| `GET /api/samples` | sample draft names |
| `POST /api/review` | `{"contract": "..."}` → per-clause verdicts |

```powershell
$body = @{ contract = (Get-Content -Raw .\precedent\inbound\Windrow_MSA_draft.txt) } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/review -Method Post -ContentType "application/json" -Body $body
```

## Layout

```
precedent/
  corpus/     the firm's past work (source of truth)
  inbound/    sample drafts to test against
  playbook/   generated artifact (committed, so demo mode works)
  web/        the UI — plain HTML/JS, no build step
  server.py   one process serves UI + API
```

## Tests

```powershell
cd precedent
python -m unittest discover -s tests -v
python ..\scripts\verify.py   # full end-to-end check
```

*Corpus documents adapted from Common Paper / Bonterms (CC BY 4.0), modified.*
