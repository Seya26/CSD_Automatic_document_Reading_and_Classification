"""End to end on synthetic documents: ingest -> preprocess -> classify (fake LLM) -> review -> evaluate."""
import json

from albo.evaluation import evaluate, export_errors, format_report
from albo.ingestion import ingest_local
from albo.llm import classify_all
from albo.preprocessing import preprocess_all
from albo.review_queue import apply_review, final_records
from albo.schema import OutputRecord

from conftest import FakeClient, field, make_pdf

CAMERINO, MATELICA = "Comune di Camerino", "Comune di Matelica"

DOCS = {
    "d1": (CAMERINO, "Deliberazione di Giunta",
           "COMUNE DI CAMERINO\nDELIBERAZIONE DELLA GIUNTA COMUNALE\nN. 45 del 12/03/2025\n"
           "OGGETTO: Approvazione del piano triennale delle opere pubbliche 2025-2027.\n"
           "Vista la deliberazione n. 12 del 21/02/2025. LA GIUNTA COMUNALE DELIBERA di approvare il piano."),
    "d2": (CAMERINO, "Determinazione",
           "COMUNE DI CAMERINO\nDETERMINAZIONE DEL RESPONSABILE DEL SERVIZIO\nN. 210 del 03/04/2025\n"
           "OGGETTO: Affidamento del servizio di manutenzione del verde pubblico.\nIL RESPONSABILE DETERMINA di affidare il servizio."),
    "d3": (MATELICA, "Ordinanza",
           "COMUNE DI MATELICA\nORDINANZA SINDACALE\nN. 18 del 20/05/2025\n"
           "OGGETTO: Divieto di transito in via Roma per lavori di asfaltatura.\nIL SINDACO ORDINA il divieto di transito."),
    "d4": (MATELICA, "Avvisi",
           "COMUNE DI MATELICA\nAVVISO PUBBLICO\nSi comunica la chiusura degli uffici comunali il giorno 2 giugno per la festa nazionale."),
    "d5": (MATELICA, "Deliberazione di Consiglio",
           "COMUNE DI MATELICA\nDELIBERAZIONE DEL CONSIGLIO COMUNALE\nN. 7 del 10/01/2025\n"
           "OGGETTO: Approvazione del bilancio di previsione 2025.\nIl 15/01/2025 il bilancio è stato pubblicato. IL CONSIGLIO DELIBERA."),
}


def fake_model(user: str) -> dict:
    if "N. 45 del 12/03/2025" in user:        # d1: all correct, high confidence
        return {"doc_type": "DELIBERATION", "type_evidence": "DELIBERAZIONE DELLA GIUNTA COMUNALE", "type_confidence": 0.98,
                "municipality": field("Comune di Camerino", "COMUNE DI CAMERINO"),
                "number": field("45", "N. 45 del 12/03/2025"), "date": field("12/03/2025", "N. 45 del 12/03/2025"),
                "subject": field("Approvazione del piano triennale delle opere pubbliche 2025-2027.",
                                 "OGGETTO: Approvazione del piano triennale delle opere pubbliche 2025-2027.")}
    if "N. 210 del 03/04/2025" in user:       # d2: uncertain number -> review
        return {"doc_type": "DETERMINATION", "type_evidence": "DETERMINAZIONE DEL RESPONSABILE DEL SERVIZIO", "type_confidence": 0.96,
                "municipality": field("Comune di Camerino", "COMUNE DI CAMERINO"),
                "number": field("210", "N. 210 del 03/04/2025", 0.55), "date": field("03/04/2025", "N. 210 del 03/04/2025"),
                "subject": field("Affidamento del servizio di manutenzione del verde pubblico.",
                                 "OGGETTO: Affidamento del servizio di manutenzione del verde pubblico.")}
    if "N. 18 del 20/05/2025" in user:        # d3: invented evidence -> review
        return {"doc_type": "ORDINANCE", "type_evidence": "ORDINANZA SINDACALE", "type_confidence": 0.97,
                "municipality": field("Comune di Matelica", "COMUNE DI MATELICA"),
                "number": field("18", "N. 18 del 20/05/2025"), "date": field("20/05/2025", "N. 18 del 20/05/2025"),
                "subject": field("Chiusura del parco comunale", "OGGETTO: Chiusura del parco comunale per manutenzione")}
    if "AVVISO PUBBLICO" in user:             # d4: OTHER, no fields
        none = field(None, None, 0.0)
        return {"doc_type": "OTHER", "type_evidence": "AVVISO PUBBLICO", "type_confidence": 0.95,
                "municipality": none, "number": none, "date": none, "subject": none}
    # d5: confidently wrong date (publication date instead of the act date), evidence exists in the text
    return {"doc_type": "DELIBERATION", "type_evidence": "DELIBERAZIONE DEL CONSIGLIO COMUNALE", "type_confidence": 0.97,
            "municipality": field("Comune di Matelica", "COMUNE DI MATELICA"),
            "number": field("7", "N. 7 del 10/01/2025"), "date": field("15/01/2025", "Il 15/01/2025 il bilancio"),
            "subject": field("Approvazione del bilancio di previsione 2025.", "OGGETTO: Approvazione del bilancio di previsione 2025.")}


