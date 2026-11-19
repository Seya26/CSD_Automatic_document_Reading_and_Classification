"""Download adapters (Gantt task 15): one configuration per municipality notice board."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator
from urllib import robotparser
from urllib.parse import urlparse

import requests

from ..config import Settings


@dataclass
class RemoteDoc:
    url: str
    declared_type: str | None = None
    published_at: str | None = None
    title: str | None = None


class BaseAdapter(ABC):
    """Polite HTTP client (robots.txt, delay, size limit) + the listing logic to implement."""

    def __init__(self, cfg: dict, settings: Settings):
        self.cfg = cfg
        self.settings = settings
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.user_agent
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0

    @property
    def name(self) -> str:
        return self.cfg["name"]

    def _allowed(self, url: str) -> bool:
        parts = urlparse(url)
        if parts.scheme not in {"http", "https"}:
            return False
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = robotparser.RobotFileParser(origin + "/robots.txt")
            try:
                rp.read()
            except Exception:  # robots.txt unreachable: be permissive, the delay still applies
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is None or rp.can_fetch(self.settings.user_agent, url)

    def _wait(self) -> None:
        delay = self.settings.request_delay_s - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()

    def get(self, url: str, *, stream: bool = False) -> requests.Response:
        if not self._allowed(url):
            raise PermissionError(f"Blocked by robots.txt or unsupported scheme: {url}")
        self._wait()
        response = self.session.get(url, timeout=30, stream=stream)
        response.raise_for_status()
        return response

    def download(self, doc: RemoteDoc) -> bytes:
        limit = self.settings.max_download_mb * 1024 * 1024
        response = self.get(doc.url, stream=True)
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > limit:
                raise ValueError(f"File larger than {self.settings.max_download_mb} MB: {doc.url}")
            chunks.append(chunk)
        return b"".join(chunks)

    @abstractmethod
    def list_documents(self, limit: int | None = None) -> Iterator[RemoteDoc]:
        """Yield the documents published on the board (most recent first)."""
