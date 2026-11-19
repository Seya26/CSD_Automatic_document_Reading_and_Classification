"""LLM integration (Gantt task 49). The pipeline only depends on the LLMClient protocol."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Protocol

import requests


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
                "stream": False,
                "options": {"temperature": 0, "num_ctx": self.num_ctx},
            },
            timeout=(5, 600),
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
