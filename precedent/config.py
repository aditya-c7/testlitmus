import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "corpus"
PLAYBOOK_DIR = ROOT / "playbook"
REVIEW_CACHE_DIR = ROOT / "review_cache"

DISPOSITIONS = ("accept", "counter", "escalate")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(ROOT / ".env")


def port() -> int:
    return int(os.environ.get("PORT", "8000"))


def credentials() -> tuple[str, str]:
    base_url = os.environ.get("LITMUS_AI_BASE_URL", "").strip()
    api_key = os.environ.get("LITMUS_AI_API_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError(
            "LITMUS_AI_BASE_URL and LITMUS_AI_API_KEY must be set. "
            "Export them in your shell or place them in a .env file next to server.py."
        )
    return base_url, api_key
