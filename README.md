# 🎵 Discord Bot — Music Quiz

A bot that joins a voice channel, plays songs sourced entirely from Deezer's
public API (random chart, a music genre/special category, or a specific
artist), and awards points to whoever guesses the title and artist in chat.

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

## 3. Installation

```bash
cd discord-quiz-bot
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in:
- `DISCORD_TOKEN` (from step 2)

## 4. Run it

```bash
python bot.py
```

## 5. Using it in Discord

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
- **category** (optional, ignored if you use `artist`): filters
  random songs by music genre (Classical, Pop, Rap/Hip Hop, Dance, Rock,
  Alternative, Metal, Electro, Jazz) or by special
  category: **decades** (80s/90s/2000s/2010s/2020s), **Recent Hits**,
  **Trending Now**, **J-Pop**, **J-Rock**, **K-Pop**, **Anime**, **Film**,
  **Games**. As you type,
  Discord suggests the available options (autocomplete). Leave empty to use
  the global chart
- **artist** (optional): type a specific
  artist's name for a quiz built entirely from their own songs (top tracks
  plus deep cuts from their studio albums). Tolerates
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

- **Deezer-only**: every song source — `random` mode, categories, special
  categories, and artist quizzes — is built exclusively from Deezer's
  public API, which needs no account, no API key, and no authentication of
  any kind, so it always works out of the box.
- **Music categories**: the plain-genre slots in the `category` autocomplete
  are curated, not the full raw Deezer genre list — only **Classical, Pop,
  Rap/Hip Hop, Dance, Rock, Alternative, Metal, Electro, Jazz** (see
  `GENRE_ALLOWLIST` in `music_source.py`) show up, matched against Deezer's
  live `/genre` names (cached for one hour) so the real IDs are always
  correct even if Deezer changes them. When you pick one, the bot combines
  the Deezer chart filtered by that genre with a public Deezer playlist
  found by searching for the genre name (and a second search pass with
  " hits" appended if more variety is still needed), so it isn't just the
  same chart every time.
- **"Random" mode variety**: instead of always returning the global Deezer
  chart, `random` mode also mixes in the charts of a few randomly-picked
  Deezer genres each time you start a quiz, for more variety across runs.
- **Special categories** (decades, Recent Hits, Trending Now, J-Pop, J-Rock,
  K-Pop, Anime, Film, Games): these don't map to a direct Deezer genre (Film
  and Games in particular come merged as a single genre in Deezer's own
  genre list, so they're split here into two real categories), so the bot
  dynamically searches for a relevant public Deezer playlist at request
  time (no fragile hardcoded IDs). If the search finds nothing suitable for
  a very niche category, the bot reports an error instead of returning
  irrelevant random results. Special categories are always listed first in
  the `category` autocomplete, ahead of the plain Deezer genres, so they
  don't get crowded out by Discord's 25-choice limit (see next point).
- **Discord's 25-choice autocomplete limit**: Discord caps every
  autocomplete field at 25 suggestions — anything past that simply isn't
  sent, no error is raised. With 13 special categories plus the 9 curated
  genres, the `category` field currently shows 22 choices total, comfortably
  under the cap, so nothing gets cut off. If you ever add enough categories
  to go past 25, special categories go first (see above) since they're the
  curated ones, and any genres beyond the remaining slots would be silently
  dropped. Both groups are sorted alphabetically.
- **Artist search, strictly one-artist**: uses Deezer's artist search
  combined with `rapidfuzz` to tolerate typos, then builds a pool from that
  artist's top tracks plus deep cuts from their studio albums (compilation
  albums and any track credited to a different main artist are excluded).
  Every song played, and every wrong-answer button in multiple choice mode,
  is guaranteed to be from that one artist — there's also a safety filter
  when building multiple-choice options that double-checks each decoy
  against the locked artist before it can appear as a button.
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
token (once published on GitHub it should be considered compromised).

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
