# Precedent — how to run (localhost)

## 1. Configure (optional)

Copy `.env.example` to `.env` next to `server.py`:

```
OPENAI_BASE_URL=https://your-provider/v1
OPENAI_API_KEY=sk-...
```

Leave it empty to run in **demo mode** (no network, deterministic heuristic).

You can also export in PowerShell:

```powershell
$env:OPENAI_BASE_URL="https://your-provider/v1"
$env:OPENAI_API_KEY="sk-..."
$env:PORT="8000"
```

## 2. Start

```powershell
.\start.ps1        # PowerShell
# or: start.bat    # cmd.exe
# or (Git Bash): ./start
```

This creates `.venv`, installs `requirements.txt`, builds/reuses the playbook
from `./corpus` into `./playbook/`, and serves HTTP on `$PORT` (default 8000).

## 3. Use

- Browser: `http://127.0.0.1:8000/`
- Health: `GET /api/health`
- Review: `POST /api/review` with `{"contract": "<draft text>"}`

Example:

```powershell
$body = @{ contract = (Get-Content -Raw .\inbound\Windrow_MSA_draft.txt) } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/review -Method Post -ContentType "application/json" -Body $body |
  ConvertTo-Json -Depth 6 | Out-File review.json
```

## 4. Test

```
python -m unittest discover -s tests -v
```
