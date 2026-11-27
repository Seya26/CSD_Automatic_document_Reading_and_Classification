"""Validator of the model output (Gantt task 34).

Every check that fails produces an Issue; the affected field is added to `fields_to_review`
and the document goes to the human review queue."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date

from rapidfuzz import fuzz

from .schema import LLMExtraction
from .taxonomy import CORE_TYPES, FIELDS, DETAIL_FIELDS, record_fields, DocType, map_declared_type
from .values import all_dates, norm_municipality, norm_number, norm_text, number_tokens, parse_date


@dataclass
class Issue:
    field: str  # "doc_type" or one of FIELDS
    code: str
    message: str


@dataclass
class ValidationResult:
    issues: list[Issue]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def fields_to_review(self) -> list[str]:
        seen: list[str] = []
        for issue in self.issues:
            if issue.field not in seen:
                seen.append(issue.field)
        return seen

    def as_dicts(self) -> list[dict]:
        return [asdict(i) for i in self.issues]


# Minimum rapidfuzz partial_ratio (0-100) for an evidence excerpt to count as "found in the text".
# Chosen on a handful of examples: OCR-noisy copies score ~89, altered quotes ~83, invented ones < 55.
# Tune it on the real development set (see the evaluation, task 38).
EVIDENCE_MIN_SIMILARITY = 85


def evidence_in_text(evidence: str, text_norm: str, threshold: int = EVIDENCE_MIN_SIMILARITY) -> bool:
    """Is the evidence (almost) verbatim in the document? Tolerates OCR noise and spacing."""
    ev = norm_text(evidence)
    if not ev:
        return False
    if ev in text_norm:
        return True
    return fuzz.partial_ratio(ev, text_norm) >= threshold


def value_supported(name: str, value: str, evidence: str) -> bool:
    """Is the extracted value actually contained in its own evidence excerpt?
    (An excerpt can exist in the text and still not say what the model claims.)
    Unparsable numbers/dates return True: they are reported as `bad_format` instead."""
    if name == "classifica":
        label = r"\bclassifica"
        return bool(re.search(label + r"\s*[:°º.]?\s*" + re.escape(value.strip()) + r"(?![\w./-])",
                              evidence, re.IGNORECASE))
    if name == "number":
        wanted = [int(p) for p in norm_number(value).split("/") if p]
        return not wanted or all(w in number_tokens(evidence) for w in wanted)
    if name in ("date", "valida_dal", "valida_al"):
        iso = parse_date(value)
        return iso is None or iso in all_dates(evidence)
    v, e = norm_text(value), norm_text(evidence)
    if not v or not e:
        return False
    if v in e or e in v:  # the excerpt may be shorter than a long subject, or longer than a short name
        return True
    short, long_ = (v, e) if len(v) <= len(e) else (e, v)
    return fuzz.partial_ratio(short, long_) >= EVIDENCE_MIN_SIMILARITY


_CITATION = re.compile(r"\b(?:vist[oai]|richiamat[oai]|in esecuzione|ai sensi)\b", re.I)
_ACT_NUMBER = re.compile(r"\bn(?:\.|°|º|r\.?)?\s*(\d+)", re.I)


def cites_other_act(date_evidence: str, number: str | None) -> bool:
    """The date excerpt is about a cited act or document ('Vista la deliberazione n. 12 del ...'),
    or carries a number that is not the act's own (e.g. the protocol number)."""
    if _CITATION.search(date_evidence):
        return True
    own = [int(p) for p in norm_number(number).split("/") if p]
    return bool(own) and any(int(n) not in own for n in _ACT_NUMBER.findall(date_evidence))


def canonicalize(extraction: LLMExtraction) -> LLMExtraction:
    """Clean values in place: trimmed strings, ISO dates when parsable."""
    for name in (*FIELDS, *sorted(DETAIL_FIELDS)):
        field = getattr(extraction, name)
        if field.value is not None:
            if name in ("luoghi", "firmatari"):
                field.value = "\n".join(line.strip() for line in field.value.splitlines() if line.strip()) or None
            else:
                field.value = re.sub(r"\s+", " ", field.value).strip() or None
        if field.evidence is not None:
            field.evidence = field.evidence.strip() or None
    if extraction.date.value:
        iso = parse_date(extraction.date.value)
        if iso:
            extraction.date.value = iso
    return extraction