GROUND_TRUTH = {
    "d1": ("DELIBERATION", CAMERINO, "45", "12/03/2025", "Approvazione del piano triennale delle opere pubbliche 2025-2027."),
    "d2": ("DETERMINATION", CAMERINO, "210", "03/04/2025", "Affidamento del servizio di manutenzione del verde pubblico."),
    "d3": ("ORDINANCE", MATELICA, "18", "20/05/2025", "Divieto di transito in via Roma per lavori di asfaltatura."),
    "d4": ("OTHER", MATELICA, None, None, None),
    "d5": ("DELIBERATION", MATELICA, "7", "10/01/2025", "Approvazione del bilancio di previsione 2025."),
}


def build(env, tmp_path):
    settings, db = env
    ids = {}
    for key, (muni, declared, text) in DOCS.items():
        folder = tmp_path / "in" / muni
        folder.mkdir(parents=True, exist_ok=True)
        make_pdf(folder / f"{key}.pdf", text)
    for muni in (CAMERINO, MATELICA):
        folder = tmp_path / "in" / muni
        rows = ["filename,declared_type,published_at,source_url"]
        rows += [f"{k}.pdf,{d},2025-06-01," for k, (m, d, _) in DOCS.items() if m == muni]
        (folder / "metadata.csv").write_text("\n".join(rows), encoding="utf-8")
        ingest_local(db, settings, folder, muni)
    assert preprocess_all(db, settings)["new"] == 5
    for doc in db.list_documents():
        text = db.current_text(doc["doc_id"])["text"]
        key = next(k for k, v in DOCS.items() if v[2].splitlines()[1] in text and v[0] == doc["municipality"]
                   and v[2].splitlines()[-1][:25] in text.replace("\n", " ") or v[2][:60] in text)
        ids[key] = doc["doc_id"]
    return settings, db, ids


def test_full_flow(env, tmp_path):
    settings, db, ids = build(env, tmp_path)
    assert len(set(ids.values())) == 5

    # --- classification, validation, routing ---
    results = classify_all(db, settings, FakeClient(fake_model))
    assert results == {"auto_accepted": 3, "needs_review": 2, "error": 0}
    status = {k: db.latest_prediction(v)["status"] for k, v in ids.items()}
    assert status == {"d1": "auto_accepted", "d2": "needs_review", "d3": "needs_review",
                      "d4": "auto_accepted", "d5": "auto_accepted"}
    flagged = {k: json.loads(db.latest_prediction(v)["record_json"])["fields_to_review"] for k, v in ids.items() if status[k] == "needs_review"}
    assert flagged == {"d2": ["number"], "d3": ["subject"]}
    assert classify_all(db, settings, FakeClient(fake_model)) == {"auto_accepted": 0, "needs_review": 0, "error": 0}  # idempotent
    for prediction in db.q("SELECT record_json FROM predictions"):
        OutputRecord.model_validate_json(prediction["record_json"])  # every record matches the schema

    # --- human review ---
    assert {r["doc_id"] for r in db.review_queue()} == {ids["d2"], ids["d3"]}
    p2, p3 = db.latest_prediction(ids["d2"]), db.latest_prediction(ids["d3"])
    apply_review(db, doc_id=ids["d2"], prediction_id=p2["id"], reviewer="pie", action="correct", doc_type="DETERMINATION",
                 values={"municipality": "Comune di Camerino", "number": "210/2025", "date": "2025-04-03",
                         "subject": "Affidamento del servizio di manutenzione del verde pubblico."})
    apply_review(db, doc_id=ids["d3"], prediction_id=p3["id"], reviewer="pie", action="discard")
    assert db.review_queue() == []
    final = {r["doc_id"]: r for r in final_records(db)}
    assert set(final) == {ids["d1"], ids["d2"], ids["d4"], ids["d5"]}          # d3 discarded
    assert final[ids["d2"]]["status"] == "reviewed" and final[ids["d2"]]["number"]["value"] == "210/2025"
    assert final[ids["d2"]]["provenance"]["reviewer"] == "pie"
    assert final[ids["d1"]]["status"] == "auto_accepted"

    # --- ground truth and evaluation ---
    for key, (t, muni, number, date, subject) in GROUND_TRUTH.items():
        db.upsert_ground_truth(doc_id=ids[key], doc_type=t, municipality=muni if number else None, number=number,
                               date=date, subject=subject, evidence=None, annotator="pie")
    report = evaluate(db)
    overall = report["overall"]
    assert overall["n"] == 5
    assert overall["classification_accuracy"] == 1.0
    assert overall["straight_through_rate"] == 0.6                     # d1, d4, d5 skipped the review
    assert abs(overall["straight_through_precision"] - 2 / 3) < 1e-9   # ... but d5 slipped through with a wrong date
    assert overall["fields"]["date"]["correct"] == 3 and overall["fields"]["date"]["predicted"] == 4
    assert overall["fields"]["number"]["precision"] == 1.0
    assert report["confusion_matrix"]["OTHER"]["OTHER"] == 1
    assert set(report["by_municipality"]) == {CAMERINO, MATELICA}

    held_out = evaluate(db, only=MATELICA)
    assert held_out["overall"]["n"] == 3
    dev = evaluate(db, exclude=MATELICA)
    assert dev["overall"]["n"] == 2 and dev["overall"]["straight_through_precision"] == 1.0

    # --- error analysis export and report ---
    n = export_errors(db, tmp_path / "errors.csv")
    assert n == 2                                                      # d3 (wrong subject) and d5 (wrong date)
    csv_text = (tmp_path / "errors.csv").read_text(encoding="utf-8")
    assert ids["d3"] in csv_text and ids["d5"] in csv_text and ids["d1"] not in csv_text
    assert "Straight-through rate" in format_report(report)
