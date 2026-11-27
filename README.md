# CSD_Automatic_document_Reading_and_Classification  
Complex Systems Design's project exam.  
Università di Camerino, A.Y 2026/2027  
_Developed by: Pietro La Riccia, Alessandro Scagnetti, Seya Andronache_

## Provare un PDF con Docker Compose

Per classificare con Gemini, impostare queste due righe nel file locale `.env`
(si può creare da `.env.example`) e inserire la propria chiave dopo il segno uguale.

```dotenv
ALBO_LLM_PROVIDER=gemini
GEMINI_API_KEY=
```

Gemini riceve il testo preprocessato dell'atto e gli esempi configurati nel prompt.
Il PDF resta locale. Il modello è configurabile con `GEMINI_MODEL`.
Per tornare a Ollama basta impostare `ALBO_LLM_PROVIDER=ollama` e ripetere il comando.

Con Docker Desktop avviato, dalla radice del repository:

```powershell
docker compose run --rm --build pipeline process-file /app/data/prova_pdf/DOC_0001.pdf --municipality "Comune di Pesaro"
```

Il PDF deve trovarsi in `data/prova_pdf`. Compose avvia PostgreSQL 18 e, solo
con il provider Ollama, prepara il modello locale. Monta la cartella `data` in
`/app/data`. Il database PostgreSQL del container si chiama `adrc` e risiede
nel volume `postgres-data`, separato da eventuali installazioni Windows.
Lo schema viene installato al primo avvio su volume vuoto.
Per verificare gli atti archiviati:

```powershell
docker compose exec postgres psql -U postgres -d adrc -c "SELECT d.id, d.categoria, coalesce(b.numero_delibera,t.numero_determinazione,o.numero_ordinanza) AS numero_atto, d.data_atto FROM archivio_comunale.documenti d LEFT JOIN archivio_comunale.delibere b ON b.documento_id=d.id LEFT JOIN archivio_comunale.determinazioni t ON t.documento_id=d.id LEFT JOIN archivio_comunale.ordinanze o ON o.documento_id=d.id ORDER BY d.id DESC LIMIT 5;"
```

## Creare il database ADRC

Dalla radice del repository, con PostgreSQL avviato (versione verificata: 18)
e `psql` nel PATH, eseguire:

```sh
psql -X -h localhost -U postgres -d postgres -f database/crea_adrc.psql
```

Su Windows, se PowerShell non trova `psql`, usare il percorso di
`bin\psql.exe` della propria installazione. Il ruolo deve poter creare database.
Lo script crea il database `adrc`, applica `database/schema.sql` e verifica
le sette tabelle di `archivio_comunale` e la funzione `acquisisci_file`.
Si può rilanciare su un archivio già inizializzato per verificarlo conservando i dati.
Le modifiche future a uno schema già installato richiederanno una migrazione.

## Collegare l'app Python

La pipeline e la UI continuano a usare SQLite. Il comando esplicito
`archive-postgres` copia i PDF acquisiti in `adrc.archivio_comunale`
e pubblica le schede approvate per le tre categorie supportate. I dati
specifici non ancora estratti dal classificatore restano nulli. Il testo
normalizzato e le annotazioni di valutazione rimangono nella pipeline SQLite.

Con Python 3.11 o successivo, su Windows dalla radice del repository:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[postgres]"
.\.venv\Scripts\python.exe -m albo archive-postgres --check
```

Su Linux e macOS usare `python3 -m venv .venv` e `.venv/bin/python`
come interprete nei comandi successivi. Il controllo stampa
`PostgreSQL: adrc.archivio_comunale`.

Per usare Gemini da Python, impostare `$env:ALBO_LLM_PROVIDER = "gemini"` e
`$env:GEMINI_API_KEY` nella sessione PowerShell. Il file `.env` è letto da Docker
Compose. Per usare Ollama in locale, avviarlo e impostare il provider a `ollama`.
Il terminale mostra il testo estratto in numero
di pagine e caratteri, la categoria proposta, i campi e l'esito in PostgreSQL.

```powershell
.\.venv\Scripts\python.exe -m albo process-file "D:\esempi\atto.pdf" --municipality "Comune di Pesaro"
```

Per archiviare i PDF di una cartella locale su Windows:

```powershell
.\.venv\Scripts\python.exe -m albo ingest-local --dir 'D:\cartella-dei-pdf' --municipality 'Comune di Pesaro'
.\.venv\Scripts\python.exe -m albo archive-postgres
```

La connessione usa localhost, porta 5432, utente postgres e database
adrc. Per cambiare i parametri usare PGHOST, PGPORT, PGUSER
e PGDATABASE. La password viene richiesta nel terminale.

Dopo preprocess, classify ed eventuale revisione in Streamlit, ripetere
`archive-postgres`. Il comando riusa gli originali a parità di SHA-256,
segnala le schede approvate successivamente modificate e richiede una
riconciliazione manuale per sostituire una scheda già pubblicata.

Per aggiornare un archivio esistente, eseguire nel database `adrc` lo script `database/migra_numeri_delibere.sql`. Trasferisce i numeri storici delle delibere nella tabella di dettaglio. Numero documento, classifica, organo ed esito vengono completati quando disponibili in una nuova estrazione approvata. Con Docker lo script di inizializzazione viene eseguito solo su volumi vuoti.
