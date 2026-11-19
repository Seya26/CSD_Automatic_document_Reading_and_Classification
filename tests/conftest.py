import shutil
from pathlib import Path

import pymupdf
import pytest

from albo.config import get_settings
from albo.db import Database
from albo.llm.client import LLMResult


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ALBO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ALBO_CONFIG_DIR", str(tmp_path / "config"))
    settings = get_settings()
    return settings, Database(settings.db_path)


def make_pdf(path: Path, text: str) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 790), text, fontsize=11)
    doc.save(path)
    doc.close()
    return path


def make_scanned_pdf(path: Path, text: str) -> Path:
    """A PDF whose only content is a picture of the text (needs OCR)."""
    src = pymupdf.open()
    page = src.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 400), text, fontsize=22)
    png = page.get_pixmap(dpi=200).tobytes("png")
    doc = pymupdf.open()
    new = doc.new_page()
    new.insert_image(new.rect, stream=png)
    doc.save(path)
    return path


class FakeClient:
    """Test double for the LLM client: `respond(user_message) -> dict`."""

    model = "fake-model"

    def __init__(self, respond):
        self.respond = respond

    def extract(self, system, user, tool):
        return LLMResult(data=self.respond(user), usage={"input_tokens": 10, "output_tokens": 5}, latency_ms=3)


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def field(value, evidence=None, confidence=0.95):
    return {"value": value, "evidence": evidence, "confidence": confidence}
