"""Adapters. `generic_html` is driven entirely by CSS selectors in config/municipalities.yaml;
add a subclass here for boards whose structure it cannot express (JSON APIs, forms, ...)."""
from __future__ import annotations

from typing import Iterator
from urllib.parse import urljoin

import yaml
from bs4 import BeautifulSoup

from ..config import Settings
from ..values import parse_date
from .base import BaseAdapter, RemoteDoc


def _text(node) -> str | None:
    return node.get_text(" ", strip=True) if node else None


class GenericHtmlAdapter(BaseAdapter):
    def list_documents(self, limit: int | None = None) -> Iterator[RemoteDoc]:
        cfg = self.cfg
        first = int(cfg.get("first_page", 1))
        seen = 0
        for page in range(first, first + int(cfg.get("max_pages", 1))):
            list_url = cfg["list_url"].format(page=page)
            soup = BeautifulSoup(self.get(list_url).text, "html.parser")
            rows = soup.select(cfg["row_selector"])
            if not rows:
                return
            for row in rows:
                link = row.select_one(cfg["link_selector"])
                if not link or not link.get("href"):
                    continue
                published = _text(row.select_one(cfg["date_selector"])) if cfg.get("date_selector") else None
                yield RemoteDoc(
                    url=urljoin(list_url, link["href"]),
                    declared_type=_text(row.select_one(cfg["declared_type_selector"]))
                    if cfg.get("declared_type_selector") else None,
                    published_at=parse_date(published) or published,
                    title=_text(row.select_one(cfg["title_selector"])) if cfg.get("title_selector") else _text(link),
                )
                seen += 1
                if limit and seen >= limit:
                    return


ADAPTERS: dict[str, type[BaseAdapter]] = {"generic_html": GenericHtmlAdapter}


def load_municipalities(settings: Settings) -> dict[str, dict]:
    data = yaml.safe_load(settings.municipalities_file.read_text(encoding="utf-8")) or {}
    return {m["slug"]: m for m in data.get("municipalities", [])}


def build_adapter(slug: str, settings: Settings) -> BaseAdapter:
    municipalities = load_municipalities(settings)
    if slug not in municipalities:
        raise KeyError(f"Unknown municipality '{slug}'. Available: {', '.join(municipalities) or 'none'}")
    cfg = municipalities[slug]
    return ADAPTERS[cfg.get("adapter", "generic_html")](cfg, settings)
