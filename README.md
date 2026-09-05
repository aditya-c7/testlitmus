# Precedent

Every law firm has that one senior lawyer who just knows. "We never sign that clause." "We gave in on this once, but only because a partner signed off." The problem is that knowledge lives in people's heads.

Precedent pulls it out of your old paperwork instead.

**The idea is simple:**

1. **Feed it your past deals.** Agreements, redlines, memos, all of it. It works out your standard positions, which concessions you have actually approved (and who approved them), and what you refuse point blank.
2. **Throw new drafts at it.** Paste in a counterparty contract and it grades every clause: **accept**, **counter** (it suggests your own fallback wording), or **escalate** to a human. Each call cites the real files behind it.

Nothing is hardcoded. Give it a different firm's folder and it learns different habits.

## Getting started

**1. Get a key.** The app talks to models through [OpenRouter](https://openrouter.ai/) (one key unlocks pretty much every model). Grab one at [openrouter.ai/keys](https://openrouter.ai/keys), then:

```powershell
cd precedent
copy .env.example .env   # open .env and paste your key
```

It should look like this:

```
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-...
OPENAI_MODEL=gpt-4o-mini
```

No key handy? It still starts up. It falls back to an offline demo mode with a built-in reviewer, so you can click around.

**2. Start it:**

```powershell
.\start.ps1
```

**3. Open http://127.0.0.1:8000/.** Click one of the sample drafts, hit Review contract, and open the Playbook panel to see what the firm believes.

## API

| Endpoint | Does what |
|---|---|
| `GET /api/health` | uptime, live or demo mode, model, playbook stats |
| `GET /api/playbook` | the whole derived playbook as JSON |
| `GET /api/samples` | names of the sample drafts |
| `POST /api/review` | send `{"contract": "..."}` and get back per-clause verdicts |

Quick try:

```powershell
$body = @{ contract = (Get-Content -Raw .\precedent\inbound\Windrow_MSA_draft.txt) } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/review -Method Post -ContentType "application/json" -Body $body
```

## What's where

```
precedent/
  corpus/     old firm paperwork, this is the source of truth
  inbound/    a couple of sample drafts to test with
  playbook/   the generated artifact (checked in, so demo mode works)
  web/        the UI, plain HTML and JS, no build step
  server.py   one process serves the UI and the API
```

## Tests

```powershell
cd precedent
python -m unittest discover -s tests -v
python ..\scripts\verify.py
```

*The sample documents borrow from Common Paper / Bonterms (CC BY 4.0) and were edited for this project.*
