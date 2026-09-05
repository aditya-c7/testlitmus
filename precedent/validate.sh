#!/usr/bin/env bash
# Checks your deliverable against the contract in the README.
# Usage: ./validate.sh
# Grading runs this same shape of check first.
set -u

PORT="${PORT:-8641}"
BOOT_LIMIT=300
REVIEW_LIMIT=180
SRV_PID=""
WORK=""
ORIG="$PWD"

cleanup() {
  [ -n "$SRV_PID" ] && { kill "$SRV_PID" 2>/dev/null; wait "$SRV_PID" 2>/dev/null; }
  # Stage 1 writes ./playbook inside the staging copy, which is about to be
  # deleted - hand it back so you can read what your agent actually produced.
  # It is part of the deliverable, not just a validation side effect.
  if [ -n "$WORK" ] && [ -d "$WORK/playbook" ]; then
    rm -rf "$ORIG/playbook" && cp -R "$WORK/playbook" "$ORIG/playbook" 2>/dev/null \
      && echo "(./playbook copied back from the validation run)"
  fi
  [ -n "$WORK" ] && rm -rf "$WORK"
}
trap cleanup EXIT
fail() { printf "FAIL: %b\n" "$1"; exit 1; }

[ -f ./start ] || fail "./start not found at the package root"
[ -x ./start ] || fail "./start is not executable (chmod +x start)"

# Validate a COPY with installed-dependency directories removed, because that is
# what grading actually runs. `litmus submit` never uploads node_modules, venv,
# .venv, env, build, dist, target, .gradle, .idea or __pycache__ - they are large
# and machine-specific - and the grading sandbox installs nothing for you. So a
# ./start that assumes its dependencies are already on disk passes here in your
# working directory and then fails at grading with a module-not-found error.
# Checking the copy is the only way this script can tell you that in advance.
#
# Your ./start is what installs them: the README asks for a start script "that
# can handle whatever your system needs to build and run". `npm ci`, `pip install
# -r requirements.txt`, `go mod download` - whatever your stack needs, before it
# serves.
STRIP_DIRS="node_modules __pycache__ venv .venv env target build dist .gradle .idea"
WORK="$(mktemp -d)"
TAR_EXCLUDES=""
for d in $STRIP_DIRS; do TAR_EXCLUDES="$TAR_EXCLUDES --exclude=$d"; done
# shellcheck disable=SC2086
tar -cf - $TAR_EXCLUDES . 2>/dev/null | (cd "$WORK" && tar -xf -) \
  || fail "could not stage a clean copy for validation"
for d in $STRIP_DIRS; do
  if [ -e "./$d" ]; then
    echo "NOTE: ./$d exists here but is NOT uploaded - validating without it."
  fi
done
cd "$WORK" || fail "could not enter the staging copy"
# Say where, so a stack trace pointing at a temp path is not a mystery.
echo "Validating a clean copy of what gets uploaded, staged at: $WORK"
[ -x ./start ] || chmod +x ./start 2>/dev/null

# Load .env if present, then warn (not fail) if AI credentials are missing.
# Demo mode needs no keys; live LLM mode uses OPENAI_* (LITMUS_* accepted).
[ -f ./.env ] && set -a && . ./.env && set +a
if [ -n "${OPENAI_BASE_URL:-}" ] && [ -n "${OPENAI_API_KEY:-}" ]; then
  echo "AI credentials present (live mode)"
elif [ -n "${LITMUS_AI_BASE_URL:-}" ] && [ -n "${LITMUS_AI_API_KEY:-}" ]; then
  echo "AI credentials present via LITMUS_* (live mode)"
else
  echo "WARN: no AI credentials set - running in DEMO_MODE (offline heuristic, still valid)"
  export DEMO_MODE=true
fi

rm -rf ./playbook
echo "Starting: PORT=$PORT ./start   (boot limit ${BOOT_LIMIT}s, includes Stage 1 playbook generation)"
PORT="$PORT" ./start &
SRV_PID=$!

START=$(date +%s)
READY=""
while [ $(( $(date +%s) - START )) -lt "$BOOT_LIMIT" ]; do
  kill -0 "$SRV_PID" 2>/dev/null || fail "./start exited before becoming ready.\n      If it ran fine outside this script, the cause is almost always a missing\n      dependency: grading gets your source, not your installed packages, so\n      ./start has to install them itself before it serves."
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/"; then READY=1; break; fi
  sleep 2
done
[ -n "$READY" ] || fail "GET / did not return 200 within ${BOOT_LIMIT}s"
echo "OK: ready in $(( $(date +%s) - START ))s"

[ -d ./playbook ] && [ -n "$(ls -A ./playbook 2>/dev/null)" ] || \
  fail "./playbook/ is missing or empty after boot - Stage 1 must write the playbook artifact"
echo "OK: ./playbook/ exists and is non-empty:"
find ./playbook -maxdepth 2 | head -10

SMOKE='This Master Services Agreement is made between Example Client Inc. ("Client") and the vendor ("Supplier"). 1. Fees and Payment. Client will pay undisputed fees within forty-five (45) days of the date of each invoice. 2. Governing Law. This Agreement is governed by the laws of the State of Delaware.'
echo "POST /api/review (smoke draft, limit ${REVIEW_LIMIT}s)"
RESP="$(mktemp)"
curl -sf --max-time "$REVIEW_LIMIT" -X POST "http://127.0.0.1:$PORT/api/review" \
  -H 'Content-Type: application/json' \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"contract": sys.argv[1]}))' "$SMOKE")" \
  -o "$RESP" || fail "POST /api/review failed or exceeded ${REVIEW_LIMIT}s"

# The review format is yours (see README) - this only checks one came back.
[ -s "$RESP" ] || fail "POST /api/review returned an empty response - the review is the product"
echo "OK: review returned ($(wc -c < "$RESP" | tr -d ' ') bytes). First lines:"
head -c 400 "$RESP"; echo
grep -qiE "accept|counter|escalate" "$RESP" || \
  echo "WARN: no disposition vocabulary (accept/counter/escalate) found in the review - see the Hard rules"

kill "$SRV_PID" 2>/dev/null
echo "OK: contract satisfied."
