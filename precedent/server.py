"""Precedent - legal playbook + contract review service with localhost web UI."""
import json
import mimetypes
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import config
import corpus
import playbook as playbook_module
from reviewer import Reviewer

try:
    from llm import estimate_cost_usd
except ImportError:  # very old checkout; cost meter just stays empty
    def estimate_cost_usd(model, usage):
        return None


class _NoLLM:
    """Stub used in demo mode (no API key): forces heuristic review."""

    def complete_json(self, system, user, max_tokens=8000):
        raise RuntimeError("demo mode: no LLM configured")


class PrecedentService:
    def __init__(self):
        self.demo = config.demo_mode()
        self.llm = None
        self.model_name = "demo-heuristic"
        if not self.demo:
            try:
                from llm import LLMClient

                base_url, api_key = config.credentials()
                self.llm = LLMClient(base_url, api_key, model=config.model_override())
                self.model_name = self.llm.model
                print(f"[precedent] model resolved: {self.llm.model}", file=sys.stderr, flush=True)
            except Exception as exc:
                print(f"[precedent] LLM init failed, falling back to demo mode: {exc}", file=sys.stderr, flush=True)
                self.demo = True
                self.llm = None
        if self.llm is None:
            self.llm = _NoLLM()
            self.model_name = "demo-heuristic"
        self.documents = corpus.load_corpus(config.CORPUS_DIR)
        print(f"[precedent] loaded {len(self.documents)} corpus documents", file=sys.stderr, flush=True)
        self._pb_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self.started_at = time.time()
        self._metrics = {
            "reviews": 0,
            "cache_hits": 0,
            "accepts": 0,
            "counters": 0,
            "escalates": 0,
            "total_latency_s": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "estimated_cost_usd": 0.0,
        }
        fingerprint = playbook_module.corpus_fingerprint(self.documents)
        exact = playbook_module._read_existing(fingerprint)
        if exact is not None:
            self._set_playbook(exact, fingerprint, "ready")
        else:
            stale = playbook_module._read_stale()
            if stale is None:
                # First boot ever with no playbook: build now (may raise).
                playbook, fingerprint = playbook_module.load_or_build(
                    self.documents, None if self.demo else self.llm
                )
                self._set_playbook(playbook, fingerprint, "ready")
            elif self.demo:
                self._set_playbook(stale, stale.get("fingerprint", "stale"), "stale")
            else:
                # Serve the stale playbook now, rebuild in the background.
                self._set_playbook(stale, stale.get("fingerprint", "stale"), "building")
                thread = threading.Thread(target=self._rebuild_playbook, daemon=True)
                thread.start()
        print(
            f"[precedent] playbook ready (fingerprint {self.fingerprint}, "
            f"{len(self.playbook.get('topics', []))} topics, demo={self.demo})",
            file=sys.stderr,
            flush=True,
        )

    def _set_playbook(self, playbook: dict, fingerprint: str, state: str) -> None:
        with self._pb_lock:
            self.playbook = playbook
            self.fingerprint = fingerprint
            self.playbook_state = state
            llm = self.llm
            model = self.model_name
        self.reviewer = Reviewer(llm, self.documents, playbook, fingerprint, model=model)

    def _rebuild_playbook(self) -> None:
        try:
            playbook, fingerprint = playbook_module.load_or_build(self.documents, self.llm)
        except Exception as exc:
            print(f"[precedent] background playbook build failed: {exc}", file=sys.stderr, flush=True)
            with self._pb_lock:
                self.playbook_state = "stale"
            return
        self._set_playbook(playbook, fingerprint, "ready")
        print(f"[precedent] background playbook build finished ({fingerprint})", file=sys.stderr, flush=True)

    def apply_credentials(self, base_url: str, api_key: str, model: str = "") -> None:
        """Hot-swap the LLM client (setup wizard). Raises on bad credentials."""
        from llm import LLMClient

        client = LLMClient(base_url, api_key, model=model or config.model_override())
        with self._pb_lock:
            self.llm = client
            self.demo = False
            self.model_name = client.model
            playbook = self.playbook
            fingerprint = self.fingerprint
        self.reviewer = Reviewer(client, self.documents, playbook, fingerprint, model=client.model)
        print(f"[precedent] credentials applied, model {client.model}", file=sys.stderr, flush=True)

    def health(self) -> dict:
        with self._pb_lock:
            state = getattr(self, "playbook_state", "ready")
        return {
            "status": "ready",
            "service": "precedent",
            "mode": "demo" if self.demo else "live",
            "model": self.model_name,
            "playbook_fingerprint": self.fingerprint,
            "playbook_topics": len(self.playbook.get("topics", [])),
            "playbook_state": state,
            "corpus_documents": len(self.documents),
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    def metrics(self) -> dict:
        with self._metrics_lock:
            snapshot = dict(self._metrics)
        reviews = snapshot["reviews"] or 0
        snapshot["avg_latency_s"] = round(snapshot["total_latency_s"] / reviews, 2) if reviews else 0.0
        snapshot["cache_hit_rate"] = round(snapshot["cache_hits"] / reviews, 3) if reviews else 0.0
        snapshot["estimated_cost_usd"] = round(snapshot["estimated_cost_usd"], 4)
        return snapshot

    def handle_review(self, contract_text: str) -> dict:
        reviewer = self.reviewer
        hit = reviewer._read_cache(contract_text) is not None
        started = time.time()
        try:
            review = reviewer.review(contract_text)
        except Exception as exc:
            print(f"[precedent] review failed: {exc}", file=sys.stderr, flush=True)
            raise
        self._record(contract_text, review, time.time() - started, hit)
        return review

    def _record(self, contract_text: str, review: dict, latency: float, hit: bool) -> None:
        counts = review.get("overall_counts", {}) or {}
        usage = review.get("usage", {}) or {}
        cost = estimate_cost_usd(review.get("model", self.model_name), usage)
        if cost is not None:
            usage = dict(usage)
            usage["estimated_cost_usd"] = cost
            review["usage"] = usage
        with self._metrics_lock:
            self._metrics["reviews"] += 1
            if hit:
                self._metrics["cache_hits"] += 1
            self._metrics["accepts"] += int(counts.get("accept", 0))
            self._metrics["counters"] += int(counts.get("counter", 0))
            self._metrics["escalates"] += int(counts.get("escalate", 0))
            self._metrics["total_latency_s"] += latency
            if not hit:
                for key in ("prompt_tokens", "completion_tokens"):
                    try:
                        self._metrics[key] += int(usage.get(key) or 0)
                    except (TypeError, ValueError):
                        pass
                if cost is not None:
                    self._metrics["estimated_cost_usd"] += cost

    def playbook_payload(self) -> dict:
        return self.playbook

    def sample(self, name: str) -> str | None:
        safe = Path(name).name
        path = config.ROOT / "inbound" / safe
        try:
            resolved = path.resolve()
        except OSError:
            return None
        if config.ROOT / "inbound" not in resolved.parents and resolved != (config.ROOT / "inbound").resolve():
            # Allow exactly the inbound dir children.
            if resolved.parent != (config.ROOT / "inbound").resolve():
                return None
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def sample_list(self) -> list[str]:
        inbound = config.ROOT / "inbound"
        if not inbound.is_dir():
            return []
        return sorted(p.name for p in inbound.iterdir() if p.is_file())


MIME_OVERRIDES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
UPLOAD_LIMIT = 15_000_000


def make_handler(service: PrecedentService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Precedent/1.0"
        protocol_version = "HTTP/1.1"

        # -- routing ---------------------------------------------------------

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                # Content negotiation: browsers get the web UI, API clients
                # (curl, httpx, tests asking for JSON) get health JSON.
                accept = (self.headers.get("Accept") or "").lower()
                wants_html = "text/html" in accept
                wants_json = "application/json" in accept
                if wants_html and not wants_json:
                    self._serve_web("index.html")
                    return
                if wants_json or not wants_html:
                    # Default for curl/httpx/tests: JSON health (back-compat).
                    # Browsers can still reach the UI; explicit /app serves it.
                    if "mozilla" in (self.headers.get("User-Agent") or "").lower() and config.WEB_DIR.joinpath("index.html").is_file():
                        self._serve_web("index.html")
                        return
                    self._respond(200, service.health())
                    return
                self._serve_web("index.html")
                return
            if path == "/app":
                self._serve_web("index.html")
                return
            if path in ("/api/health", "/health"):
                self._respond(200, service.health())
                return
            if path == "/api/metrics":
                if not hasattr(service, "metrics"):
                    self._respond(404, {"error": "not found"})
                    return
                self._respond(200, service.metrics())
                return
            if path == "/api/playbook":
                self._respond(200, service.playbook_payload())
                return
            if path == "/api/samples":
                self._respond(200, {"samples": service.sample_list()})
                return
            if path.startswith("/api/sample/"):
                name = path[len("/api/sample/") :]
                text = service.sample(name)
                if text is None:
                    self._respond(404, {"error": "sample not found"})
                    return
                self._respond(200, {"name": Path(name).name, "contract": text})
                return
            if path.startswith("/api/"):
                self._respond(404, {"error": "not found"})
                return
            # Static web assets: /app.js, /styles.css, etc.
            rel = path.lstrip("/")
            if ".." in rel or rel.startswith(("api", "corpus", "playbook")):
                self._respond(404, {"error": "not found"})
                return
            if rel and (config.WEB_DIR / rel).is_file():
                self._serve_web(rel)
                return
            self._respond(404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/review":
                try:
                    body = self._read_json_body()
                except ValueError as exc:
                    self._respond(400, {"error": str(exc)})
                    return
                contract_text = body.get("contract")
                error = _contract_error(contract_text)
                if error:
                    self._respond(400, {"error": error})
                    return
                try:
                    review = service.handle_review(contract_text)
                except Exception as exc:
                    self._respond(500, {"error": "review failed", "detail": str(exc)})
                    return
                self._respond(200, review)
                return
            if path == "/api/review/stream":
                try:
                    body = self._read_json_body()
                except ValueError as exc:
                    self._respond(400, {"error": str(exc)})
                    return
                contract_text = body.get("contract")
                error = _contract_error(contract_text)
                if error:
                    self._respond(400, {"error": error})
                    return
                self._stream_review(contract_text)
                return
            if path == "/api/review/file":
                self._review_upload()
                return
            if path == "/api/setup":
                try:
                    body = self._read_json_body()
                except ValueError as exc:
                    self._respond(400, {"error": str(exc)})
                    return
                self._setup(body)
                return
            self._respond(404, {"error": "not found"})

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
            self.end_headers()

        # -- handlers ----------------------------------------------------------

        def _stream_review(self, contract_text: str) -> None:
            reviewer = service.reviewer
            hit = reviewer._read_cache(contract_text) is not None
            started = time.time()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                final = None
                for kind, payload in reviewer.review_iter(contract_text):
                    if kind == "done":
                        final = payload
                    else:
                        self._send_event(kind, payload)
                if final is None:
                    raise RuntimeError("review produced no result")
                service._record(contract_text, final, time.time() - started, hit)
                self._send_event("done", final)
            except Exception as exc:
                print(f"[precedent] stream failed: {exc}", file=sys.stderr, flush=True)
                try:
                    self._send_event("error", {"error": "review failed", "detail": str(exc)})
                except (ConnectionError, BrokenPipeError):
                    pass

        def _send_event(self, kind: str, payload) -> None:
            data = json.dumps(payload, ensure_ascii=False)
            chunk = "".join(f"data: {line}\n" for line in data.splitlines() or [""])
            self.wfile.write(f"event: {kind}\n{chunk}\n".encode("utf-8"))
            self.wfile.flush()

        def _review_upload(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._respond(400, {"error": "empty upload body; send raw file bytes"})
                return
            if length > UPLOAD_LIMIT:
                self._respond(400, {"error": "file too large (max 15MB)"})
                return
            filename = self.headers.get("X-Filename") or urlparse(self.path).query or "upload"
            if filename.startswith("name="):
                filename = filename[len("name="):]
            try:
                data = self.rfile.read(length)
            except OSError as exc:
                self._respond(400, {"error": f"could not read upload: {exc}"})
                return
            try:
                text = corpus.extract_upload(Path(filename).name, data)
            except RuntimeError as exc:
                self._respond(400, {"error": str(exc)})
                return
            except Exception as exc:
                self._respond(400, {"error": f"could not parse file: {exc}"})
                return
            if not text or not text.strip():
                self._respond(400, {"error": "no readable text found in file"})
                return
            try:
                review = service.handle_review(text)
            except Exception as exc:
                self._respond(500, {"error": "review failed", "detail": str(exc)})
                return
            self._respond(200, {"filename": Path(filename).name, "chars": len(text),
                               "preview": text[:4000], "review": review})

        def _setup(self, body: dict) -> None:
            # Localhost setup wizard: test credentials, optionally save + apply.
            base_url = str(body.get("base_url") or "").strip()
            api_key = str(body.get("api_key") or "").strip()
            model = str(body.get("model") or "").strip()
            save = bool(body.get("save"))
            if not base_url or not api_key:
                # Fall back to whatever is already configured.
                try:
                    base_url, api_key = config.credentials()
                except RuntimeError:
                    self._respond(400, {"error": "base_url and api_key are required"})
                    return
            try:
                from llm import LLMClient

                models = _probe_models(base_url, api_key)
                probe = LLMClient(base_url, api_key, model=model)
                models = list(probe.models)
            except Exception as exc:
                self._respond(502, {"error": "could not reach the model API", "detail": str(exc)[:300]})
                return
            if save:
                updates = {"OPENAI_BASE_URL": base_url, "OPENAI_API_KEY": api_key}
                if model:
                    updates["OPENAI_MODEL"] = model
                try:
                    config.save_env(updates)
                    service.apply_credentials(base_url, api_key, model)
                except Exception as exc:
                    self._respond(502, {"error": "credentials saved but activation failed", "detail": str(exc)[:300]})
                    return
            self._respond(200, {"ok": True, "models": models[:50], "health": service.health()})

        # -- helpers -----------------------------------------------------------

        def _serve_web(self, name: str) -> None:
            path = config.WEB_DIR / name
            if not path.is_file():
                # Fall back to JSON health if web UI not installed.
                if name == "index.html":
                    self._respond(200, service.health())
                    return
                self._respond(404, {"error": "not found"})
                return
            try:
                data = path.read_bytes()
            except OSError:
                self._respond(500, {"error": "could not read web asset"})
                return
            mime, _ = mimetypes.guess_type(str(path))
            content_type = MIME_OVERRIDES.get(path.suffix.lower(), None) or (mime or "application/octet-stream")
            if content_type.startswith("text/") and "charset" not in content_type:
                content_type += "; charset=utf-8"
            if name == "index.html":
                content_type = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ValueError("empty request body")
            if length > 2_000_000:
                raise ValueError("request body too large")
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"invalid JSON body: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("request body must be a JSON object")
            return parsed

        def _respond(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:
            print(f"[precedent] {self.address_string()} {format % args}", file=sys.stderr, flush=True)

    return Handler


def _probe_models(base_url: str, api_key: str) -> list[str]:
    """Verify an OpenAI-compatible API is reachable. Raises on failure.

    Unlike LLMClient init (which silently falls back to candidate names),
    the setup wizard needs a hard yes/no that the key and URL work.
    """
    import httpx

    base = base_url.rstrip("/")
    bases = [base, base[: -len("/v1")]] if base.endswith("/v1") else [base, f"{base}/v1"]
    last_error: Exception | None = None
    for candidate in bases:
        try:
            response = httpx.get(
                f"{candidate}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15.0,
            )
            if response.status_code == 200:
                ids = [m.get("id") for m in (response.json().get("data") or []) if m.get("id")]
                if ids:
                    return ids
                last_error = RuntimeError("model list came back empty")
            else:
                last_error = RuntimeError(f"GET /models returned {response.status_code}")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last_error = exc
    raise RuntimeError(f"no reachable model API: {last_error}")


def _contract_error(contract_text) -> str | None:
    if not isinstance(contract_text, str) or not contract_text.strip():
        return 'request body must be JSON with a non-empty "contract" string'
    if len(contract_text) > 200_000:
        return "contract too large (max 200k chars)"
    return None


def main() -> None:
    service = PrecedentService()
    server = ThreadingHTTPServer(("0.0.0.0", config.port()), make_handler(service))
    print(f"[precedent] serving on port {config.port()} (mode={'demo' if service.demo else 'live'})", file=sys.stderr, flush=True)
    print(f"[precedent] open http://127.0.0.1:{config.port()}/ in your browser", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[precedent] shutting down", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
