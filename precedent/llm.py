import json
import re
import time

import httpx

MODEL_CANDIDATES = (
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-3.5-turbo",
)

MODEL_PREFERENCES = (
    "gpt-4.1",
    "gpt-4o",
    "gpt-4-turbo",
    "claude-sonnet",
    "claude-3-7",
    "claude-3-5-sonnet",
    "gemini-2.5-pro",
    "gemini-2.0",
    "deepseek-chat",
    "qwen-max",
    "llama-3.3-70b",
)

RATE_LIMIT_STATUS = 429


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 150.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.models = self._discover_models()
        self.model = self.models[0]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _discover_models(self) -> list[str]:
        for base in (self.base_url, f"{self.base_url}/v1"):
            try:
                response = httpx.get(f"{base}/models", headers=self._headers(), timeout=15.0)
                if response.status_code == 200:
                    ids = [m["id"] for m in (response.json().get("data") or []) if m.get("id")]
                    if ids:
                        return _rank_models(ids)
            except (httpx.HTTPError, ValueError, KeyError):
                continue
        return list(MODEL_CANDIDATES)

    def _rotate_model(self) -> bool:
        current = self.models.index(self.model) if self.model in self.models else 0
        if current + 1 < len(self.models):
            self.model = self.models[current + 1]
            return True
        return False

    def complete(self, system: str, user: str, max_tokens: int = 8000) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code == RATE_LIMIT_STATUS:
                    raise LLMError(f"rate limited (429) on model {payload['model']}")
                if response.status_code != 200:
                    raise LLMError(
                        f"chat/completions returned {response.status_code}: {response.text[:300]}"
                    )
                return response.json()["choices"][0]["message"]["content"]
            except (httpx.HTTPError, LLMError, KeyError, ValueError) as exc:
                last_error = exc
                self._rotate_model()
                payload["model"] = self.model
                time.sleep(min(2 ** attempt, 15))
        raise LLMError(f"LLM call failed after retries: {last_error}")

    def complete_json(self, system: str, user: str, max_tokens: int = 8000):
        text = self.complete(system, user, max_tokens=max_tokens)
        return _extract_json(text)


def _rank_models(ids: list[str]) -> list[str]:
    def rank(model_id: str) -> int:
        lowered = model_id.lower()
        for priority, needle in enumerate(MODEL_PREFERENCES):
            if needle in lowered:
                return priority
        return len(MODEL_PREFERENCES)

    return sorted(ids, key=lambda model_id: (rank(model_id), model_id))


def _extract_json(text: str):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start = min((i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise LLMError(f"no JSON object found in model output: {text[:300]}")
    return json.loads(cleaned[start:])
