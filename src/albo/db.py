"""SQLite storage shared by the pipeline and the Streamlit app (WAL mode)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    municipality TEXT NOT NULL,
    declared_type TEXT,
    published_at TEXT,
    source_url TEXT,
    raw_path TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS text_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
    version INTEGER NOT NULL,
    extractor TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    pseudonymised INTEGER NOT NULL,
    n_pages INTEGER NOT NULL,
    n_chars INTEGER NOT NULL,
    text_sha256 TEXT NOT NULL,
    text TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (doc_id, version)
);
CREATE TABLE IF NOT EXISTS processing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
    text_version INTEGER NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('auto_accepted', 'needs_review')),
    record_json TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    usage_json TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
    prediction_id INTEGER REFERENCES predictions(id),
    reviewer TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('approve', 'correct', 'discard')),
    record_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ground_truth (
    doc_id TEXT PRIMARY KEY REFERENCES documents(doc_id),
    doc_type TEXT NOT NULL,
    municipality TEXT,
    number TEXT,
    date TEXT,
    subject TEXT,
    evidence_json TEXT,
    annotator TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_doc ON predictions(doc_id, id);
CREATE INDEX IF NOT EXISTS idx_reviews_doc ON reviews(doc_id, id);
CREATE INDEX IF NOT EXISTS idx_text_current ON text_versions(doc_id, is_current);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def run(self, sql: str, params: tuple = ()) -> int:
        with self.conn() as c:
            return c.execute(sql, params).lastrowid or 0

    # ---- documents -------------------------------------------------
    def add_document(self, *, doc_id: str, sha256: str, municipality: str, raw_path: str,
                     declared_type: str | None = None, published_at: str | None = None,
                     source_url: str | None = None) -> bool:
        """Insert a document; return False if the same file (sha256) is already stored."""
        if self.one("SELECT 1 FROM documents WHERE sha256 = ?", (sha256,)):
            return False
        self.run(
            "INSERT INTO documents (doc_id, sha256, municipality, declared_type, published_at,"
            " source_url, raw_path, ingested_at) VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, sha256, municipality, declared_type, published_at, source_url, raw_path, now()),
        )
        return True

    def get_document(self, doc_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM documents WHERE doc_id = ?", (doc_id,))

    def has_source_url(self, url: str) -> bool:
        return self.one("SELECT 1 FROM documents WHERE source_url = ?", (url,)) is not None

    def list_documents(self, municipality: str | None = None) -> list[sqlite3.Row]:
        if municipality:
            return self.q("SELECT * FROM documents WHERE municipality = ? ORDER BY doc_id", (municipality,))
        return self.q("SELECT * FROM documents ORDER BY doc_id")

    def municipalities(self) -> list[str]:
        return [r[0] for r in self.q("SELECT DISTINCT municipality FROM documents ORDER BY 1")]

    # ---- text versions ---------------------------------------------
    def current_text(self, doc_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM text_versions WHERE doc_id = ? AND is_current = 1", (doc_id,))

    def add_text_version(self, doc_id: str, **kw: Any) -> int:
        with self.conn() as c:
            row = c.execute("SELECT COALESCE(MAX(version), 0) FROM text_versions WHERE doc_id = ?", (doc_id,)).fetchone()
            version = row[0] + 1
            c.execute("UPDATE text_versions SET is_current = 0 WHERE doc_id = ?", (doc_id,))
            c.execute(
                "INSERT INTO text_versions (doc_id, version, extractor, extraction_method, normalizer_version,"
                " pseudonymised, n_pages, n_chars, text_sha256, text, is_current, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,1,?)",
                (doc_id, version, kw["extractor"], kw["extraction_method"], kw["normalizer_version"],
                 int(kw["pseudonymised"]), kw["n_pages"], kw["n_chars"], kw["text_sha256"], kw["text"], now()),
            )
        return version

    def documents_to_preprocess(self, force: bool = False) -> list[sqlite3.Row]:
        if force:
            return self.list_documents()
        return self.q(
            "SELECT d.* FROM documents d WHERE NOT EXISTS"
            " (SELECT 1 FROM text_versions t WHERE t.doc_id = d.doc_id) ORDER BY d.doc_id"
        )

    # ---- log -------------------------------------------------------
    def log(self, doc_id: str | None, stage: str, status: str, message: str = "") -> None:
        self.run(
            "INSERT INTO processing_log (doc_id, stage, status, message, created_at) VALUES (?,?,?,?,?)",
            (doc_id, stage, status, message, now()),
        )

    # ---- predictions -----------------------------------------------
    def add_prediction(self, *, doc_id: str, text_version: int, model: str, prompt_version: str,
                       status: str, record: dict, issues: list[dict], usage: dict | None,
                       latency_ms: int | None) -> int:
        return self.run(
            "INSERT INTO predictions (doc_id, text_version, model, prompt_version, status, record_json,"
            " issues_json, usage_json, latency_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc_id, text_version, model, prompt_version, status, dumps(record), dumps(issues),
             dumps(usage) if usage else None, latency_ms, now()),
        )

    def latest_prediction(self, doc_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM predictions WHERE doc_id = ? ORDER BY id DESC LIMIT 1", (doc_id,))

    def documents_to_classify(self, model: str, prompt_version: str, force: bool = False) -> list[sqlite3.Row]:
        if force:
            return self.q(
                "SELECT d.* FROM documents d JOIN text_versions t ON t.doc_id = d.doc_id AND t.is_current = 1"
                " ORDER BY d.doc_id"
            )
        return self.q(
            "SELECT d.* FROM documents d JOIN text_versions t ON t.doc_id = d.doc_id AND t.is_current = 1"
            " WHERE NOT EXISTS (SELECT 1 FROM predictions p WHERE p.doc_id = d.doc_id"
            " AND p.text_version = t.version AND p.model = ? AND p.prompt_version = ?) ORDER BY d.doc_id",
            (model, prompt_version),
        )

    # ---- review queue ----------------------------------------------
    def review_queue(self) -> list[sqlite3.Row]:
        """Latest predictions that need review and have not been reviewed yet."""
        return self.q(
            "SELECT p.*, d.municipality, d.declared_type FROM predictions p JOIN documents d USING (doc_id)"
            " WHERE p.id = (SELECT MAX(id) FROM predictions WHERE doc_id = p.doc_id)"
            " AND p.status = 'needs_review'"
            " AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.doc_id = p.doc_id AND r.prediction_id = p.id)"
            " ORDER BY p.id"
        )

    def add_review(self, *, doc_id: str, prediction_id: int | None, reviewer: str, action: str,
                   record: dict | None) -> int:
        return self.run(
            "INSERT INTO reviews (doc_id, prediction_id, reviewer, action, record_json, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (doc_id, prediction_id, reviewer, action, dumps(record) if record else None, now()),
        )

    def latest_review(self, doc_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM reviews WHERE doc_id = ? ORDER BY id DESC LIMIT 1", (doc_id,))

    # ---- ground truth ----------------------------------------------
    def upsert_ground_truth(self, *, doc_id: str, doc_type: str, municipality: str | None,
                            number: str | None, date: str | None, subject: str | None,
                            evidence: dict | None, annotator: str) -> None:
        self.run(
            "INSERT INTO ground_truth (doc_id, doc_type, municipality, number, date, subject, evidence_json,"
            " annotator, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(doc_id) DO UPDATE SET doc_type=excluded.doc_type, municipality=excluded.municipality,"
            " number=excluded.number, date=excluded.date, subject=excluded.subject,"
            " evidence_json=excluded.evidence_json, annotator=excluded.annotator, updated_at=excluded.updated_at",
            (doc_id, doc_type, municipality, number, date, subject, dumps(evidence or {}), annotator, now()),
        )

    def get_ground_truth(self, doc_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM ground_truth WHERE doc_id = ?", (doc_id,))

    def all_ground_truth(self) -> list[sqlite3.Row]:
        return self.q("SELECT g.*, d.municipality AS doc_municipality FROM ground_truth g JOIN documents d USING (doc_id)")

    def documents_to_annotate(self) -> list[sqlite3.Row]:
        return self.q(
            "SELECT d.* FROM documents d WHERE EXISTS (SELECT 1 FROM text_versions t WHERE t.doc_id = d.doc_id)"
            " AND NOT EXISTS (SELECT 1 FROM ground_truth g WHERE g.doc_id = d.doc_id) ORDER BY d.doc_id"
        )

    # ---- stats -----------------------------------------------------
    def stats(self) -> dict[str, int]:
        def n(sql: str) -> int:
            return self.q(sql)[0][0]
        return {
            "documents": n("SELECT COUNT(*) FROM documents"),
            "with_text": n("SELECT COUNT(DISTINCT doc_id) FROM text_versions"),
            "predicted": n("SELECT COUNT(DISTINCT doc_id) FROM predictions"),
            "to_review": len(self.review_queue()),
            "ground_truth": n("SELECT COUNT(*) FROM ground_truth"),
        }
