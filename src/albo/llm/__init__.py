from .classify import classify_all, classify_document
from .client import LLMClient, LLMResult, OllamaClient

__all__ = ["LLMClient", "LLMResult", "OllamaClient", "classify_all", "classify_document"]
