"""Export local pipeline PDFs and approved records to the municipal archive."""
from __future__ import annotations

import hashlib
import json
import os
from getpass import getpass
from sqlite3 import Row
from typing import TYPE_CHECKING

from .config import get_settings, resolve_raw_pdf
from .db import Database
from .values import parse_date

if TYPE_CHECKING:
    from psycopg import Connection
    from psycopg.rows import TupleRow


TYPES = {
    "DELIBERATION": ("delibera", "delibere"),
    "DETERMINATION": ("determinazione", "determinazioni"),
    "ORDINANCE": ("ordinanza", "ordinanze"),
}


def store_original(conn: Connection[TupleRow], doc: Row) -> tuple[int, bool]:
    existing = conn.execute(
        "SELECT id, nome_originale FROM file_originali WHERE sha256=%s", (bytes.fromhex(doc["sha256"]),)
    ).fetchone()
    name = doc["original_name"]
    fallback = f"{doc['doc_id']}.pdf"
    if existing:
        if name and existing[1] == fallback:
            conn.execute("UPDATE file_originali SET nome_originale=%s WHERE id=%s AND nome_originale=%s",
                         (name, existing[0], fallback))
        return existing[0], True

    path = resolve_raw_pdf(doc["raw_path"], get_settings().raw_dir)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != doc["sha256"]:
        raise ValueError("Il PDF locale differisce da quello acquisito")
    acquired = conn.execute(
        "SELECT file_id, gia_presente FROM acquisisci_file(%s,%s,%s)",
        (data, name or path.name, "application/pdf"),
    ).fetchone()
    if acquired is None:
        raise RuntimeError("acquisisci_file non ha restituito il file acquisito")
    return acquired[0], acquired[1]


def publish(conn, file_id, prediction, review):
    record = json.loads((review or prediction)["record_json"])
    if record["doc_type"] not in TYPES:
        return "categoria fuori archivio"
    category, table = TYPES[record["doc_type"]]
    fields = [record[key]["value"] for key in ("municipality", "number", "date", "subject")]
    fields[2] = parse_date(fields[2])
    if not all(fields):
        return "campi obbligatori incompleti"

    # Serialize the candidate lookup and insertion across simultaneous exports.
    conn.execute("LOCK TABLE documenti IN SHARE ROW EXCLUSIVE MODE")
    linked = conn.execute(
        "SELECT documento_id FROM file_originali WHERE id=%s FOR NO KEY UPDATE", (file_id,)
    ).fetchone()[0]
    # The act number belongs to the category-specific detail table.
    number_column, detail_columns = {
        "ordinanza": ("numero_ordinanza", ("organo_emittente", "valida_dal", "valida_al", "prescrizioni",
                                         "firmatario", "responsabile_procedimento")),
        "delibera": ("numero_delibera", ("classifica", "organo_deliberante", "esito_votazione")),
        "determinazione": ("numero_determinazione", ("ufficio_responsabile", "firmatario")),
    }[category]
    # SQL identifiers are exclusively fixed constants above, not model input.
    selected = ", ".join(
        f"to_char(b.{key},'YYYY-MM-DD')" if key in ("valida_dal", "valida_al") else f"b.{key}"
        for key in detail_columns
    )
    lookup = f"""
        SELECT d.id, d.categoria, d.ente, b.{number_column} AS numero,
               to_char(d.data_atto,'YYYY-MM-DD') AS data, d.oggetto,
               {selected}
        FROM documenti d JOIN {table} b ON b.documento_id=d.id
    """
    detail_values = {key: record.get(key, {}).get("value") for key in detail_columns}
    if category == "ordinanza":
        for key in ("valida_dal", "valida_al"):
            if detail_values[key]:
                detail_values[key] = parse_date(detail_values[key])
                if detail_values[key] is None:
                    return "data di validita non valida"
        start, end = detail_values["valida_dal"], detail_values["valida_al"]
        if start and end and start > end:
            return "intervallo di validita non valido"
    details = tuple(detail_values.values())
    children = []
    child_table = child_columns = None
    if category == "ordinanza":
        child_table, child_columns = "ordinanze_luoghi", "luogo_interessato"
        children = [(line.strip(),) for line in (record.get("luoghi", {}).get("value") or "").splitlines()
                    if line.strip()]
    elif category == "delibera":
        child_table, child_columns = "delibere_firmatari", "nome, ruolo"
        for line in (record.get("firmatari", {}).get("value") or "").splitlines():
            if line.strip():
                name, _, role = line.partition("|")
                if not name.strip():
                    return "nome del firmatario mancante"
                children.append((name.strip(), role.strip() or None))

    def store_children(document_id):
        for row in children:
            params = ", ".join(["%s"] * (1 + len(row)))
            conn.execute(f"INSERT INTO {child_table}(documento_id, {child_columns}) VALUES ({params}) "
                         "ON CONFLICT DO NOTHING", (document_id, *row))

    if linked is not None:
        previous = conn.execute(lookup + " WHERE d.id=%s", (linked,)).fetchone()
        if previous is None or previous[1:6] != (category, *fields):
            return "conflitto con la scheda approvata"
        if any(old is not None and new is not None and old != new
               for old, new in zip(previous[6:], details)):
            return "conflitto con la scheda approvata"
        if children:
            existing = conn.execute(f"SELECT {child_columns} FROM {child_table} WHERE documento_id=%s",
                                    (linked,)).fetchall()
            if existing and not set(existing).issubset(children):
                return "conflitto con la scheda approvata"
        if any(old is None and new is not None for old, new in zip(previous[6:], details)):
            assignments = ", ".join(f"{key}=coalesce({key},%s)" for key in detail_columns)
            conn.execute(f"UPDATE {table} SET {assignments} WHERE documento_id=%s", (*details, linked))
        store_children(linked)
        return "già archiviato"

    candidates = conn.execute(
        "SELECT id FROM (" + lookup + ") AS atti WHERE categoria=%s AND lower(ente)=lower(%s)"
        " AND numero=%s AND data=%s ORDER BY id", (category, *fields[:3])
    ).fetchall()
    if candidates:
        ids = ", ".join(str(row[0]) for row in candidates)
        return f"possibile copia, verificare il collegamento con documento {ids}"

    ente, act_number, data_atto, oggetto = fields
    document_id = conn.execute(
        "INSERT INTO documenti(categoria,ente,data_atto,oggetto)"
        " VALUES (%s,%s,%s,%s) RETURNING id",
        (category, ente, data_atto, oggetto),
    ).fetchone()[0]
    columns = ", ".join(("documento_id", number_column, *detail_columns))
    placeholders = ", ".join(["%s"] * (2 + len(details)))
    conn.execute(f"INSERT INTO {table}({columns}) VALUES ({placeholders})", (document_id, act_number, *details))
    store_children(document_id)
    conn.execute("UPDATE file_originali SET documento_id=%s WHERE id=%s", (document_id, file_id))
    return f"scheda archiviata, documento {document_id}"


