BEGIN;
SET LOCAL search_path = archivio_comunale, pg_catalog;
LOCK TABLE documenti, delibere IN ACCESS EXCLUSIVE MODE;
ALTER TABLE documenti ADD COLUMN IF NOT EXISTS numero_documento text;
ALTER TABLE delibere ADD COLUMN IF NOT EXISTS numero_delibera text,
                     ADD COLUMN IF NOT EXISTS classifica text;
INSERT INTO delibere(documento_id)
SELECT id FROM documenti WHERE categoria = 'delibera'
ON CONFLICT (documento_id) DO NOTHING;
DO $$
BEGIN
    IF EXISTS (SELECT FROM documenti d JOIN delibere b ON b.documento_id=d.id
               WHERE d.numero_atto IS NOT NULL AND b.numero_delibera IS NOT NULL
               AND d.numero_atto <> b.numero_delibera) THEN
        RAISE EXCEPTION 'Numeri delibera discordanti: verificare i dati prima della migrazione';
    END IF;
END $$;
UPDATE delibere b SET numero_delibera=d.numero_atto
FROM documenti d WHERE d.id=b.documento_id AND b.numero_delibera IS NULL AND d.numero_atto IS NOT NULL;
UPDATE documenti SET numero_atto=NULL WHERE categoria='delibera' AND numero_atto IS NOT NULL;
ALTER TABLE documenti DROP CONSTRAINT IF EXISTS numero_atto_solo_altre_categorie;
ALTER TABLE documenti ADD CONSTRAINT numero_atto_solo_altre_categorie
CHECK (categoria <> 'delibera' OR numero_atto IS NULL);
COMMIT;
