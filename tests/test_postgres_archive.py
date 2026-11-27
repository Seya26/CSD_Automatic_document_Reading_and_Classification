"""Run against an empty disposable archive by setting ALBO_TEST_POSTGRES=1."""
import json
import os

import pytest

from albo.llm import classify_all
from albo.postgres_archive import archive, publish
from albo.review_queue import apply_review
from conftest import FakeClient
from test_pipeline import build, fake_model


@pytest.mark.skipif(os.getenv("ALBO_TEST_POSTGRES") != "1", reason="requires disposable PostgreSQL")
def test_archive_twice(env, tmp_path):
    import psycopg

    settings, db, ids = build(env, tmp_path)
    archive(db)
    with psycopg.connect() as conn:
        assert conn.execute("SELECT count(*) FROM archivio_comunale.file_originali WHERE documento_id IS NULL").fetchone()[0] == 5
    classify_all(db, settings, FakeClient(fake_model))
    for key in ("d2", "d3"):
        doc_id = ids[key]
        apply_review(db, doc_id=doc_id, prediction_id=db.latest_prediction(doc_id)["id"],
                     reviewer="test", action="approve")
    archive(db)
    archive(db)
    with psycopg.connect() as conn:
        conn.execute("SET search_path TO archivio_comunale, pg_catalog")
        for table, count in (("documenti", 4), ("file_originali", 5), ("delibere", 2),
                             ("determinazioni", 1), ("ordinanze", 1)):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == count
        assert conn.execute("SELECT count(DISTINCT documento_id) FROM file_originali").fetchone()[0] == 4
        pred = db.latest_prediction(ids["d1"])
        enriched = json.loads(pred["record_json"])
        for key, value in (("numero_documento", "390"), ("classifica", "6.5"),
                           ("organo_deliberante", "Giunta comunale"), ("esito_votazione", "voti unanimi")):
            enriched[key] = {"value": value}
        file_id = conn.execute("SELECT id FROM file_originali WHERE encode(sha256,'hex') LIKE %s",
                               (ids["d1"] + "%",)).fetchone()[0]
        publish(conn, file_id, {"record_json": json.dumps(enriched)}, None)
        row = conn.execute("SELECT b.numero_delibera,b.classifica,"
                           "b.organo_deliberante,b.esito_votazione FROM documenti d JOIN delibere b "
                           "ON b.documento_id=d.id JOIN file_originali f ON f.documento_id=d.id "
                           "WHERE f.id=%s", (file_id,)).fetchone()
        assert row == (enriched["number"]["value"], "6.5", "Giunta comunale", "voti unanimi")
        scan, present = conn.execute(
            "SELECT * FROM acquisisci_file(%s,%s,%s)", (b"scan fixture", "scan.pdf", "application/pdf")
        ).fetchone()
        assert not present
        assert "possibile copia" in publish(conn, scan, db.latest_prediction(ids["d1"]), None)
        assert conn.execute("SELECT documento_id FROM file_originali WHERE id=%s", (scan,)).fetchone() == (None,)


def test_publish_determination_mapping():
    from unittest.mock import MagicMock
    conn = MagicMock()
    record = {"doc_type": "DETERMINATION"}
    for key, value in {"municipality": "Comune di Pesaro", "number": "2748", "date": "15/10/2026",
                       "subject": "Opposizione avverso verbale", "numero_documento": "3085",
                       "ufficio_responsabile": "Servizio Ufficio Verbali", "firmatario": "Francesca Muzzini"}.items():
        record[key] = {"value": value}
    conn.execute.return_value.fetchone.side_effect = [(None,), (42,)]
    conn.execute.return_value.fetchall.return_value = []
    assert "scheda archiviata" in publish(conn, 1, {"record_json": json.dumps(record)}, None)
    calls = [c.args for c in conn.execute.call_args_list]
    assert any("INSERT INTO documenti(" in c[0] and c[1] == ("determinazione", "Comune di Pesaro", "2026-10-15", "Opposizione avverso verbale") for c in calls)
    assert any("INSERT INTO determinazioni(" in c[0] and c[1] == (42, "2748", "Servizio Ufficio Verbali", "Francesca Muzzini") for c in calls)
    assert all("numero_atto" not in c[0] for c in calls)


@pytest.mark.skipif(os.getenv("ALBO_TEST_POSTGRES") != "1", reason="requires disposable PostgreSQL")
@pytest.mark.parametrize("doc_type, collection, lines, table, columns, expected", [
    ("ORDINANCE", "luoghi", "Largo Volontari del Sangue\nVia Roma", "ordinanze_luoghi",
     "luogo_interessato", [("Largo Volontari del Sangue",), ("Via Roma",)]),
    ("DELIBERATION", "firmatari", "Mario Rossi | Presidente\nAnna Verdi | Segretaria",
     "delibere_firmatari", "nome, ruolo", [("Mario Rossi", "Presidente"), ("Anna Verdi", "Segretaria")]),
])
def test_archive_repeated_details(doc_type, collection, lines, table, columns, expected):
    import psycopg

    values = {"municipality": "Comune di Pesaro", "number": "2194", "date": "21/10/2026",
              "subject": "Regolamentazione della viabilita", collection: lines}
    if doc_type == "ORDINANCE":
        values.update(organo_emittente="Direzione Sicurezza Urbana Integrata", numero_proposta="2272",
                      valida_dal="28/10/2026", valida_al="08/11/2026", firmatario="Francesca Muzzini",
                      responsabile_procedimento="Barbara Savelli", prescrizioni="Divieto di sosta")
    prediction = {"record_json": json.dumps({"doc_type": doc_type,
                                 **{key: {"value": value} for key, value in values.items()}})}
    with psycopg.connect() as conn:
        conn.execute("SET search_path TO archivio_comunale, pg_catalog")
        file_id = conn.execute("SELECT file_id FROM acquisisci_file(%s,%s,%s)",
                               (doc_type.encode(), "esempio.pdf", "application/pdf")).fetchone()[0]
        assert "scheda archiviata" in publish(conn, file_id, prediction, None)
        assert "già archiviato" in publish(conn, file_id, prediction, None)
        doc_id = conn.execute("SELECT documento_id FROM file_originali WHERE id=%s", (file_id,)).fetchone()[0]
        assert conn.execute(f"SELECT {columns} FROM {table} WHERE documento_id=%s ORDER BY id",
                            (doc_id,)).fetchall() == expected
        if doc_type == "ORDINANCE":
            assert conn.execute("SELECT o.numero_ordinanza "
                                "FROM documenti d JOIN ordinanze o ON o.documento_id=d.id WHERE d.id=%s",
                                (doc_id,)).fetchone() == ("2194",)
        conn.rollback()
