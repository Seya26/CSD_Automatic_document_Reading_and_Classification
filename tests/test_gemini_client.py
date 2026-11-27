from unittest.mock import Mock

import pytest

from albo.config import get_settings
from albo.llm.client import create_client
from albo.schema import EXTRACTION_TOOL


def test_gemini_extraction(monkeypatch):
    monkeypatch.setenv("ALBO_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = create_client(get_settings())
    data = {"doc_type": "ORDINANCE"}
    call = {"name": "record_extraction", "args": data}
    response = Mock()
    response.json.return_value = {
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"functionCall": call}]}}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
    }
    post = Mock(return_value=response)
    monkeypatch.setattr("albo.llm.client.requests.post", post)
    result = client.extract("istruzioni", "documento", EXTRACTION_TOOL)
    assert result.data == data
    assert result.usage == {"input_tokens": 7, "output_tokens": 3}
    assert post.call_args.kwargs["headers"] == {"x-goog-api-key": "test-key"}
    body = post.call_args.kwargs["json"]
    assert body["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == EXTRACTION_TOOL["input_schema"]
    assert body["contents"][0]["parts"] == [{"text": "documento"}]

    response.json.return_value["candidates"][0]["finishReason"] = "MAX_TOKENS"
    with pytest.raises(ValueError, match="MAX_TOKENS"):
        client.extract("istruzioni", "documento", EXTRACTION_TOOL)


def test_missing_key(monkeypatch):
    monkeypatch.setenv("ALBO_LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        create_client(get_settings())
