"""Streamlit app: human review queue (Gantt task 35) and ground-truth annotation (tasks 20-22)."""
from __future__ import annotations

import html
import json

import pymupdf
import pandas as pd
import streamlit as st

from albo.config import get_settings
from albo.db import Database
from albo.evaluation import evaluate
from albo.review_queue import apply_review, final_records
from albo.taxonomy import FIELDS, DocType

st.set_page_config(page_title="Albo – validazione", page_icon="📄", layout="wide")

LABELS = {"municipality": "Comune", "number": "Numero", "date": "Data dell'atto", "subject": "Oggetto"}
TYPES = [t.value for t in DocType]


@st.cache_resource
def get_db() -> Database:
    return Database(get_settings().db_path)


# ---------------------------------------------------------------- helpers
def show_text(text: str, snippets: list[str | None] = ()) -> None:
    """Document text with the model's evidence highlighted (exact matches only)."""
    body = html.escape(text)
    for snippet in sorted({s for s in snippets if s and len(s) >= 4}, key=len, reverse=True):
        escaped = html.escape(snippet)
        body = body.replace(escaped, f"<mark>{escaped}</mark>")
    st.html(
        '<div style="white-space:pre-wrap;font-family:monospace;font-size:0.85rem;max-height:70vh;'
        f'overflow:auto;border:1px solid #ccc;padding:0.75rem;border-radius:6px">{body}</div>'
    )


def show_pdf(path: str, key: str) -> None:
    with pymupdf.open(path) as pdf:
        page_no = st.number_input("Pagina", 1, len(pdf), 1, key=f"page_{key}") if len(pdf) > 1 else 1
        st.image(pdf[page_no - 1].get_pixmap(dpi=110).tobytes("png"))


def document_view(db: Database, doc_id: str, snippets: list[str | None] = ()) -> None:
    doc = db.get_document(doc_id)
    st.caption(
        f"{doc['municipality']} · tipo dichiarato dall'albo: {doc['declared_type'] or '—'} · "
        f"pubblicato: {doc['published_at'] or '—'}"
        + (f" · [fonte]({doc['source_url']})" if doc["source_url"] else "")
    )
    show_text(db.current_text(doc_id)["text"], snippets)
    with st.expander("Mostra il PDF originale"):
        show_pdf(doc["raw_path"], doc_id)


# ---------------------------------------------------------------- review
def page_review(db: Database, reviewer: str) -> None:
    queue = db.review_queue()
    if not queue:
        st.success("Nessun documento in coda di revisione.")
        return
    municipalities = sorted({r["municipality"] for r in queue})
    chosen = st.sidebar.multiselect("Filtra per comune", municipalities, default=municipalities)
    queue = [r for r in queue if r["municipality"] in chosen]
    if not queue:
        st.info("Nessun documento per i filtri selezionati.")
        return
    labels = {
        r["doc_id"]: f"{r['doc_id']} · {r['municipality']} · {json.loads(r['record_json'])['doc_type']}"
        for r in queue
    }
    st.caption(f"{len(queue)} documenti in coda")
    doc_id = st.selectbox("Documento", list(labels), format_func=labels.get)
    row = next(r for r in queue if r["doc_id"] == doc_id)
    record, issues = json.loads(row["record_json"]), json.loads(row["issues_json"])

    left, right = st.columns([3, 2])
    with left:
        evidence = [record.get("type_evidence")] + [record[f]["evidence"] for f in FIELDS]
        document_view(db, doc_id, evidence)
    with right:
        st.subheader("Verifica")
        for issue in issues:
            st.warning(f"**{issue['field']}** — {issue['message']}", icon="⚠️")
        with st.form(f"review_{doc_id}"):
            doc_type = st.selectbox("Tipo di atto", TYPES, index=TYPES.index(record["doc_type"]), key=f"{doc_id}_type")
            st.caption(f"confidenza {record['type_confidence']:.2f}"
                       + (f" · evidenza: «{record['type_evidence']}»" if record.get("type_evidence") else ""))
            values: dict[str, str] = {}
            for name in FIELDS:
                field = record[name]
                label = ("⚠️ " if name in record["fields_to_review"] else "") + LABELS[name]
                widget = st.text_area if name == "subject" else st.text_input
                values[name] = widget(label, field["value"] or "", key=f"{doc_id}_{name}")
                st.caption(f"confidenza {field['confidence']:.2f}"
                           + (f" · evidenza: «{field['evidence']}»" if field["evidence"] else ""))
            save, discard = st.columns(2)
            do_save = save.form_submit_button("💾 Salva e passa al prossimo", type="primary")
            do_discard = discard.form_submit_button("🗑️ Scarta (non è un atto valido)")
        if do_save or do_discard:
            if not reviewer:
                st.error("Inserisci il tuo nome nella barra laterale.")
                return
            if do_discard:
                action = "discard"
            else:
                changed = doc_type != record["doc_type"] or any(
                    (values[f].strip() or None) != record[f]["value"] for f in FIELDS
                )
                action = "correct" if changed else "approve"
            apply_review(db, doc_id=doc_id, prediction_id=row["id"], reviewer=reviewer, action=action,
                         doc_type=doc_type, values=values)
            st.rerun()


