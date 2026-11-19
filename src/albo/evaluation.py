"""Evaluation (Gantt tasks 37-38, 40): classification accuracy, field precision, straight-through rate.

The model is evaluated on its AUTOMATIC output (latest prediction), never on the reviewed record."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

from .db import Database
from .taxonomy import CORE_TYPES, FIELDS, DocType
from .values import norm_municipality, norm_number, norm_text, parse_date

SUBJECT_THRESHOLD = 90  # similarity (0-100) above which two subjects count as equal


def values_match(field: str, truth: str | None, pred: str | None, subject_threshold: int = SUBJECT_THRESHOLD) -> bool:
    if not truth or not pred:
        return not truth and not pred
    if field == "municipality":
        return fuzz.ratio(norm_municipality(truth), norm_municipality(pred)) >= 90
    if field == "number":
        return norm_number(truth) == norm_number(pred) != ""
    if field == "date":
        return (parse_date(truth) or truth.strip()) == (parse_date(pred) or pred.strip())
    return fuzz.ratio(norm_text(truth), norm_text(pred)) >= subject_threshold


def _pairs(db: Database, only: str | None, exclude: str | None) -> tuple[list[dict], int]:
    pairs, missing = [], 0
    for gt in db.all_ground_truth():
        muni = gt["doc_municipality"]
        if (only and muni != only) or (exclude and muni == exclude):
            continue
        prediction = db.latest_prediction(gt["doc_id"])
        if prediction is None:
            missing += 1
            continue
        pairs.append({
            "doc_id": gt["doc_id"], "municipality": muni, "gt": dict(gt),
            "pred": json.loads(prediction["record_json"]), "status": prediction["status"],
        })
    return pairs, missing


def _field_results(pair: dict, subject_threshold: int) -> dict[str, tuple[str | None, str | None, bool]]:
    """field -> (truth, predicted, match); only for ground-truth types that carry the fields."""
    if DocType(pair["gt"]["doc_type"]) not in CORE_TYPES:
        return {}
    return {
        f: (pair["gt"][f], pair["pred"][f]["value"],
            values_match(f, pair["gt"][f], pair["pred"][f]["value"], subject_threshold))
        for f in FIELDS
    }


def _summarise(pairs: list[dict], subject_threshold: int) -> dict:
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    type_ok = sum(p["pred"]["doc_type"] == p["gt"]["doc_type"] for p in pairs)
    predicted = defaultdict(int)
    correct = defaultdict(int)
    present = defaultdict(int)
    doc_correct = []
    for p in pairs:
        results = _field_results(p, subject_threshold)
        for f, (truth, pred, ok) in results.items():
            predicted[f] += pred is not None
            present[f] += truth is not None
            correct[f] += ok and truth is not None
        doc_correct.append(p["pred"]["doc_type"] == p["gt"]["doc_type"] and all(r[2] for r in results.values()))
    auto = [i for i, p in enumerate(pairs) if p["status"] == "auto_accepted"]
    fields = {
        f: {
            "predicted": predicted[f], "correct": correct[f], "ground_truth_present": present[f],
            "precision": correct[f] / predicted[f] if predicted[f] else None,
            "recall": correct[f] / present[f] if present[f] else None,
        }
        for f in FIELDS
    }
    total_pred, total_correct = sum(predicted.values()), sum(correct.values())
    return {
        "n": n,
        "classification_accuracy": type_ok / n,
        "fields": fields,
        "field_precision_micro": total_correct / total_pred if total_pred else None,
        "straight_through_rate": len(auto) / n,
        "straight_through_precision": sum(doc_correct[i] for i in auto) / len(auto) if auto else None,
        "fully_correct_rate": sum(doc_correct) / n,
    }


def evaluate(db: Database, *, only: str | None = None, exclude: str | None = None,
             subject_threshold: int = SUBJECT_THRESHOLD) -> dict:
    """only: evaluate a single municipality (held-out test, task 40); exclude: development set."""
    pairs, missing = _pairs(db, only, exclude)
    report = {"filters": {"only": only, "exclude": exclude}, "subject_threshold": subject_threshold,
              "ground_truth_without_prediction": missing, "overall": _summarise(pairs, subject_threshold)}
    # confusion matrix and per-class metrics
    labels = [t.value for t in DocType]
    confusion = {t: {p: 0 for p in labels} for t in labels}
    for p in pairs:
        confusion[p["gt"]["doc_type"]][p["pred"]["doc_type"]] += 1
    per_class = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[o][label] for o in labels if o != label)
        fn = sum(confusion[label][o] for o in labels if o != label)
        per_class[label] = {
            "support": tp + fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        }
    report["confusion_matrix"] = confusion
    report["per_class"] = per_class
    # breakdowns (task 38)
    by_muni, by_type = defaultdict(list), defaultdict(list)
    for p in pairs:
        by_muni[p["municipality"]].append(p)
        by_type[p["gt"]["doc_type"]].append(p)
    report["by_municipality"] = {k: _summarise(v, subject_threshold) for k, v in sorted(by_muni.items())}
    report["by_document_type"] = {k: _summarise(v, subject_threshold) for k, v in sorted(by_type.items())}
    return report


def export_errors(db: Database, path: Path, *, only: str | None = None, exclude: str | None = None,
                  subject_threshold: int = SUBJECT_THRESHOLD) -> int:
    """CSV with one row per wrong document, for the error analysis (task 38)."""
    pairs, _ = _pairs(db, only, exclude)
    rows = []
    for p in pairs:
        results = _field_results(p, subject_threshold)
        wrong = [f for f, (_, _, ok) in results.items() if not ok]
        type_wrong = p["pred"]["doc_type"] != p["gt"]["doc_type"]
        if not wrong and not type_wrong:
            continue
        row = {
            "doc_id": p["doc_id"], "municipality": p["municipality"], "status": p["status"],
            "gt_type": p["gt"]["doc_type"], "pred_type": p["pred"]["doc_type"],
            "wrong_fields": ",".join(wrong), "flagged_for_review": ",".join(p["pred"]["fields_to_review"]),
        }
        for f in FIELDS:
            row[f"gt_{f}"] = p["gt"][f]
            row[f"pred_{f}"] = p["pred"][f]["value"]
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["doc_id", "municipality", "status", "gt_type", "pred_type", "wrong_fields", "flagged_for_review"] + \
              [f"{k}_{f}" for f in FIELDS for k in ("gt", "pred")]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def format_report(report: dict) -> str:
    o = report["overall"]
    if not o.get("n"):
        return "No documents with both ground truth and prediction."
    lines = [f"Documents evaluated: {o['n']}  (ground truth without prediction: {report['ground_truth_without_prediction']})"]
    flt = {k: v for k, v in report["filters"].items() if v}
    if flt:
        lines.append(f"Filters: {flt}")
    lines += [
        f"Classification accuracy:      {_pct(o['classification_accuracy'])}",
        f"Field precision (micro):      {_pct(o['field_precision_micro'])}",
        f"Straight-through rate:        {_pct(o['straight_through_rate'])}",
        f"  precision of auto-accepted: {_pct(o['straight_through_precision'])}",
        f"Fully correct documents:      {_pct(o['fully_correct_rate'])}",
        "", "Fields:      predicted  correct  precision  recall",
    ]
    for f, m in o["fields"].items():
        lines.append(f"  {f:<12}{m['predicted']:>6}{m['correct']:>9}{_pct(m['precision']):>11}{_pct(m['recall']):>9}")
    lines += ["", "Per class:   support  precision  recall"]
    for label, m in report["per_class"].items():
        lines.append(f"  {label:<14}{m['support']:>4}{_pct(m['precision']):>11}{_pct(m['recall']):>9}")
    for title, key in (("By municipality", "by_municipality"), ("By document type", "by_document_type")):
        lines += ["", f"{title}:      n   accuracy  field prec.  STP"]
        for name, m in report[key].items():
            lines.append(f"  {name[:28]:<28}{m['n']:>4}{_pct(m['classification_accuracy']):>10}"
                         f"{_pct(m['field_precision_micro']):>12}{_pct(m['straight_through_rate']):>8}")
    return "\n".join(lines)
