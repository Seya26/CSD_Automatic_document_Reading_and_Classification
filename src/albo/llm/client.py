"""LLM integration (Gantt task 49). The pipeline only depends on the LLMClient protocol."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Protocol

import requests

from ..config import Settings


@dataclass
class LLMResult:
    data: dict
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0


class LLMClient(Protocol):
    model: str

    def extract(self, system: str, user: str, tool: dict) -> LLMResult:
        """Call the model, forcing the given tool, and return the tool input."""


class OllamaClient:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", num_ctx: int = 8192):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.read_timeout = int(os.environ.get("ALBO_OLLAMA_READ_TIMEOUT_S", "600"))
        if self.read_timeout <= 0:
            raise ValueError("ALBO_OLLAMA_READ_TIMEOUT_S must be positive")

    def extract(self, system: str, user: str, tool: dict) -> LLMResult:
        started = time.monotonic()
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": system + "\nReturn only one JSON object matching the supplied schema. No markdown.",
                    },
                    {"role": "user", "content": user},
                ],
                "format": tool["input_schema"],
                "think": False,
                "stream": False,
                "options": {"temperature": 0, "num_ctx": self.num_ctx},
            },
            timeout=(5, self.read_timeout),
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("message", {}).get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("Ollama returned no message content")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("Ollama returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Ollama response must be a JSON object")

        usage = {}
        if "prompt_eval_count" in payload:
            usage["input_tokens"] = payload["prompt_eval_count"]
        if "eval_count" in payload:
            usage["output_tokens"] = payload["eval_count"]
        latency = int(payload.get("total_duration", 0) / 1_000_000) or int((time.monotonic() - started) * 1000)
        return LLMResult(data=data, usage=usage, latency_ms=latency)


class GeminiClient:
    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key.strip()
        if not self.api_key:
            raise ValueError("Imposta GEMINI_API_KEY per usare Gemini")

    def extract(self, system: str, user: str, tool: dict) -> LLMResult:
        started = time.monotonic()
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "tools": [{"functionDeclarations": [{
                    "name": tool["name"], "description": tool["description"],
                    "parametersJsonSchema": tool["input_schema"],
                }]}],
                "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tool["name"]]}},
            },
            timeout=(5, 120),
        )
        response.raise_for_status()
        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            reason = payload.get("promptFeedback", {}).get("blockReason", "unknown")
            raise ValueError(f"Gemini returned no candidate ({reason})")
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        calls = [part["functionCall"] for part in parts if "functionCall" in part]
        if (candidate.get("finishReason") != "STOP" or len(calls) != 1
                or calls[0].get("name") != tool["name"] or not isinstance(calls[0].get("args"), dict)):
            raise ValueError(f"Gemini returned no complete extraction ({candidate.get('finishReason')})")
        metadata = payload.get("usageMetadata", {})
        usage = {key: metadata[name] for key, name in (
            ("input_tokens", "promptTokenCount"), ("output_tokens", "candidatesTokenCount")
        ) if name in metadata}
        return LLMResult(calls[0]["args"], usage, int((time.monotonic() - started) * 1000))


def create_client(settings: Settings) -> LLMClient:
    model = settings.classification_model
    if settings.llm_provider == "gemini":
        return GeminiClient(model, os.environ.get("GEMINI_API_KEY", ""))
    return OllamaClient(model, settings.ollama_base_url, settings.ollama_num_ctx)