# ---------------------------------------------------------------- annotation
def page_annotate(db: Database, annotator: str) -> None:
    st.info("Annota guardando solo il documento: la predizione del modello non viene mostrata, "
            "per non influenzare la ground truth.")
    only_todo = st.sidebar.checkbox("Solo documenti da annotare", value=True)
    docs = db.documents_to_annotate() if only_todo else [d for d in db.list_documents() if db.current_text(d["doc_id"])]
    if not docs:
        st.success("Nessun documento da annotare.")
        return
    done = {g["doc_id"] for g in db.all_ground_truth()}
    labels = {d["doc_id"]: f"{'✓ ' if d['doc_id'] in done else ''}{d['doc_id']} · {d['municipality']}" for d in docs}
    st.caption(f"{len(done)} annotati · {len(docs)} nella lista")
    doc_id = st.selectbox("Documento", list(labels), format_func=labels.get)
    existing = db.get_ground_truth(doc_id)
    evidence_old = json.loads(existing["evidence_json"]) if existing and existing["evidence_json"] else {}

    left, right = st.columns([3, 2])
    with left:
        document_view(db, doc_id)
    with right:
        st.subheader("Annotazione")
        with st.form(f"annotate_{doc_id}"):
            current_type = existing["doc_type"] if existing else DocType.OTHER.value
            doc_type = st.selectbox("Tipo di atto", TYPES[:-1], index=TYPES[:-1].index(current_type), key=f"a_{doc_id}_type")
            st.caption("UNCERTAIN non è ammesso nella ground truth: se il tipo è davvero dubbio, discutine con il gruppo.")
            values, evidence = {}, {}
            for name in FIELDS:
                widget = st.text_area if name == "subject" else st.text_input
                values[name] = widget(LABELS[name], (existing[name] if existing else "") or "", key=f"a_{doc_id}_{name}")
                evidence[name] = st.text_input(f"Evidenza · {LABELS[name].lower()}", evidence_old.get(name, ""),
                                               key=f"a_{doc_id}_{name}_ev")
            submitted = st.form_submit_button("💾 Salva annotazione", type="primary")
        if submitted:
            if not annotator:
                st.error("Inserisci il tuo nome nella barra laterale.")
                return
            if DocType(doc_type) in (DocType.DELIBERATION, DocType.DETERMINATION, DocType.ORDINANCE):
                missing = [LABELS[f] for f in FIELDS if not values[f].strip()]
                if missing:
                    st.warning("Campi vuoti: " + ", ".join(missing) + " (salvati come assenti).")
            db.upsert_ground_truth(
                doc_id=doc_id, doc_type=doc_type, annotator=annotator,
                evidence={k: v.strip() for k, v in evidence.items() if v.strip()},
                **{f: values[f].strip() or None for f in FIELDS},
            )
            st.rerun()


# ---------------------------------------------------------------- metrics
def page_metrics(db: Database) -> None:
    st.subheader("Stato della pipeline")
    stats = db.stats()
    cols = st.columns(len(stats))
    for col, (name, value) in zip(cols, stats.items()):
        col.metric(name.replace("_", " "), value)

    st.subheader("Valutazione sulla ground truth")
    municipalities = db.municipalities()
    c1, c2 = st.columns(2)
    only = c1.selectbox("Solo questo comune (test held-out)", [None] + municipalities, format_func=lambda x: x or "—")
    exclude = c2.selectbox("Escludi questo comune (sviluppo)", [None] + municipalities, format_func=lambda x: x or "—")
    report = evaluate(db, only=only, exclude=exclude)
    overall = report["overall"]
    if not overall.get("n"):
        st.info("Servono documenti con ground truth e predizione per calcolare le metriche.")
    else:
        fmt = lambda x: "n/d" if x is None else f"{x:.1%}"
        m = st.columns(4)
        m[0].metric("Accuratezza tipo", fmt(overall["classification_accuracy"]))
        m[1].metric("Precisione campi", fmt(overall["field_precision_micro"]))
        m[2].metric("Straight-through rate", fmt(overall["straight_through_rate"]))
        m[3].metric("Precisione degli auto-accettati", fmt(overall["straight_through_precision"]))
        st.dataframe(pd.DataFrame(overall["fields"]).T, use_container_width=True)
        st.caption("Matrice di confusione (righe = ground truth, colonne = predizione)")
        st.dataframe(pd.DataFrame(report["confusion_matrix"]).T, use_container_width=True)
        for title, key in (("Per comune", "by_municipality"), ("Per tipo di documento", "by_document_type")):
            st.caption(title)
            frame = pd.DataFrame(
                {k: {c: v[c] for c in ("n", "classification_accuracy", "field_precision_micro", "straight_through_rate")}
                 for k, v in report[key].items()}
            ).T
            st.dataframe(frame, use_container_width=True)

    st.subheader("Esporta")
    records = final_records(db)
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    st.download_button(f"Record finali ({len(records)}) · JSON Lines", payload, "final_records.jsonl", "application/jsonl")


# ---------------------------------------------------------------- main
def main() -> None:
    db = get_db()
    st.sidebar.title("📄 Albo pretorio")
    person = st.sidebar.text_input("Il tuo nome", key="person")
    mode = st.sidebar.radio("Modalità", ["Coda di revisione", "Annotazione ground truth", "Metriche"])
    if mode == "Coda di revisione":
        st.title("Coda di revisione")
        page_review(db, person.strip())
    elif mode == "Annotazione ground truth":
        st.title("Annotazione ground truth")
        page_annotate(db, person.strip())
    else:
        st.title("Metriche")
        page_metrics(db)


main()
