"""Optional AI provider boundary.

No provider is enabled by default. An OpenAI-compatible endpoint is used only
when the operator explicitly sets IWTT_AI_BASE_URL (and optionally model/key).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class AIUnavailable(RuntimeError):
    pass


class AIProvider:
    name = "AI provider"
    model: str | None = None
    available = False

    def summarize(self, thought: dict[str, Any]) -> str:
        raise AIUnavailable("No AI provider is configured")

    def suggest_tags(self, thought: dict[str, Any]) -> list[str]:
        raise AIUnavailable("No AI provider is configured")

    def suggest_relationships(self, thought: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise AIUnavailable("No AI provider is configured")


class NoAIProvider(AIProvider):
    name = "NoAIProvider"
    available = False


class OpenAICompatibleProvider(AIProvider):
    """Minimal opt-in adapter for a local OpenAI-compatible chat endpoint."""

    name = "OpenAI-compatible endpoint"
    available = True

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/v1"):
            return self.base_url + "/chat/completions"
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/v1/chat/completions"

    def _chat(self, system: str, user: str, max_tokens: int = 350) -> str:
        body = json.dumps({
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"The configured AI endpoint did not return a valid response: {exc}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("The configured endpoint returned an unexpected chat-completions response") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("The configured AI endpoint returned an empty response")
        return content.strip()

    def summarize(self, thought: dict[str, Any]) -> str:
        system = (
            "You create a concise, neutral summary of a personal idea. Do not rewrite, correct, "
            "or replace the author's text. Return only the summary, clearly treating it as an interpretation."
        )
        user = (
            "Summarize the following thought in two or three sentences. Preserve uncertainty and intent.\n\n"
            f"ORIGINAL CAPTURE (read-only):\n{thought['originalText']}\n\n"
            f"CURRENT VERSION:\n{thought['currentText']}"
        )
        return self._chat(system, user, max_tokens=220)

    def suggest_tags(self, thought: dict[str, Any]) -> list[str]:
        system = "Return only a JSON array of up to five short lowercase tags. Do not add commentary."
        user = f"Suggest useful tags for this idea.\n\n{thought['currentText']}"
        raw = self._chat(system, user, max_tokens=120)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The configured AI endpoint returned invalid JSON for tags") from exc
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise RuntimeError("The configured AI endpoint returned an invalid tag list")
        return [item.strip()[:40] for item in value[:5] if item.strip()]

    def suggest_relationships(self, thought: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system = (
            "Return only a JSON array. Each item has targetId, type, and reason. "
            "Use only supplied IDs and these types: INSPIRED_BY, LED_TO, RELATED_TO, CONTRADICTS, "
            "SUPPORTS, DEPENDS_ON, PART_OF, SUPERSEDES, REVIVES, TESTS, IMPLEMENTED_BY."
        )
        compact = [{"id": item["id"], "title": item["displayTitle"], "text": item["currentText"][:500]} for item in candidates]
        user = json.dumps({"thought": thought["currentText"], "candidates": compact}, ensure_ascii=False)
        raw = self._chat(system, user, max_tokens=500)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The configured AI endpoint returned invalid JSON for relationships") from exc
        if not isinstance(value, list):
            raise RuntimeError("The configured AI endpoint returned an invalid relationship list")
        ids = {item["id"] for item in candidates}
        result = []
        for item in value[:10]:
            if isinstance(item, dict) and item.get("targetId") in ids and isinstance(item.get("type"), str):
                result.append(item)
        return result


def provider_from_environment() -> AIProvider:
    """No network capability exists unless an endpoint is explicitly configured."""
    base_url = os.environ.get("IWTT_AI_BASE_URL", "").strip()
    if not base_url:
        return NoAIProvider()
    model = os.environ.get("IWTT_AI_MODEL", "local-model").strip() or "local-model"
    api_key = os.environ.get("IWTT_AI_API_KEY") or None
    return OpenAICompatibleProvider(base_url, model, api_key)
