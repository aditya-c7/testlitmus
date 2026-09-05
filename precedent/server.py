"""Precedent — legal playbook + contract review service with localhost web UI."""
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import config
import corpus
import playbook as playbook_module
from reviewer import Reviewer


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
        try:
            self.playbook, self.fingerprint = playbook_module.load_or_build(self.documents, None if self.demo else self.llm)
        except Exception as exc:
            print(f"[precedent] playbook build failed: {exc}", file=sys.stderr, flush=True)
            stale = playbook_module._read_stale()
            if stale is None:
                raise
            self.playbook = stale
            self.fingerprint = stale.get("fingerprint", "stale")
        print(
            f"[precedent] playbook ready (fingerprint {self.fingerprint}, "
            f"{len(self.playbook.get('topics', []))} topics, demo={self.demo})",
            file=sys.stderr,
            flush=True,
        )
        self.reviewer = Reviewer(self.llm, self.documents, self.playbook, self.fingerprint)

    def health(self) -> dict:
        return {
            "status": "ready",
            "service": "precedent",
            "mode": "demo" if self.demo else "live",
            "model": self.model_name,
            "playbook_fingerprint": self.fingerprint,
            "playbook_topics": len(self.playbook.get("topics", [])),
            "corpus_documents": len(self.documents),
        }

    def handle_review(self, contract_text: str) -> dict:
        return self.reviewer.review(contract_text)

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


def make_handler(service: PrecedentService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Precedent/1.0"

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
            if parsed.path != "/api/review":
                self._respond(404, {"error": "not found"})
                return
            try:
                body = self._read_json_body()
            except ValueError as exc:
                self._respond(400, {"error": str(exc)})
                return
            contract_text = body.get("contract")
            if not isinstance(contract_text, str) or not contract_text.strip():
                self._respond(400, {"error": 'request body must be JSON with a non-empty "contract" string'})
                return
            if len(contract_text) > 200_000:
                self._respond(400, {"error": "contract too large (max 200k chars)"})
                return
            try:
                review = service.handle_review(contract_text)
            except Exception as exc:
                print(f"[precedent] review failed: {exc}", file=sys.stderr, flush=True)
                self._respond(500, {"error": "review failed", "detail": str(exc)})
                return
            self._respond(200, review)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

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
