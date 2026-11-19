"""Store raw documents (Gantt tasks 16-17): metadata + de-duplication by content hash."""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

from ..config import Settings
from ..db import Database
from ..values import parse_date
from .adapters import build_adapter


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "unknown"


def ingest_bytes(db: Database, settings: Settings, data: bytes, *, municipality: str,
                 declared_type: str | None = None, published_at: str | None = None,
                 source_url: str | None = None) -> tuple[str, bool]:
    """Store one PDF. Returns (doc_id, created); created is False for a duplicate."""
    if not data.startswith(b"%PDF"):
        raise ValueError("Not a PDF file")
    sha = hashlib.sha256(data).hexdigest()
    doc_id = sha[:12]
    folder = settings.raw_dir / slugify(municipality)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{doc_id}.pdf"
    created = db.add_document(
        doc_id=doc_id, sha256=sha, municipality=municipality, declared_type=declared_type,
        published_at=published_at, source_url=source_url, raw_path=str(path),
    )
    if created:
        path.write_bytes(data)
        db.log(doc_id, "ingest", "ok", source_url or "local")
    return doc_id, created


def ingest_local(db: Database, settings: Settings, folder: Path, municipality: str) -> dict[str, int]:
    """Import PDFs already downloaded by hand. Optional metadata.csv with the columns
    filename, declared_type, published_at, source_url."""
    meta: dict[str, dict] = {}
    meta_file = folder / "metadata.csv"
    if meta_file.exists():
        with meta_file.open(newline="", encoding="utf-8") as fh:
            meta = {row["filename"]: row for row in csv.DictReader(fh)}
    counts = {"added": 0, "duplicates": 0, "skipped": 0}
    for pdf in sorted(folder.glob("*.pdf")):
        row = meta.get(pdf.name, {})
        published = row.get("published_at") or None
        try:
            _, created = ingest_bytes(
                db, settings, pdf.read_bytes(), municipality=municipality,
                declared_type=row.get("declared_type") or None,
                published_at=parse_date(published) or published,
                source_url=row.get("source_url") or None,
            )
        except ValueError as exc:
            db.log(None, "ingest", "skipped", f"{pdf.name}: {exc}")
            counts["skipped"] += 1
            continue
        counts["added" if created else "duplicates"] += 1
    return counts


def run_ingest(db: Database, settings: Settings, slug: str, limit: int | None = None) -> dict[str, int]:
    adapter = build_adapter(slug, settings)
    counts = {"added": 0, "duplicates": 0, "skipped": 0, "errors": 0}
    for remote in adapter.list_documents(limit):
        if db.has_source_url(remote.url):
            counts["duplicates"] += 1
            continue
        try:
            data = adapter.download(remote)
            _, created = ingest_bytes(
                db, settings, data, municipality=adapter.name, declared_type=remote.declared_type,
                published_at=remote.published_at, source_url=remote.url,
            )
            counts["added" if created else "duplicates"] += 1
        except ValueError as exc:  # not a PDF, too large
            db.log(None, "ingest", "skipped", f"{remote.url}: {exc}")
            counts["skipped"] += 1
        except Exception as exc:  # network errors, robots.txt, ...
            db.log(None, "ingest", "error", f"{remote.url}: {exc}")
            counts["errors"] += 1
    return counts
