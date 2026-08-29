import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import corpus
import playbook as playbook_module
from llm import LLMClient
from reviewer import Reviewer


class PrecedentService:
    def __init__(self):
        base_url, api_key = config.credentials()
        self.llm = LLMClient(base_url, api_key)
        print(f"[precedent] model resolved: {self.llm.model}", file=sys.stderr, flush=True)
        self.documents = corpus.load_corpus(config.CORPUS_DIR)
        print(f"[precedent] loaded {len(self.documents)} corpus documents", file=sys.stderr, flush=True)
        self.playbook, self.fingerprint = playbook_module.load_or_build(self.documents, self.llm)
        print(
            f"[precedent] playbook ready (fingerprint {self.fingerprint}, "
            f"{len(self.playbook.get('topics', []))} topics)",
            file=sys.stderr,
            flush=True,
        )
        self.reviewer = Reviewer(self.llm, self.documents, self.playbook, self.fingerprint)

    def health(self) -> dict:
        return {
            "status": "ready",
            "service": "precedent",
            "model": self.llm.model,
            "playbook_fingerprint": self.fingerprint,
            "playbook_topics": len(self.playbook.get("topics", [])),
        }

    def handle_review(self, contract_text: str) -> dict:
        return self.reviewer.review(contract_text)


def make_handler(service: PrecedentService):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self._respond(404, {"error": "not found"})
                return
            self._respond(200, service.health())

        def do_POST(self):
            if self.path != "/api/review":
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
            try:
                review = service.handle_review(contract_text)
            except Exception as exc:
                print(f"[precedent] review failed: {exc}", file=sys.stderr, flush=True)
                self._respond(500, {"error": "review failed", "detail": str(exc)})
                return
            self._respond(200, review)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                raise ValueError("empty request body")
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid JSON body: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("request body must be a JSON object")
            return parsed

        def _respond(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:
            print(f"[precedent] {self.address_string()} {format % args}", file=sys.stderr, flush=True)

    return Handler


def main() -> None:
    service = PrecedentService()
    server = ThreadingHTTPServer(("0.0.0.0", config.port()), make_handler(service))
    print(f"[precedent] serving on port {config.port()}", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
