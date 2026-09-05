import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "corpus"
PLAYBOOK_DIR = ROOT / "playbook"
REVIEW_CACHE_DIR = ROOT / "review_cache"
WEB_DIR = ROOT / "web"

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
    try:
        return int(os.environ.get("PORT", "8000"))
    except ValueError:
        return 8000


def _get_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip().strip("'\"")
        if value:
            return value
    return ""


def credentials() -> tuple[str, str]:
    """Return (base_url, api_key), supporting generic OpenAI env vars.

    Priority: OPENAI_BASE_URL/OPENAI_API_KEY, then LITMUS_* (back-compat),
    then AI_* generic. Raises only when demo mode is off.
    """
    base_url = _get_env("OPENAI_BASE_URL", "LITMUS_AI_BASE_URL", "AI_BASE_URL")
    api_key = _get_env("OPENAI_API_KEY", "LITMUS_AI_API_KEY", "AI_API_KEY")
    if not base_url or not api_key:
        raise RuntimeError(
            "No AI credentials found. Set OPENAI_BASE_URL and OPENAI_API_KEY "
            "(or LITMUS_AI_BASE_URL / LITMUS_AI_API_KEY), place them in a .env "
            "file next to server.py, or run with DEMO_MODE=true for offline use."
        )
    return base_url, api_key


def demo_mode() -> bool:
    """True when the service should run without an LLM (localhost demo)."""
    flag = os.environ.get("DEMO_MODE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    # Auto-demo when no credentials are present.
    base_url = _get_env("OPENAI_BASE_URL", "LITMUS_AI_BASE_URL", "AI_BASE_URL")
    api_key = _get_env("OPENAI_API_KEY", "LITMUS_AI_API_KEY", "AI_API_KEY")
    return not (base_url and api_key)


def model_override() -> str:
    return _get_env("OPENAI_MODEL", "AI_MODEL")
