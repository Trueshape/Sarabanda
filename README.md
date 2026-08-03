# 🎵 Discord Bot — Music Quiz

A bot that joins a voice channel, plays songs from a Spotify playlist (with
automatic Deezer fallback for audio) or from Deezer/iTunes, and awards points
to whoever guesses the title and artist in chat.

**Scoring:** first to guess the **title** → 2 points · first to guess the
**artist** → 1 point (open answer mode), or **3 points** for the first
correct button click (multiple choice mode). Matching is lenient: it ignores
upper/lowercase, accents, "(feat. ...)", "[Remix]", small typos, etc.

---

## 1. Requirements

- Python 3.10 or newer
- **FFmpeg** installed and available on the system PATH (required to play
  audio in a Discord voice channel):
  - Windows: download from https://ffmpeg.org/download.html and add it to PATH
  - macOS: `brew install ffmpeg`
  - Linux (Debian/Ubuntu): `sudo apt install ffmpeg`

## 2. Create the Discord bot

1. Go to https://discord.com/developers/applications → **New Application**
2. **Bot** section → **Reset Token** and copy the token (you'll need it later)
3. In the same **Bot** section, enable **MESSAGE CONTENT INTENT** (required,
   otherwise the bot can't read answers in chat)
4. **OAuth2 → URL Generator** section:
   - Scopes: `bot` **and** `applications.commands` (the latter is required
     for the `/quiz ...` slash commands to show up in the server)
   - Permissions: `Send Messages`, `Connect`, `Speak`, `Read Message History`
   - Open the generated URL and invite the bot to your server

## 3. Create Spotify credentials

1. Go to https://developer.spotify.com/dashboard → **Create app**
2. You only need the **Client ID** and **Client Secret** (you don't need to
   configure a real redirect URI for the bot to work, but the field must
   still be filled in with some value, e.g. `http://127.0.0.1:8888/callback`)

## 4. Installation

```bash
cd discord-quiz-bot
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in:
- `DISCORD_TOKEN` (from step 2)
- `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` (from step 3)

## 5. Run it

```bash
python bot.py
```

## 6. Using it in Discord

You need to already be connected to a voice channel before running
`/quiz start`: the bot joins you there automatically.

⚠️ **Important for slash commands**: in step 2 (inviting the bot), the
`applications.commands` scope must be selected together with `bot` in the
OAuth2 URL Generator, otherwise slash commands won't appear in the server.
If you already invited the bot with only the `bot` scope, you need to
regenerate the invite link adding `applications.commands` too and invite it
again (no need to remove it first, re-inviting updates the permissions).

```
/quiz start
```

Typing `/quiz start` shows a menu with all the parameters right away:

- **songs** (required): how many songs to play, e.g. `10`
- **mode** (required): pick from the dropdown between "Open answer" or
  "Multiple choice (4 buttons)"
- **source** (optional): paste a **playlist or single-song** link (Spotify,
  Deezer, or iTunes/Apple Music). You can also paste **multiple links
  separated by commas**, even mixing all three sources, to build a custom
  mini-quiz without creating a dedicated playlist (e.g.
  `spotify_link, deezer_link, itunes_link`). Leave empty for random songs
  from the Deezer chart
- **category** (optional, ignored if you use `source` or `artist`): filters
  random songs by music genre (Pop, Rock, Rap/Hip Hop, etc.) or by special
  category: **decades** (70s/80s/90s/2000s/2010s/2020s), **Recent Hits**,
  **Trending Now**, **J-Pop**, **J-Rock**, **K-Pop**, **Anime**. As you type,
  Discord suggests the available options (autocomplete). Leave empty to use
  the global chart
- **artist** (optional, ignored if you use `source`): type a specific
  artist's name for a quiz built from their most popular songs. Tolerates
  typos and imprecise punctuation (e.g. typing "Evanescene" still finds
  "Evanescence") thanks to fuzzy matching
- **duration** (optional): seconds per round, default 30

Other commands:

```
/quiz leaderboard   # show the overall leaderboard at any time
/quiz stop           # stop the quiz early
/quiz reset          # reset the server's leaderboard
/quiz help           # show help
```

**How answering works:**
- **Open answer mode**: type the title/artist as a normal chat message.
- **Multiple choice mode**: click one of the 4 buttons under the round's
  message. Your result is shown **only to you** (ephemeral message), so you
  can never spoil the answer for other players. One attempt per person: if
  you get it wrong, no more points for that round. The round ends early once
  everyone currently in the voice channel has answered; otherwise it waits
  out the full round duration before revealing the answer.

After every round, the bot reveals the song and automatically shows the
**updated overall leaderboard** in chat.

**Note on syncing**: on first startup the bot registers its slash commands
with Discord (automatic, visible in the logs as "Synced N slash command(s)").
Discord sometimes takes a couple of minutes to show updated commands in the
client — if you don't see them right away, restart Discord or wait a bit.

---

## 6.1 Unrelated bonus commands: Clemmy's work schedule

These two commands have nothing to do with the music quiz — they're just
bolted onto the same bot as a small standalone extra.

- `/edit_settimana [lunedi] [martedi] [mercoledi] [giovedi] [venerdi] [sabato] [domenica]`
  — each parameter is an optional true/false. Only the days you set are
  changed; the rest keep their previous value. Replies with the full
  updated weekly schedule.
- `/oggyclemmy` — checks today's day (Europe/Rome timezone) against the
  schedule set above. Replies **"Oggi si lavora e si fattura"** if Clemmy is
  at work today, or **"Sì è a casa, GOGOGO"** otherwise.

The schedule is stored per server in `data/clemmy_<server_id>.json`, same
storage style as the quiz leaderboard. Before `/edit_settimana` is used for
the first time, every day defaults to "not working".

---

## Technical notes and limitations

- **⚠️ Spotify now requires a Premium account for the app**: Spotify
  introduced a requirement where the account that owns the developer app
  must have an active Premium subscription, otherwise API requests fail
  with a 403 error ("Active premium subscription required"). If you don't
  have Premium, **use Deezer links or `random` mode** (based on the Deezer
  chart), which always work with no account or authentication needed.
- **Automatic Spotify/Deezer/iTunes recognition**: when you paste a link,
  the bot recognizes the service on its own based on the domain in the URL,
  and uses the matching API. For Deezer and iTunes the audio preview is
  already included in the response; for Spotify, if `preview_url` is
  missing, the bot automatically looks up the same song on Deezer as a
  fallback.
- **iTunes/Apple Music**: only supported for **single tracks** (a direct
  link to a song), not playlists — there's no unauthenticated public API to
  read a user's Apple Music playlist.
- **Music categories**: the genre list in the `category` autocomplete is
  read live from the Deezer API (`/genre`) and cached for one hour. When you
  pick a category, the bot combines the Deezer chart filtered by that genre
  (primary, reliable source, ~70% of songs) with an iTunes search for the
  same category name (secondary source, for extra variety, ~30%). The
  iTunes genre search is an approximation (it searches the genre name as a
  free-text term, not a true genre filter like Deezer's) because iTunes
  doesn't offer a reliable, unauthenticated genre-filtered public API; the
  full Apple Music API with real genres would require an Apple Developer
  Program membership (paid).
- **Special categories** (decades, Recent Hits, Trending Now, J-Pop, J-Rock,
  K-Pop, Anime): these don't map to a direct Deezer genre, so the bot
  dynamically searches for a relevant public Deezer playlist at request
  time (no fragile hardcoded IDs). If the search finds nothing suitable for
  a very niche category, the bot reports an error instead of returning
  irrelevant random results.
- **Artist search**: uses Deezer's artist search combined with `rapidfuzz`
  to tolerate typos and imprecise punctuation in the typed name, then reads
  that artist's most popular tracks.
- Each round lasts at most `ROUND_DURATION_SECONDS` (default 30s, adjustable
  in `.env`), or ends earlier once the title and artist are both guessed
  (open answer mode) or everyone in voice has answered (multiple choice mode).
- Scores are saved in `data/scores_<server_id>.json`, one per server.
- The bot supports only one active quiz at a time per server (not per channel).

## Possible future extensions
- A `/quiz skip` command to skip a song mid-round
- Multiple playlists / themed categories
- Speed-based bonus points
- Multi-server / seasonal overall leaderboard

---

## 7. Publishing to GitHub

```bash
cd discord-quiz-bot
git init
git add .
git commit -m "Discord music quiz bot"
```

Then create an empty repo at https://github.com/new and link it:

```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

⚠️ The included `.gitignore` already excludes `.env` (your credentials) and
local scores in `data/*.json` — **make sure you never committed `.env`**
before pushing. If you did by mistake, immediately regenerate the Discord
token and Spotify credentials (once published on GitHub they should be
considered compromised).

## 8. Deploy to Render (free plan) + "keep-alive" cron job

The bot includes a `Dockerfile` (guarantees `ffmpeg`, required for audio)
and a small Flask server (`keep_alive.py`) used only for Render's free
plan, which otherwise puts the service to sleep after ~15 minutes without
HTTP requests.

### 8.1 Create the service on Render

1. Go to https://dashboard.render.com → **New** → **Web Service**
2. Connect the GitHub repository you just created
3. Render will detect the `Dockerfile` automatically (Environment: **Docker**)
4. Plan: **Free**
5. Under **Environment Variables**, add:
   - `DISCORD_TOKEN`
   - `SPOTIFY_CLIENT_ID`
   - `SPOTIFY_CLIENT_SECRET`
   - `ROUND_DURATION_SECONDS` (optional, default 30)
6. **Create Web Service** — on the first deploy Render will build the Docker
   image and start `python bot.py`

Alternatively, if you'd rather deploy automatically via Blueprint, the repo
also includes `render.yaml`: on Render choose **New → Blueprint**, point it
at the repository, then fill in the env var values when prompted.

### 8.2 Get the service URL

Once the deploy is done, Render shows a URL like:
`https://discord-quiz-bot-xxxx.onrender.com`

Opening it in a browser should show: *"The music quiz bot is alive! 🎵"*

### 8.3 Set up the keep-alive cron job

The free plan goes to sleep after ~15 minutes of HTTP inactivity: a cron
job that pings the URL every 10 minutes prevents that.

Use a free service like **cron-job.org**:

1. Sign up at https://cron-job.org
2. **Create cronjob**
3. URL: the Render address from step 8.2 (e.g. `https://discord-quiz-bot-xxxx.onrender.com`)
4. Execution interval: **every 10 minutes**
5. Save

Alternatively you can use **UptimeRobot** (HTTP monitoring every 5 minutes)
for the same effect.

### Important notes

- Render's free plan has **750 free hours/month**: if the cron job keeps it
  awake 24/7, a ~730-hour month fits within the limit, but it's worth
  keeping an eye on it from the Render dashboard.
- Render's filesystem on the free plan is **ephemeral**: every redeploy or
  service restart wipes the scores saved in `data/scores_*.json`. For
  long-term persistent scores, consider a paid **Render Disk** or a small
  external database (e.g. free Postgres on Render/Neon/Supabase) instead of
  file-based storage.
- Discord commands that read chat require **Message Content Intent** to be
  enabled (see step 2) — without it, the bot doesn't receive message text
  and can't award points.
