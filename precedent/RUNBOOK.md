# Precedent — how to run

1. Put your credentials in a `.env` file next to `server.py` (or export them):

   ```
   LITMUS_AI_BASE_URL=<base url from the assessment page>
   LITMUS_AI_API_KEY=<key from the assessment page>
   ```

2. `./start` creates a virtualenv, installs `requirements.txt`, derives the
   playbook from `./corpus` into `./playbook/` (JSON + markdown), and serves
   HTTP on `$PORT` (default 8000).

3. Endpoints:
   - `GET /` → readiness + playbook fingerprint.
   - `POST /api/review` with `{"contract": "<draft text>"}` → per-clause
     dispositions (`accept` / `counter` / `escalate`), each with rationale,
     proposed language for counters, approval notes, and corpus citations.
     Reviews are cached by (playbook fingerprint, contract hash), so the same
     draft always returns the same dispositions.

4. Offline tests (no credentials needed):

   ```
   python -m unittest discover -s tests
   ```