def validate(extraction: LLMExtraction, text: str, *, municipality: str | None,
             declared_type: str | None, conf_threshold: float,
             published_at: str | None = None, today: date | None = None) -> ValidationResult:
    issues: list[Issue] = []
    text_norm = norm_text(text)

    def add(field: str, code: str, message: str) -> None:
        issues.append(Issue(field, code, message))

    # --- type ---
    if extraction.doc_type == DocType.UNCERTAIN:
        add("doc_type", "uncertain_type", "The model could not decide the document type")
    if extraction.type_confidence < conf_threshold:
        add("doc_type", "low_confidence", f"Type confidence {extraction.type_confidence:.2f} < {conf_threshold}")
    if extraction.doc_type != DocType.UNCERTAIN:
        if not extraction.type_evidence:
            add("doc_type", "missing_evidence", "No evidence for the document type")
        elif not evidence_in_text(extraction.type_evidence, text_norm):
            add("doc_type", "evidence_not_found", "Type evidence not found in the text")
    declared = map_declared_type(declared_type)
    if declared and extraction.doc_type not in (DocType.UNCERTAIN, declared):
        add("doc_type", "declared_type_mismatch",
            f"Predicted {extraction.doc_type.value}, the notice board declares {declared_type!r}")

    # --- fields (not required for OTHER / UNCERTAIN) ---
    required = extraction.doc_type in CORE_TYPES
    for name in (*FIELDS, *sorted(DETAIL_FIELDS)):
        field = getattr(extraction, name)
        if field.value is None:
            if required and name in FIELDS:
                add(name, "missing_value", f"{name} is required for {extraction.doc_type.value} but was not found")
            continue
        if name not in record_fields(extraction.doc_type):
            add(name, "wrong_category", "Field does not belong to this category")
        if name in ("valida_dal", "valida_al") and not parse_date(field.value):
            add(name, "bad_format", "Invalid validity date")
        if field.confidence < conf_threshold:
            add(name, "low_confidence", f"Confidence {field.confidence:.2f} < {conf_threshold}")
        if not field.evidence:
            add(name, "missing_evidence", "Value without evidence")
        elif not evidence_in_text(field.evidence, text_norm):
            add(name, "evidence_not_found", "Evidence not found in the text (possible hallucination)")
        elif not value_supported(name, field.value, field.evidence):
            add(name, "value_not_in_evidence", "The value is not contained in its evidence excerpt")

    for name in ("firmatario", "responsabile_procedimento", "firmatari"):
        value = getattr(extraction, name).value or ""
        if "[NOME]" in value.upper():
            add(name, "redacted_name", "Verify the masked name in the original PDF")
    if extraction.doc_type == DocType.ORDINANCE and not extraction.responsabile_procedimento.value:
        if re.search(r"responsabile\s+del\s+procedimento\s*:\s*(?:dott[.\w]*\s+)?\[NOME\]", text, re.I):
            add("responsabile_procedimento", "redacted_name", "Verify the procedure manager in the original PDF")
    start, end = parse_date(extraction.valida_dal.value), parse_date(extraction.valida_al.value)
    if start and end and start > end:
        add("valida_al", "invalid_range", "Validity ends before it starts")
    for name in ("luoghi", "firmatari"):
        value = getattr(extraction, name).value or ""
        parts = (part for line in value.splitlines() for part in (line.split("|") if name == "firmatari" else [line]))
        if any(norm_text(part) not in text_norm for part in parts if part.strip()):
            add(name, "value_not_in_text", "Location, signer or role missing from source text")

    # --- format / consistency checks ---
    number, date_, subject, muni = extraction.number, extraction.date, extraction.subject, extraction.municipality
    if number.value and not norm_number(number.value):
        add("number", "bad_format", f"No digits in number {number.value!r}")
    if date_.value:
        iso = parse_date(date_.value)
        if iso is None:
            add("date", "bad_format", f"Unparsable date {date_.value!r}")
        else:
            if iso > (today or date.today()).isoformat():
                add("date", "date_in_future", f"Act date {iso} is in the future")
            published = parse_date(published_at)
            if published and iso > published:
                add("date", "date_after_publication", f"Act date {iso} is later than the publication date {published}")
            if date_.evidence and cites_other_act(date_.evidence, number.value):
                add("date", "date_from_cited_act", "The date excerpt refers to another act or document")
    if subject.value and not 10 <= len(subject.value) <= 800:
        add("subject", "bad_length", f"Subject length {len(subject.value)} outside 10-800 characters")
    if muni.value and municipality:
        if fuzz.ratio(norm_municipality(muni.value), norm_municipality(municipality)) < 85:
            add("municipality", "municipality_mismatch",
                f"Extracted {muni.value!r}, the notice board is {municipality!r}")
    return ValidationResult(issues)
