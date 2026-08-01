# 🎵 Bot Discord — Quiz Musicale

Bot che si collega a un canale vocale, riproduce brani presi da una playlist
Spotify (con fallback automatico su Deezer per l'audio) e assegna punti a chi
indovina titolo e artista in chat.

**Punteggio:** primo a indovinare il **titolo** → 2 punti · primo a indovinare
l'**artista** → 1 punto. Il matching è permissivo: ignora maiuscole/minuscole,
accenti, "(feat. ...)", "[Remix]", piccoli refusi, ecc.

---

## 1. Requisiti

- Python 3.10 o superiore
- **FFmpeg** installato e disponibile nel PATH di sistema (necessario per
  riprodurre audio in un canale vocale Discord):
  - Windows: scarica da https://ffmpeg.org/download.html e aggiungi alla PATH
  - macOS: `brew install ffmpeg`
  - Linux (Debian/Ubuntu): `sudo apt install ffmpeg`

## 2. Crea il bot Discord

1. Vai su https://discord.com/developers/applications → **New Application**
2. Sezione **Bot** → **Reset Token** e copia il token (ti servirà dopo)
3. Nella stessa sezione **Bot**, attiva **MESSAGE CONTENT INTENT** (obbligatorio,
   altrimenti il bot non può leggere le risposte in chat)
4. Sezione **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Permessi: `Send Messages`, `Connect`, `Speak`, `Read Message History`
   - Apri l'URL generato e invita il bot nel tuo server

## 3. Crea le credenziali Spotify

1. Vai su https://developer.spotify.com/dashboard → **Create app**
2. Ti servono solo **Client ID** e **Client Secret** (non serve configurare
   redirect URI per il funzionamento del bot, il campo va comunque compilato
   con un valore qualsiasi es. `http://localhost:8888/callback`)

## 4. Installazione

```bash
cd discord-quiz-bot
pip install -r requirements.txt
cp .env.example .env
```

Apri `.env` e inserisci:
- `DISCORD_TOKEN` (dal punto 2)
- `SPOTIFY_CLIENT_ID` e `SPOTIFY_CLIENT_SECRET` (dal punto 3)

## 5. Avvio

```bash
python bot.py
```

## 6. Utilizzo in Discord

Devi essere già connesso a un canale vocale prima di digitare `!quiz start`: il
bot ti raggiunge lì automaticamente.

```
!quiz start
```

Il bot ti farà 4 domande in chat, una alla volta (hai 60 secondi per rispondere
a ciascuna):

1. **Quante canzoni** vuoi riprodurre → scrivi un numero, es. `10`
2. **Playlist specifica o casuale?** → incolla il link di una playlist **Spotify o Deezer**
   (il bot riconosce automaticamente da quale servizio proviene), oppure scrivi
   `casuale` per pescare brani dalla classifica globale Deezer (nessun account
   richiesto, sempre disponibile)
3. **Modalità di gioco** → scrivi `aperta` (rispondi scrivendo liberamente
   titolo/artista) oppure `scelta multipla` (rispondi con un numero da 4
   opzioni mostrate in chat)
4. **Durata di ogni round** in secondi → scrivi un numero, oppure `default`
   per usare il valore in `.env` (30s)

**Punteggi:**
- Modalità **aperta**: primo a scrivere il titolo giusto → 2 punti, primo a
  scrivere l'artista giusto → 1 punto (indipendenti tra loro)
- Modalità **scelta multipla**: 4 opzioni numerate, primo a scrivere il numero
  giusto → 3 punti (titolo+artista insieme)

Altri comandi:

```
!quiz classifica   # mostra la classifica generale in qualsiasi momento
!quiz stop          # ferma il quiz in anticipo
!quiz reset         # azzera la classifica del server
```

Dopo ogni round, il bot rivela il brano e mostra automaticamente la
**classifica generale aggiornata** in chat.

---

## Note tecniche e limiti

- **⚠️ Spotify richiede ora un account Premium per l'app**: Spotify ha
  introdotto un requisito per cui l'account proprietario dell'app developer
  deve avere un abbonamento Premium attivo, altrimenti le richieste API
  falliscono con errore 403 ("Active premium subscription required").
  Se non hai Premium, **usa link Deezer o la modalità `casuale`** (basata
  sulla classifica Deezer), che funzionano sempre senza bisogno di alcun
  account o autenticazione.
- **Riconoscimento automatico Spotify/Deezer**: quando incolli un link di
  playlist, il bot riconosce da solo se è un link Spotify o Deezer in base
  al dominio nell'URL, e usa l'API corrispondente. Per Deezer il preview
  audio è già incluso nella risposta; per Spotify, se manca il
  `preview_url`, il bot cerca automaticamente lo stesso brano su Deezer
  come fallback.
- Ogni round dura al massimo `ROUND_DURATION_SECONDS` (default 30s, modificabile
  in `.env`), o termina prima se sia titolo che artista vengono indovinati.
- I punteggi sono salvati in `data/scores_<id_server>.json`, uno per server.
- Il bot supporta un solo quiz attivo per volta per ogni server (non per canale).

## Possibili estensioni future
- Comando `!quiz skip` per saltare un brano durante il round
- Playlist multiple / categorie a tema
- Bonus di punti in base alla velocità di risposta
- Classifica generale multi-server / stagionale

---

## 7. Pubblicare su GitHub

```bash
cd discord-quiz-bot
git init
git add .
git commit -m "Bot Discord per quiz musicali"
```

Poi crea una repo vuota su https://github.com/new e collegala:

```bash
git remote add origin https://github.com/TUO_USERNAME/TUO_REPO.git
git branch -M main
git push -u origin main
```

⚠️ Il file `.gitignore` incluso esclude già `.env` (le tue credenziali) e i
punteggi locali in `data/*.json` — **controlla di non aver mai committato
`.env`** prima del push. Se lo hai fatto per errore, rigenera subito il token
Discord e le credenziali Spotify (una volta pubblicate su GitHub vanno
considerate compromesse).

## 8. Deploy su Render (piano gratuito) + cron job "keep-alive"

Il bot include un `Dockerfile` (garantisce `ffmpeg`, necessario per l'audio)
e un mini server Flask (`keep_alive.py`) usato solo per il piano free di
Render, che altrimenti spegne il servizio dopo ~15 minuti senza richieste HTTP.

### 8.1 Crea il servizio su Render

1. Vai su https://dashboard.render.com → **New** → **Web Service**
2. Collega il repository GitHub appena creato
3. Render rileverà il `Dockerfile` automaticamente (Environment: **Docker**)
4. Piano: **Free**
5. In **Environment Variables** aggiungi:
   - `DISCORD_TOKEN`
   - `SPOTIFY_CLIENT_ID`
   - `SPOTIFY_CLIENT_SECRET`
   - `ROUND_DURATION_SECONDS` (opzionale, default 30)
6. **Create Web Service** — al primo deploy Render builderà l'immagine Docker
   e avvierà `python bot.py`

In alternativa, se preferisci il deploy automatico via Blueprint, nella repo
è incluso anche `render.yaml`: su Render scegli **New → Blueprint** e punta
al repository, poi inserisci i valori delle env var quando richiesto.

### 8.2 Recupera l'URL del servizio

A deploy completato, Render mostra un URL tipo:
`https://discord-quiz-bot-xxxx.onrender.com`

Aprendolo nel browser dovresti vedere: *"Il bot per il quiz musicale è vivo! 🎵"*

### 8.3 Configura il cron job per tenerlo sveglio

Il piano free si "addormenta" dopo ~15 minuti di inattività HTTP: un cron
job che pinga l'URL ogni 10 minuti evita lo spegnimento.

Usa un servizio gratuito come **cron-job.org**:

1. Registrati su https://cron-job.org
2. **Create cronjob**
3. URL: l'indirizzo Render del punto 8.2 (es. `https://discord-quiz-bot-xxxx.onrender.com`)
4. Intervallo di esecuzione: **ogni 10 minuti**
5. Salva

In alternativa puoi usare **UptimeRobot** (monitoraggio HTTP ogni 5 minuti)
con lo stesso effetto.

### Note importanti

- Il piano free di Render ha **750 ore/mese** di utilizzo gratuito: se il
  cron job lo tiene sveglio 24/7, un mese da ~730 ore rientra nel limite,
  ma è bene monitorarlo dalla dashboard Render.
- Il filesystem di Render nel piano free è **effimero**: ad ogni redeploy o
  riavvio del servizio, i punteggi salvati in `data/scores_*.json` vengono
  persi. Per punteggi persistenti a lungo termine valuta un **Render Disk**
  (a pagamento) o un piccolo database esterno (es. Postgres gratuito su
  Render/Neon/Supabase) al posto del salvataggio su file.
- I comandi Discord che leggono la chat richiedono **Message Content
  Intent** attivo (vedi punto 2) — senza, il bot non riceve il testo dei
  messaggi e non può assegnare punti.
