"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


def resolve_raw_pdf(raw_path: str, raw_dir: Path) -> Path:
    """Resolve an archived PDF after moving the shared data folder between hosts."""
    original = Path(raw_path)
    if original.is_file():
        return original
    parts = PureWindowsPath(raw_path).parts
    if len(parts) >= 3 and parts[-3] == "raw" and ".." not in parts[-2:]:
        return raw_dir.joinpath(*parts[-2:])
    return original


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    raw_dir: Path
    config_dir: Path
    ollama_model: str
    ollama_base_url: str
    ollama_num_ctx: int
    llm_max_chars: int
    conf_threshold: float
    ocr_lang: str
    ocr_min_chars_per_page: int
    pseudonymise: bool
    request_delay_s: float
    max_download_mb: int
    user_agent: str
    llm_provider: str = "ollama"
    gemini_model: str = "gemini-3.5-flash-lite"

    @property
    def classification_model(self) -> str:
        if self.llm_provider == "gemini":
            return self.gemini_model
        if self.llm_provider == "ollama":
            return self.ollama_model
        raise ValueError("ALBO_LLM_PROVIDER deve essere gemini oppure ollama")

    @property
    def municipalities_file(self) -> Path:
        return self.config_dir / "municipalities.yaml"

    @property
    def fewshot_file(self) -> Path:
        return self.config_dir / "fewshot.json"


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("ALBO_DATA_DIR", "data")).resolve()
    config_dir = Path(os.environ.get("ALBO_CONFIG_DIR", "config")).resolve()
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.environ.get("ALBO_DB_PATH", data_dir / "albo.db")).resolve(),
        raw_dir=data_dir / "raw",
        config_dir=config_dir,
        llm_provider=os.environ.get("ALBO_LLM_PROVIDER", "ollama").strip().lower(),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:4b"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "8192")),
        llm_max_chars=int(os.environ.get("ALBO_LLM_MAX_CHARS", "12000")),
        conf_threshold=float(os.environ.get("ALBO_CONF_THRESHOLD", "0.85")),
        ocr_lang=os.environ.get("ALBO_OCR_LANG", "ita"),
        ocr_min_chars_per_page=int(os.environ.get("ALBO_OCR_MIN_CHARS_PER_PAGE", "100")),
        pseudonymise=_bool("ALBO_PSEUDONYMISE", True),
        request_delay_s=float(os.environ.get("ALBO_REQUEST_DELAY_S", "1.5")),
        max_download_mb=int(os.environ.get("ALBO_MAX_DOWNLOAD_MB", "50")),
        user_agent=os.environ.get(
            "ALBO_USER_AGENT", "albo-pipeline/0.1 (university research project)"
        ),
    )
