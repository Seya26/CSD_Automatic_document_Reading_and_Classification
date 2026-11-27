-- Installare nel database adrc con psql -v ON_ERROR_STOP=1.
BEGIN;
CREATE SCHEMA archivio_comunale;
SET LOCAL search_path = archivio_comunale, pg_catalog;

CREATE TABLE email (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    casella text NOT NULL,
    chiave_messaggio text NOT NULL,
    ricevuta_il timestamptz NOT NULL,
    mittente text,
    oggetto text,
    UNIQUE (casella, chiave_messaggio)
);

CREATE TABLE documenti (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    categoria text NOT NULL CHECK (categoria IN ('ordinanza','determinazione','delibera')),
    ente text,
    data_atto date,
    oggetto text,
    creato_il timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, categoria)
);

CREATE TABLE file_originali (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contenuto bytea NOT NULL,
    sha256 bytea GENERATED ALWAYS AS (sha256(contenuto)) STORED UNIQUE,
    nome_originale text NOT NULL,
    mime_type text NOT NULL,
    acquisito_il timestamptz NOT NULL DEFAULT now(),
    -- NULL fino all'identificazione dell'atto, anche in caso di OCR incerto.
    documento_id bigint REFERENCES documenti
);
CREATE INDEX file_per_documento ON file_originali(documento_id);

CREATE TABLE email_allegati (
    email_id bigint NOT NULL REFERENCES email,
    posizione integer NOT NULL CHECK (posizione > 0),
    file_id bigint NOT NULL REFERENCES file_originali,
    nome_ricevuto text NOT NULL,
    PRIMARY KEY (email_id, posizione)
);
CREATE INDEX email_per_file ON email_allegati(file_id);

CREATE TABLE ordinanze (
    documento_id bigint PRIMARY KEY,
    categoria text GENERATED ALWAYS AS ('ordinanza'::text) STORED,
    organo_emittente text,
    numero_ordinanza text,
    prescrizioni text,
    firmatario text,
    responsabile_procedimento text,
    valida_dal date,
    valida_al date,
    CHECK (valida_al >= valida_dal),
    FOREIGN KEY (documento_id, categoria) REFERENCES documenti(id, categoria)
);
CREATE TABLE determinazioni (
    documento_id bigint PRIMARY KEY,
    categoria text GENERATED ALWAYS AS ('determinazione'::text) STORED,
    ufficio_responsabile text,
    numero_determinazione text,
    firmatario text,
    FOREIGN KEY (documento_id, categoria) REFERENCES documenti(id, categoria)
);
CREATE TABLE delibere (
    documento_id bigint PRIMARY KEY,
    categoria text GENERATED ALWAYS AS ('delibera'::text) STORED,
    numero_delibera text,
    classifica text,
    organo_deliberante text,
    esito_votazione text,
    FOREIGN KEY (documento_id, categoria) REFERENCES documenti(id, categoria)
);

CREATE TABLE ordinanze_luoghi (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento_id bigint NOT NULL REFERENCES ordinanze(documento_id) ON DELETE CASCADE,
    luogo_interessato text NOT NULL,
    UNIQUE (documento_id, luogo_interessato)
);

CREATE TABLE delibere_firmatari (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento_id bigint NOT NULL REFERENCES delibere(documento_id) ON DELETE CASCADE,
    nome text NOT NULL,
    ruolo text,
    UNIQUE NULLS NOT DISTINCT (documento_id, nome, ruolo)
);

-- Eseguire insieme all'inserimento del collegamento email nella stessa transazione.
CREATE FUNCTION acquisisci_file(p_contenuto bytea, p_nome text, p_mime text)
RETURNS TABLE(file_id bigint, gia_presente boolean)
LANGUAGE plpgsql SET search_path = archivio_comunale, pg_catalog AS $$
BEGIN
    INSERT INTO file_originali(contenuto, nome_originale, mime_type)
    VALUES (p_contenuto, p_nome, p_mime)
    ON CONFLICT (sha256) DO NOTHING RETURNING id INTO file_id;
    gia_presente := file_id IS NULL;
    IF gia_presente THEN
        SELECT id INTO STRICT file_id FROM file_originali
        WHERE sha256 = pg_catalog.sha256(p_contenuto) FOR KEY SHARE;
    END IF;
    RETURN NEXT;
END
$$;
COMMIT;
