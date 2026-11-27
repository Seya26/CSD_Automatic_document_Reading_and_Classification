from datetime import date

from albo.schema import LLMExtraction
from albo.validator import canonicalize, validate

from conftest import field

TEXT = """COMUNE DI CAMERINO
DELIBERAZIONE DELLA GIUNTA COMUNALE
N. 45 del 12/03/2025
OGGETTO: Approvazione del piano triennale delle opere pubbliche 2025-2027.
LA GIUNTA COMUNALE DELIBERA di approvare il piano."""


def good(**override):
    data = {
        "doc_type": "DELIBERATION", "type_evidence": "DELIBERAZIONE DELLA GIUNTA COMUNALE", "type_confidence": 0.97,
        "municipality": field("Comune di Camerino", "COMUNE DI CAMERINO"),
        "number": field("45", "N. 45 del 12/03/2025"),
        "date": field("12/03/2025", "N. 45 del 12/03/2025"),
        "subject": field("Approvazione del piano triennale delle opere pubbliche 2025-2027.",
                         "OGGETTO: Approvazione del piano triennale delle opere pubbliche 2025-2027."),
    }
    data.update(override)
    return canonicalize(LLMExtraction.model_validate(data))


def run(extraction, **kw):
    kw.setdefault("municipality", "Comune di Camerino")
    kw.setdefault("declared_type", "Deliberazione di Giunta")
    return validate(extraction, TEXT, conf_threshold=0.85, **kw)


def codes(result):
    return {(i.field, i.code) for i in result.issues}


def test_clean_record_passes_and_date_becomes_iso():
    extraction = good()
    assert extraction.date.value == "2025-03-12"
    assert run(extraction).ok


def test_low_confidence_flags_only_that_field():
    result = run(good(number=field("45", "N. 45 del 12/03/2025", 0.6)))
    assert codes(result) == {("number", "low_confidence")}
    assert result.fields_to_review == ["number"]


def test_hallucinated_evidence_is_caught():
    result = run(good(subject=field("Affidamento servizio mensa", "OGGETTO: Affidamento servizio mensa scolastica")))
    assert ("subject", "evidence_not_found") in codes(result)


def test_evidence_tolerates_ocr_noise_and_case():
    result = run(good(municipality=field("Comune di Camerino", "C0MUNE DI CAMERlNO")))
    assert result.ok


def test_missing_required_field_for_core_type():
    result = run(good(number=field(None, None, 0.0)))
    assert ("number", "missing_value") in codes(result)


def test_other_type_does_not_require_fields():
    extraction = good(
        doc_type="OTHER", type_evidence="COMUNE DI CAMERINO",
        municipality=field(None, None, 0.0), number=field(None, None, 0.0),
        date=field(None, None, 0.0), subject=field(None, None, 0.0),
    )
    assert run(extraction, declared_type=None).ok


def test_uncertain_always_goes_to_review():
    extraction = good(doc_type="UNCERTAIN", type_evidence=None, type_confidence=0.4)
    assert ("doc_type", "uncertain_type") in codes(run(extraction))


def test_declared_type_mismatch():
    result = run(good(), declared_type="Ordinanza sindacale")
    assert ("doc_type", "declared_type_mismatch") in codes(result)


def test_municipality_mismatch_with_notice_board():
    result = run(good(), municipality="Comune di Matelica")
    assert ("municipality", "municipality_mismatch") in codes(result)


def test_bad_formats():
    result = run(good(date=field("31/02/2025", "N. 45 del 12/03/2025"), number=field("senza", "N. 45 del 12/03/2025")))
    assert {("date", "bad_format"), ("number", "bad_format")} <= codes(result)


def test_evidence_with_altered_content_is_caught():
    """A quote whose years were changed is not 'noise': it must be flagged."""
    altered = good(subject=field("Approvazione del piano triennale delle opere 2026-2028",
                                 "Approvazione del piano triennale delle opere 2026-2028"))
    assert ("subject", "evidence_not_found") in codes(run(altered))


def test_value_must_be_in_its_evidence():
    # the excerpt exists in the text, but says 45, not 54
    result = run(good(number=field("54", "N. 45 del 12/03/2025")))
    assert ("number", "value_not_in_evidence") in codes(result)
    # same for a date that the excerpt does not contain
    result = run(good(date=field("13/03/2025", "N. 45 del 12/03/2025")))
    assert ("date", "value_not_in_evidence") in codes(result)


def test_subject_shorter_than_evidence_is_still_supported():
    result = run(good(subject=field("Approvazione del piano triennale delle opere pubbliche 2025-2027.",
                                    "Approvazione del piano triennale delle opere pubbliche")))
    assert ("subject", "value_not_in_evidence") not in codes(result)


def test_date_after_publication_and_in_future():
    assert ("date", "date_after_publication") in codes(run(good(), published_at="2025-01-10"))
    assert ("date", "date_after_publication") not in codes(run(good(), published_at="2025-06-01"))
    assert ("date", "date_in_future") in codes(run(good(), today=date(2024, 1, 1)))


def test_date_from_cited_act_is_flagged():
    text_evidence = "Vista la deliberazione n. 12 del 21/02/2025"
    extraction = good(date=field("21/02/2025", text_evidence))
    result = validate(extraction, TEXT + "\n" + text_evidence, municipality="Camerino",
                      declared_type=None, conf_threshold=0.85)
    assert ("date", "date_from_cited_act") in codes(result)
    assert ("date", "date_from_cited_act") not in codes(run(good()))


def test_delibera_fields_require_their_own_evidence():
    from albo.validator import value_supported

    assert value_supported("classifica", "6.5", "Classifica 6.5")
    assert not value_supported("classifica", "6", "Classifica 6.5")
