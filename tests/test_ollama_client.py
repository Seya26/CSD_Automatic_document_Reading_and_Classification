import json

from albo.llm.client import OllamaClient
from albo.schema import EXTRACTION_TOOL


def test_ollama_client_returns_structured_json(monkeypatch):
    payload = {
        "message": {
            "content": json.dumps({"doc_type": "OTHER"}, ensure_ascii=False),
        },
        "prompt_eval_count": 12,
        "eval_count": 7,
        "total_duration": 2_500_000,
    }
    request = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    def post(url, *, json, timeout):
        request.update(url=url, body=json, timeout=timeout)
        return Response()

    monkeypatch.setattr("albo.llm.client.requests.post", post)
    client = OllamaClient("qwen3:4b", "http://localhost:11434/", num_ctx=4096)

    result = client.extract("system prompt", "user message", EXTRACTION_TOOL)

    assert request["url"] == "http://localhost:11434/api/chat"
    assert request["body"]["format"] == EXTRACTION_TOOL["input_schema"]
    assert request["body"]["options"] == {"temperature": 0, "num_ctx": 4096}
    assert result.data == {"doc_type": "OTHER"}
    assert result.usage == {"input_tokens": 12, "output_tokens": 7}
    assert result.latency_ms == 2
