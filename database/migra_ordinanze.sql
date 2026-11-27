BEGIN;
SET LOCAL search_path = archivio_comunale, pg_catalog;
LOCK TABLE documenti, ordinanze, delibere IN ACCESS EXCLUSIVE MODE;
ALTER TABLE ordinanze
    ADD COLUMN IF NOT EXISTS numero_ordinanza text,
    ADD COLUMN IF NOT EXISTS numero_proposta text,
    ADD COLUMN IF NOT EXISTS prescrizioni text,
    ADD COLUMN IF NOT EXISTS firmatario text,
    ADD COLUMN IF NOT EXISTS responsabile_procedimento text;

CREATE TABLE IF NOT EXISTS ordinanze_luoghi (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento_id bigint NOT NULL REFERENCES ordinanze(documento_id) ON DELETE CASCADE,
    luogo_interessato text NOT NULL,
    UNIQUE (documento_id, luogo_interessato)
);

CREATE TABLE IF NOT EXISTS delibere_firmatari (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    documento_id bigint NOT NULL REFERENCES delibere(documento_id) ON DELETE CASCADE,
    nome text NOT NULL,
    ruolo text,
    UNIQUE NULLS NOT DISTINCT (documento_id, nome, ruolo)
);

INSERT INTO ordinanze(documento_id)
SELECT id FROM documenti WHERE categoria='ordinanza'
ON CONFLICT (documento_id) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.columns WHERE table_schema='archivio_comunale'
               AND table_name='ordinanze' AND column_name='luogo_interessato') THEN
        INSERT INTO ordinanze_luoghi(documento_id, luogo_interessato)
        SELECT documento_id, luogo_interessato FROM ordinanze WHERE luogo_interessato IS NOT NULL
        ON CONFLICT DO NOTHING;
        ALTER TABLE ordinanze DROP COLUMN luogo_interessato;
    END IF;
    IF EXISTS (SELECT FROM information_schema.columns WHERE table_schema='archivio_comunale'
               AND table_name='documenti' AND column_name='numero_atto') THEN
        IF EXISTS (SELECT FROM documenti d JOIN ordinanze o ON o.documento_id=d.id
                   WHERE d.numero_atto <> o.numero_ordinanza) THEN
            RAISE EXCEPTION 'Numeri ordinanza discordanti: verificare prima della migrazione';
        END IF;
        UPDATE ordinanze o SET numero_ordinanza=d.numero_atto FROM documenti d
        WHERE o.documento_id=d.id AND o.numero_ordinanza IS NULL AND d.numero_atto IS NOT NULL;
        UPDATE documenti SET numero_atto=NULL WHERE categoria='ordinanza' AND numero_atto IS NOT NULL;
    END IF;
END $$;
COMMIT;