def archive(db: Database, check: bool = False, *, doc_id: str | None = None) -> None:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        if exc.name != "psycopg":
            raise
        raise RuntimeError('Installa il driver con python -m pip install -e ".[postgres]"') from exc

    dbname = os.getenv("PGDATABASE", "adrc")
    user = os.getenv("PGUSER", "postgres")
    host = os.getenv("PGHOST", "localhost")
    try:
        try:
            connection = psycopg.connect(dbname=dbname, user=user, host=host, autocommit=True)
        except psycopg.OperationalError as exc:
            if not exc.pgconn or not exc.pgconn.needs_password:
                raise
            connection = psycopg.connect(
                dbname=dbname, user=user, host=host,
                password=getpass("Password PostgreSQL: "), autocommit=True,
            )
        with connection as conn:
            conn.execute("SET search_path TO archivio_comunale, pg_catalog")
            expected = {"ordinanze_luoghi", "delibere_firmatari", "documenti", "file_originali", "email", "email_allegati", *[t for _, t in TYPES.values()]}
            actual = conn.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='archivio_comunale'"
            ).fetchall()
            missing = expected.difference(row[0] for row in actual)
            if missing:
                raise ValueError("Tabelle mancanti: " + ", ".join(sorted(missing)))
            conn.execute("SELECT 'archivio_comunale.acquisisci_file(bytea,text,text)'::regprocedure")
            columns = conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema='archivio_comunale' AND table_name IN ('documenti','delibere','determinazioni','ordinanze','ordinanze_luoghi','delibere_firmatari')"
            ).fetchall()
            if not {("ordinanze_luoghi", "luogo_interessato"), ("delibere_firmatari", "nome"),
                    ("delibere_firmatari", "ruolo"), *(("ordinanze", key) for key in (
                        "numero_ordinanza", "organo_emittente", "valida_dal", "valida_al",
                        "prescrizioni", "firmatario", "responsabile_procedimento")), ("delibere", "numero_delibera"),
                    ("delibere", "classifica"), ("determinazioni", "numero_determinazione"),
                    ("determinazioni", "ufficio_responsabile"), ("determinazioni", "firmatario")}.issubset(columns):
                raise ValueError("Schema non aggiornato alle migrazioni di delibere, determinazioni e ordinanze")
            print(f"PostgreSQL: {dbname}.archivio_comunale")
            if check:
                return
            docs = db.list_documents() if doc_id is None else [db.get_document(doc_id)]
            for doc in docs:
                if doc is None:
                    raise KeyError(doc_id)
                with conn.transaction():
                    file_id, present = store_original(conn, doc)
                    prediction = db.latest_prediction(doc["doc_id"])
                    review = db.latest_review(doc["doc_id"])
                    if review and (not prediction or review["prediction_id"] != prediction["id"]):
                        review = None
                    status = "originale presente" if present else "originale acquisito"
                    if review and review["action"] == "discard":
                        status += ", proposta scartata"
                    elif prediction and (review or prediction["status"] == "auto_accepted"):
                        status += ", " + publish(conn, file_id, prediction, review)
                    elif prediction:
                        status += ", in revisione"
                print(f"{doc['doc_id']} -> file {file_id}: {status}")
    except (psycopg.Error, OSError, ValueError) as exc:
        raise RuntimeError(f"Archivio PostgreSQL: {exc}") from exc
