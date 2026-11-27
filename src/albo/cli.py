"""Command line interface: python -m albo <command> --help"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .config import get_settings
from .db import Database


def _db() -> tuple[Database, "object"]:
    settings = get_settings()
    return Database(settings.db_path), settings


def cmd_stats(args: argparse.Namespace) -> None:
    db, _ = _db()
    for key, value in db.stats().items():
        print(f"{key:<14}{value}")


def cmd_export_schema(args: argparse.Namespace) -> None:
    from .schema import OutputRecord

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(OutputRecord.model_json_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON Schema written to {out}")


def cmd_ingest(args: argparse.Namespace) -> None:
    from .ingestion import run_ingest

    db, settings = _db()
    print(run_ingest(db, settings, args.municipality, args.limit))


def cmd_ingest_local(args: argparse.Namespace) -> None:
    from .ingestion import ingest_local

    db, settings = _db()
    print(ingest_local(db, settings, Path(args.dir), args.municipality))


def cmd_preprocess(args: argparse.Namespace) -> None:
    from .preprocessing import preprocess_all

    db, settings = _db()
    print(preprocess_all(db, settings, force=args.force))


def cmd_classify(args: argparse.Namespace) -> None:
    from .llm import classify_all
    from .llm.client import create_client

    db, settings = _db()
    client = create_client(settings)
    print(classify_all(db, settings, client, force=args.force, limit=args.limit))


def cmd_evaluate(args: argparse.Namespace) -> None:
    from .evaluation import evaluate, export_errors, format_report

    db, _ = _db()
    report = evaluate(db, only=args.only, exclude=args.exclude)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    if args.errors_csv:
        n = export_errors(db, Path(args.errors_csv), only=args.only, exclude=args.exclude)
        print(f"\n{n} wrong documents written to {args.errors_csv}", file=sys.stderr)


def cmd_export_final(args: argparse.Namespace) -> None:
    from .review_queue import final_records

    db, _ = _db()
    records = final_records(db)
    with Path(args.out).open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"{len(records)} final records written to {args.out}")


def cmd_export_ground_truth(args: argparse.Namespace) -> None:
    db, _ = _db()
    rows = db.all_ground_truth()
    columns = ["doc_id", "doc_municipality", "doc_type", "municipality", "number", "date", "subject", "annotator", "updated_at"]
    with Path(args.out).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[c] for c in columns])
    print(f"{len(rows)} annotations written to {args.out}")


def cmd_archive(args: argparse.Namespace) -> None:
    from .postgres_archive import archive

    db, _ = _db()
    archive(db, args.check)


def cmd_process_file(args: argparse.Namespace) -> None:
    from .ingestion.ingest import ingest_bytes
    from .llm.client import create_client
    from .llm.classify import classify_document
    from .llm.prompts import PROMPT_VERSION
    from .postgres_archive import archive
    from .preprocessing.store import preprocess_document

    db, settings = _db()
    path = Path(args.pdf)
    doc_id, created = ingest_bytes(db, settings, path.read_bytes(), municipality=args.municipality, original_name=path.name)
    print(f"1/4 {path.name} ({doc_id}): " + ("acquisito" if created else "già presente"))

    def raise_stage_error(stage: str) -> None:
        entry = db.one("SELECT message FROM processing_log WHERE doc_id=? AND stage=? AND status='error'"
                       " ORDER BY id DESC LIMIT 1", (doc_id, stage))
        raise RuntimeError(f"{stage}: {entry['message'] if entry else 'errore'}")

    if preprocess_document(db, settings, doc_id) == "error":
        raise_stage_error("preprocess")
    current = db.current_text(doc_id)
    print(f"2/4 {current['extraction_method']}: {current['n_pages']} pagine, {current['n_chars']} caratteri")
    prediction = db.latest_prediction(doc_id)
    if (prediction and prediction["text_version"] == current["version"]
            and prediction["model"] == settings.classification_model and prediction["prompt_version"] == PROMPT_VERSION):
        print("3/4 Classificazione già disponibile")
    else:
        client = create_client(settings)
        print(f"3/4 Classificazione con {client.model}...", flush=True)
        if classify_document(db, settings, client, doc_id) == "error":
            raise_stage_error("classify")
        prediction = db.latest_prediction(doc_id)
    record = json.loads(prediction["record_json"])
    print(f"Proposta AI: {record['doc_type']} ({prediction['status']})")
    from .taxonomy import record_fields
    for field in record_fields(record["doc_type"]):
        print(f"  {field}: {record.get(field, {}).get('value')}")
    print("4/4 Archiviazione del PDF in PostgreSQL...", flush=True)
    archive(db, doc_id=doc_id)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="albo", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="counts per pipeline stage").set_defaults(func=cmd_stats)
    p = sub.add_parser("process-file", help="process and archive one local PDF")
    p.add_argument("pdf", type=Path)
    p.add_argument("--municipality", required=True, help="comune di provenienza")
    p.set_defaults(func=cmd_process_file)
    p = sub.add_parser("archive-postgres", help="archive originals and approved records in PostgreSQL")
    p.add_argument("--check", action="store_true", help="check the connection and schema only")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("export-schema", help="write the JSON Schema of the output record")
    p.add_argument("--out", default="config/output_record.schema.json")
    p.set_defaults(func=cmd_export_schema)

    p = sub.add_parser("ingest", help="download documents from a configured notice board")
    p.add_argument("--municipality", required=True, help="slug in config/municipalities.yaml")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ingest-local", help="import PDFs already downloaded by hand")
    p.add_argument("--dir", required=True)
    p.add_argument("--municipality", required=True, help='display name, e.g. "Comune di Camerino"')
    p.set_defaults(func=cmd_ingest_local)

    p = sub.add_parser("preprocess", help="extract, normalise and store the text")
    p.add_argument("--force", action="store_true", help="reprocess documents that already have text")
    p.set_defaults(func=cmd_preprocess)

    p = sub.add_parser("classify", help="classify and extract fields with the LLM, validate, route")
    p.add_argument("--force", action="store_true", help="also documents already predicted with this model/prompt")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("evaluate", help="metrics against the ground truth")
    p.add_argument("--only", help="evaluate a single municipality (held-out test)")
    p.add_argument("--exclude", help="exclude a municipality (development set)")
    p.add_argument("--errors-csv", help="write the wrong documents to this CSV")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("export-final", help="final JSON records (reviewed or auto-accepted) as JSON Lines")
    p.add_argument("--out", default="data/final_records.jsonl")
    p.set_defaults(func=cmd_export_final)

    p = sub.add_parser("export-ground-truth", help="ground truth annotations as CSV")
    p.add_argument("--out", default="data/ground_truth.csv")
    p.set_defaults(func=cmd_export_ground_truth)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (RuntimeError, KeyError, OSError, ValueError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"error: {message}", file=sys.stderr)
        sys.exit(1)
