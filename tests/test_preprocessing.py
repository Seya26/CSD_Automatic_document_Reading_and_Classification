import pytest

from albo.preprocessing.normalize import normalize_text
from albo.preprocessing.pseudonymise import pseudonymise
from albo.preprocessing import preprocess_all
from albo.ingestion import ingest_bytes

from conftest import make_pdf, make_scanned_pdf, tesseract_available


def test_normalize_dehyphenates_and_cleans_whitespace():
    raw = "L’ammini-\nstrazione   comunale\u00a0delibera\r\n\r\n\r\n\r\nOggetto:  “piano”"
    out = normalize_text(raw)
    assert "amministrazione comunale delibera" in out
    assert "\n\n\n" not in out
    assert "'" in out and '"piano"' in out


def test_normalize_keeps_real_hyphens():
    assert normalize_text("decreto-legge") == "decreto-legge"


def test_pseudonymise_structured_identifiers():
    text = ("C.F. RSSMRA80A01H501U, IBAN IT60X0542811101000000123456, mail mario.rossi@example.it, "
            "Tel. 0737 402 123. Il Sig. Mario Rossi, nei confronti di Luigi Bianchi.")
    out, counts = pseudonymise(text)
    for leaked in ("RSSMRA80A01H501U", "IT60X0542811101000000123456", "mario.rossi@example.it", "0737 402 123"):
        assert leaked not in out
    assert "Il Sig. Mario Rossi, nei confronti di Luigi Bianchi." in out
    assert set(counts) == {"cf", "iban", "email", "phone"}


def test_pseudonymise_leaves_act_fields_alone():
    text = "COMUNE DI CAMERINO\nDELIBERAZIONE N. 45 del 12/03/2025\nOGGETTO: Approvazione del piano triennale"
    out, counts = pseudonymise(text)
    assert out == text and counts == {}


def test_preprocess_creates_versions_and_is_idempotent(env, tmp_path):
    settings, db = env
    pdf = make_pdf(tmp_path / "a.pdf", "COMUNE DI CAMERINO\n" + "Oggetto: prova di estrazione del testo. " * 8)
    doc_id, created = ingest_bytes(db, settings, pdf.read_bytes(), municipality="Comune di Camerino")
    assert created
    assert preprocess_all(db, settings) == {"new": 1, "unchanged": 0, "error": 0}
    assert preprocess_all(db, settings) == {"new": 0, "unchanged": 0, "error": 0}   # nothing left to do
    assert preprocess_all(db, settings, force=True)["unchanged"] == 1                # same text: no new version
    current = db.current_text(doc_id)
    assert current["version"] == 1 and current["extraction_method"] == "text"
    assert "COMUNE DI CAMERINO" in current["text"]


def test_duplicate_files_are_stored_once(env, tmp_path):
    settings, db = env
    data = make_pdf(tmp_path / "a.pdf", "COMUNE DI CAMERINO " * 20).read_bytes()
    first, created1 = ingest_bytes(db, settings, data, municipality="Comune di Camerino")
    second, created2 = ingest_bytes(db, settings, data, municipality="Comune di Camerino")
    assert created1 and not created2 and first == second
    assert len(db.list_documents()) == 1


def test_non_pdf_is_rejected(env):
    settings, db = env
    with pytest.raises(ValueError):
        ingest_bytes(db, settings, b"<html>not a pdf</html>", municipality="X")


def test_scanned_pdf_without_ocr_data_degrades_gracefully(env, tmp_path, monkeypatch):
    """Language data missing (or no tesseract): no crash, a warning is logged, the document is reported as error."""
    monkeypatch.setenv("ALBO_OCR_LANG", "zzz")
    from albo.config import get_settings

    settings = get_settings()
    _, db = env
    pdf = make_scanned_pdf(tmp_path / "scan.pdf", "DETERMINAZIONE N. 210 del 03/04/2025")
    ingest_bytes(db, settings, pdf.read_bytes(), municipality="Comune di Camerino")
    assert preprocess_all(db, settings) == {"new": 0, "unchanged": 0, "error": 1}
    messages = [r["message"] for r in db.q("SELECT message FROM processing_log WHERE stage = 'preprocess'")]
    assert any("OCR unavailable" in m for m in messages)


@pytest.mark.skipif(not tesseract_available(), reason="tesseract not installed")
def test_ocr_fallback_reads_scanned_pdf(env, tmp_path, monkeypatch):
    monkeypatch.setenv("ALBO_OCR_LANG", "eng")  # the sample is plain ASCII; `ita` needs tesseract-ocr-ita
    from albo.config import get_settings

    settings = get_settings()
    _, db = env
    pdf = make_scanned_pdf(tmp_path / "scan.pdf", "DETERMINATION number 210 of 03/04/2025\nSubject: grass cutting service")
    doc_id, _ = ingest_bytes(db, settings, pdf.read_bytes(), municipality="Comune di Camerino")
    assert preprocess_all(db, settings)["new"] == 1
    current = db.current_text(doc_id)
    assert current["extraction_method"] == "ocr" and current["extractor"] == "pymupdf+tesseract"
    assert "210" in current["text"] and "DETERMINATION" in current["text"].upper()
