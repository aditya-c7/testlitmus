"""In-process verification: boots the real service (demo mode) and checks everything."""
import json
import os
import sys
import threading

os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("PORT", "8799")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "precedent"))

import config  # noqa: E402
import corpus  # noqa: E402
import playbook as playbook_module  # noqa: E402
import reviewer as reviewer_module  # noqa: E402
from reviewer import Reviewer, segment_clauses  # noqa: E402
from server import ThreadingHTTPServer, make_handler, PrecedentService  # noqa: E402

import httpx  # noqa: E402

failures = []


def check(label, fn):
    try:
        result = fn()
        print(f"PASS {label}: {result}", flush=True)
    except Exception as exc:
        failures.append(label)
        print(f"FAIL {label}: {exc}", flush=True)


service = PrecedentService()
print(f"health={service.health()}", flush=True)
print(f"playbook topics={len(service.playbook.get('topics', []))}", flush=True)

server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"

check("GET / json health", lambda: httpx.get(f"{base}/", timeout=10).json()["status"])
check(
    "GET / html UI",
    lambda: "Review contract" in httpx.get(f"{base}/", headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"}, timeout=10).text,
)
check("GET /app.js", lambda: httpx.get(f"{base}/app.js", timeout=10).status_code)
check("GET /styles.css", lambda: httpx.get(f"{base}/styles.css", timeout=10).status_code)
check("GET /api/health", lambda: httpx.get(f"{base}/api/health", timeout=10).json()["mode"])
check("GET /api/samples", lambda: httpx.get(f"{base}/api/samples", timeout=10).json()["samples"])
check("GET /api/playbook", lambda: len(httpx.get(f"{base}/api/playbook", timeout=10).json().get("topics", [])))


def review_text(name):
    contract = (config.ROOT / "inbound" / name).read_text(encoding="utf-8")
    resp = httpx.post(f"{base}/api/review", json={"contract": contract}, timeout=120)
    assert resp.status_code == 200, resp.text[:300]
    review = resp.json()
    clauses = review.get("clauses", [])
    assert clauses, "no clauses"
    known = {d.citation for d in service.documents}
    empty = [c["clause"] for c in clauses if not c.get("citations")]
    bad_disp = [c for c in clauses if c.get("disposition") not in ("accept", "counter", "escalate")]
    bad_cite = [c for c in clauses for c in c.get("citations", []) if c not in known]
    no_lang = [c["clause"] for c in clauses if c.get("disposition") == "counter" and not (c.get("proposed_language") or "").strip()]
    assert not bad_disp, f"bad dispositions {bad_disp}"
    assert not empty, f"empty citations {empty}"
    assert not bad_cite, f"bad citations {bad_cite}"
    assert not no_lang, f"counter w/o language {no_lang}"
    # determinism
    resp2 = httpx.post(f"{base}/api/review", json={"contract": contract}, timeout=120)
    assert resp2.json() == review, "non-deterministic"
    return f"{len(clauses)} clauses ok"


check("review Windrow", lambda: review_text("Windrow_MSA_draft.txt"))
check("review Marchetti", lambda: review_text("Marchetti_MSA_draft.txt"))

smoke = 'This MSA is between Example Client and Supplier. 1. Fees and Payment. Client will pay within 45 days. 2. Governing Law. Governed by Delaware law.'
check("review smoke", lambda: f"{len(httpx.post(f'{base}/api/review', json={'contract': smoke}, timeout=60).json()['clauses'])} clauses")
check("reject empty", lambda: httpx.post(f"{base}/api/review", json={}, timeout=10).status_code)
check("404 unknown", lambda: httpx.get(f"{base}/nope", timeout=10).status_code)

# segmentation sanity
clauses = segment_clauses((config.ROOT / "inbound" / "Marchetti_MSA_draft.txt").read_text(encoding="utf-8"))
print(f"Marchetti segments: {len(clauses)}", flush=True)

server.shutdown()
if failures:
    print(f"\n{len(failures)} FAILURES: {failures}", flush=True)
    sys.exit(1)
print("\nALL CHECKS PASSED", flush=True)
