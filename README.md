# News4u

Ogni mattina GitHub legge i feed RSS dei settori, Gemma 4 31B (gratis tramite Google AI Studio) riassume le notizie nuove in italiano e la pagina si aggiorna da sola.

## Configurazione (circa 15 minuti, una volta sola)

### 1. Crea il repository
1. Accedi a github.com (o crea un account gratuito).
2. In alto a destra: **+ → New repository**.
3. Nome: `rassegna`, visibilità **Public**, poi **Create repository**.

### 2. Carica i file
1. Nel repository vuoto clicca **uploading an existing file**.
2. Trascina tutti i file e le cartelle estratti dallo zip: `build.py`, `feeds.json`, `template.html`, `requirements.txt`, `README.md`, `data/`, `docs/`.
3. **Commit changes**.

La cartella `.github` è nascosta su Mac e Windows e spesso non viene caricata trascinando. Creala a mano:
1. **Add file → Create new file**.
2. Come nome scrivi esattamente `.github/workflows/rassegna.yml`.
3. Incolla il contenuto del file `rassegna.yml` che trovi nello zip, poi **Commit changes**.

### 3. Chiave API gratuita di Google AI Studio
1. Vai su aistudio.google.com e accedi con un account Google.
2. **Get API key → Create API key** (se lo chiede, crea un progetto nuovo). Non collegare la fatturazione: così la chiave resta sul piano gratuito e non paghi nulla.
3. Copia la chiave.
4. Nel repository: **Settings → Secrets and variables → Actions → New repository secret**.
5. Nome `GEMINI_API_KEY`, valore la chiave, **Add secret**.

Lo script fa una sola chiamata per settore al giorno (8 in totale), ben dentro i limiti gratuiti. Nota: sul piano gratuito Google può usare i testi inviati per migliorare i propri servizi; qui sono solo titoli ed estratti di articoli pubblici.

Senza chiave il sito funziona comunque, ma usa gli estratti dei feed al posto dei riassunti.

### 3b. (Facoltativo) Post in tendenza da X
La rubrica «In tendenza» usa già, gratis, i post più votati del giorno di r/LocalLLaMA, r/MachineLearning, r/OpenAI e r/singularity (lista in `feeds.json`, voce `reddit_subs`) e le discussioni più votate di Hacker News. Per aggiungere i post di X serve un account sviluppatore su developer.x.com con credito prepagato: l'API di X si paga a consumo, circa 0,005 $ per post letto. Con le impostazioni di base (25 post al giorno) sono circa 4 $ al mese.
1. Su developer.x.com crea un progetto e un'app, poi copia il **Bearer Token**.
2. Nel repository aggiungi il secret `X_BEARER_TOKEN`.
3. In `feeds.json`, sezione `tendenze`, puoi cambiare la ricerca (`x_query`), quanti post leggere (`x_max`) e il minimo di like (`x_min_likes`).

Senza token lo script salta X e usa Reddit e Hacker News.

Nota su Reddit: lo script usa i feed pubblici senza chiave. Reddit a volte blocca le richieste dai server cloud come quelli di GitHub: nel log compare `r/... non disponibile`. In quel caso la rubrica continua con le altre fonti; lanciato dal Mac funziona normalmente.

### 4. Attiva il sito
**Settings → Pages**: Source **Deploy from a branch**, Branch **main**, cartella **/docs**, **Save**.

### 5. Primo aggiornamento
**Actions → Rassegna giornaliera → Run workflow**. Dopo un paio di minuti il sito è su
`https://TUO-UTENTE.github.io/rassegna/`

Da quel momento si aggiorna ogni giorno alle 7:00 (6:00 con l'ora solare).

## Personalizzare
- **Fonti e settori**: `feeds.json`. Ogni settore ha un elenco di feed e una descrizione (`topic`) che il modello usa per scartare le notizie fuori tema. Per un argomento senza feed usa Google News: `https://news.google.com/rss/search?q=PAROLE+when:2d&hl=it&gl=IT&ceid=IT:it`
- **Quante notizie**: `max_per_settore` (nuove per giorno) e `giorni_in_pagina` (storico visibile).
- **Orario**: la riga `cron` in `.github/workflows/rassegna.yml` (è in UTC).
- **Modello**: di default `gemma-4-31b-it`. Per cambiarlo aggiungi nel workflow la variabile `RASSEGNA_MODEL`, per esempio `gemma-4-26b-a4b-it` (più veloce) o un modello Gemini.

## Se qualcosa non va
- **Il workflow fallisce su "git push"**: Settings → Actions → General → Workflow permissions → **Read and write permissions**.
- **Nel log compare "modello non disponibile"**: controlla che il secret si chiami esattamente `GEMINI_API_KEY`; il messaggio riporta l'errore di Google. Quel giorno la pagina usa gli estratti dei feed.
- **Un feed non dà risultati**: nel log del workflow compare una riga con `!`. Sostituiscilo in `feeds.json`.
- **GitHub sospende i workflow pianificati dopo 60 giorni senza attività sul repository**: se succede, riattivalo dalla scheda Actions.
