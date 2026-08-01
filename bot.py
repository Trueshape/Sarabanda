"""
Bot Discord per quiz musicali.

Comandi:
  !quiz start        -> avvia il quiz: il bot chiede in chat numero di canzoni,
                        playlist Spotify (o "casuale"), modalità di gioco
                        (aperta o scelta multipla) e durata round
  !quiz stop         -> ferma il quiz e disconnette il bot
  !quiz classifica   -> mostra la classifica del server
  !quiz reset        -> azzera la classifica del server
  !quiz help         -> mostra l'aiuto

Regole punteggio:
  - Modalità APERTA: primo a indovinare il TITOLO -> 2 punti,
    primo a indovinare l'ARTISTA -> 1 punto (indipendenti, refusi/feat./ecc. ignorati)
  - Modalità SCELTA MULTIPLA: 4 opzioni numerate, primo a scegliere il numero
    corretto -> 3 punti (titolo+artista insieme)
  - Dopo ogni round viene mostrata la classifica generale aggiornata
"""

import asyncio
import os
import random

import discord
from discord.ext import commands
from dotenv import load_dotenv

import scores
from keep_alive import keep_alive
from matching import any_artist_match, is_close_match
from music_source import (
    SpotifyProvider,
    fetch_deezer_chart_tracks,
    fetch_tracks_from_source,
    resolve_playable_url,
)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ROUND_DURATION = int(os.getenv("ROUND_DURATION_SECONDS", "30"))
SECONDS_BETWEEN_ROUNDS = 5
MAX_TRACK_FETCH_ATTEMPTS = 5

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

spotify_provider: SpotifyProvider | None = None
try:
    spotify_provider = SpotifyProvider()
except RuntimeError as e:
    print(f"[ATTENZIONE] {e}")


class GuildGameState:
    """Stato del gioco per un singolo server."""

    def __init__(self):
        self.playlist_id: str | None = None
        self.tracks: list[dict] = []
        self.used_indexes: set[int] = set()
        self.voice_client: discord.VoiceClient | None = None
        self.text_channel: discord.TextChannel | None = None
        self.active: bool = False
        self.round_state: "RoundState | None" = None
        self.task: asyncio.Task | None = None


class RoundState:
    def __init__(self, track: dict, mode: str = "aperta"):
        self.track = track
        self.title = track["name"]
        self.artists = track["artists"]
        self.title_awarded = False
        self.artist_awarded = False
        self.done_event = asyncio.Event()
        self.mode = mode  # "aperta" oppure "scelta_multipla"
        self.correct_number: int | None = None
        self.options: list[dict] | None = None


game_states: dict[int, GuildGameState] = {}


def get_state(guild_id: int) -> GuildGameState:
    if guild_id not in game_states:
        game_states[guild_id] = GuildGameState()
    return game_states[guild_id]


@bot.event
async def on_ready():
    print(f"Bot connesso come {bot.user}")


@bot.group(invoke_without_command=True)
async def quiz(ctx: commands.Context):
    await send_help(ctx)


async def send_help(ctx: commands.Context):
    embed = discord.Embed(
        title="🎵 Quiz Musicale — Comandi",
        color=discord.Color.blurple(),
        description=(
            "`!quiz start` — avvia il quiz: ti chiedo numero canzoni, playlist "
            "(o `casuale`), modalità di gioco e durata round\n"
            "`!quiz stop` — ferma il quiz\n"
            "`!quiz classifica` — mostra la classifica\n"
            "`!quiz reset` — azzera la classifica\n\n"
            "**Modalità aperta:** chi indovina il **titolo** per primo prende **2 punti**, "
            "chi indovina l'**artista** per primo prende **1 punto**.\n"
            "**Modalità scelta multipla:** 4 opzioni numerate, chi indovina il numero "
            "corretto per primo prende **3 punti**.\n"
            "Piccoli refusi, maiuscole/minuscole, 'feat.', '(Remix)' ecc. vengono ignorati.\n"
            "Dopo ogni round vedrai la classifica generale aggiornata."
        ),
    )
    await ctx.send(embed=embed)


@quiz.command(name="help")
async def quiz_help(ctx: commands.Context):
    await send_help(ctx)


SETUP_TIMEOUT = 60  # secondi di attesa per ogni risposta durante la configurazione


@quiz.command(name="start")
async def quiz_start(ctx: commands.Context):
    state = get_state(ctx.guild.id)

    if state.active:
        await ctx.send("⚠️ Il quiz è già in corso in questo server.")
        return

    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("❌ Devi essere in un canale vocale per avviare il quiz.")
        return

    def check(m: discord.Message) -> bool:
        return m.author == ctx.author and m.channel == ctx.channel

    # 1) Quante canzoni
    await ctx.send("🎵 Quante canzoni vuoi riprodurre? (scrivi un numero, es. `10`)")
    try:
        reply = await bot.wait_for("message", check=check, timeout=SETUP_TIMEOUT)
        rounds = int(reply.content.strip())
        if rounds <= 0:
            raise ValueError
    except asyncio.TimeoutError:
        await ctx.send("❌ Tempo scaduto, annullo l'avvio.")
        return
    except ValueError:
        await ctx.send("❌ Numero non valido, annullo l'avvio. Riprova con `!quiz start`.")
        return

    # 2) Playlist specifica o casuale
    await ctx.send(
        "🎧 Vuoi usare una playlist **Spotify** o **Deezer** specifica, oppure canzoni **casuali**?\n"
        "Incolla il link della playlist (Spotify o Deezer), oppure scrivi `casuale`."
    )
    try:
        reply = await bot.wait_for("message", check=check, timeout=SETUP_TIMEOUT)
        source_choice = reply.content.strip()
    except asyncio.TimeoutError:
        await ctx.send("❌ Tempo scaduto, annullo l'avvio.")
        return

    # 3) Modalità di gioco
    await ctx.send(
        "🕹️ Che modalità di gioco vuoi usare?\n"
        "Scrivi `aperta` per rispondere scrivendo liberamente titolo/artista, "
        "oppure `scelta multipla` per rispondere con un numero da 4 opzioni."
    )
    try:
        reply = await bot.wait_for("message", check=check, timeout=SETUP_TIMEOUT)
        mode_input = reply.content.strip().lower()
        if mode_input in ("aperta", "1", "open", "libera"):
            game_mode = "aperta"
        elif mode_input in ("scelta multipla", "scelta_multipla", "2", "multipla", "multiple choice"):
            game_mode = "scelta_multipla"
        else:
            raise ValueError
    except asyncio.TimeoutError:
        await ctx.send("❌ Tempo scaduto, annullo l'avvio.")
        return
    except ValueError:
        await ctx.send("❌ Modalità non riconosciuta, annullo l'avvio. Riprova con `!quiz start`.")
        return

    # 4) Durata round
    await ctx.send(
        f"⏱️ Quanti secondi deve durare ogni round? (scrivi un numero, oppure `default` per {ROUND_DURATION}s)"
    )
    try:
        reply = await bot.wait_for("message", check=check, timeout=SETUP_TIMEOUT)
        duration_input = reply.content.strip().lower()
        round_duration = ROUND_DURATION if duration_input == "default" else int(duration_input)
        if round_duration <= 0:
            raise ValueError
    except asyncio.TimeoutError:
        await ctx.send("❌ Tempo scaduto, annullo l'avvio.")
        return
    except ValueError:
        await ctx.send("❌ Valore non valido, annullo l'avvio. Riprova con `!quiz start`.")
        return

    # Recupero brani
    await ctx.send("🔎 Preparo i brani, un attimo...")
    try:
        if source_choice.lower() == "casuale":
            tracks = await asyncio.wait_for(
                asyncio.to_thread(fetch_deezer_chart_tracks, limit=max(rounds * 3, 30)),
                timeout=45,
            )
        else:
            tracks = await asyncio.wait_for(
                asyncio.to_thread(fetch_tracks_from_source, source_choice, spotify_provider),
                timeout=45,
            )
    except asyncio.TimeoutError:
        await ctx.send("❌ Il recupero dei brani ha impiegato troppo tempo (rete lenta/irraggiungibile). Riprova con `!quiz start`.")
        return
    except Exception as e:
        await ctx.send(f"❌ Errore nel recupero dei brani: `{e}`")
        return

    if not tracks:
        await ctx.send("❌ Nessun brano trovato, annullo l'avvio.")
        return

    if len(tracks) < rounds:
        await ctx.send(
            f"⚠️ Ho trovato solo {len(tracks)} brani disponibili: farò {len(tracks)} round invece di {rounds}."
        )
        rounds = len(tracks)

    state.tracks = tracks
    state.used_indexes = set()

    voice_channel = ctx.author.voice.channel
    try:
        state.voice_client = await voice_channel.connect()
    except discord.ClientException:
        state.voice_client = ctx.guild.voice_client

    state.text_channel = ctx.channel
    state.active = True
    state.task = asyncio.create_task(run_quiz(ctx.guild.id, rounds, round_duration, game_mode))
    mode_label = "risposta aperta" if game_mode == "aperta" else "scelta multipla (4 opzioni)"
    await ctx.send(
        f"🎬 Quiz avviato: **{rounds} round**, **{round_duration}s** a round, modalità **{mode_label}**. Preparatevi!"
    )


async def run_quiz(
    guild_id: int,
    rounds: int,
    round_duration: int = ROUND_DURATION,
    game_mode: str = "aperta",
):
    state = get_state(guild_id)
    channel = state.text_channel

    try:
        for round_number in range(1, rounds + 1):
            if not state.active:
                break

            track, audio_url = await pick_next_track(state)
            if track is None:
                await channel.send("⚠️ Non ho trovato altri brani riproducibili disponibili, mi fermo qui.")
                break

            round_state = RoundState(track, mode=game_mode)
            state.round_state = round_state

            artists_display = ", ".join(track["artists"])

            if game_mode == "scelta_multipla":
                options, correct_number = build_multiple_choice_options(track, state.tracks)
                round_state.options = options
                round_state.correct_number = correct_number
                options_lines = "\n".join(
                    f"**{i+1})** {opt['name']} — {', '.join(opt['artists'])}"
                    for i, opt in enumerate(options)
                )
                await channel.send(
                    f"🎧 **Round {round_number}/{rounds}** — in riproduzione!\n"
                    f"{options_lines}\n"
                    f"Rispondi con il **numero** corretto in chat! (vale 3 punti)"
                )
            else:
                await channel.send(
                    f"🎧 **Round {round_number}/{rounds}** — in riproduzione! "
                    f"Scrivi il **titolo** (+2) e l'**artista** (+1) qui in chat."
                )

            try:
                source = discord.FFmpegPCMAudio(audio_url)
                state.voice_client.play(source)
            except Exception as e:
                await channel.send(f"⚠️ Errore riproduzione, salto il brano: `{e}`")
                continue

            try:
                await asyncio.wait_for(round_state.done_event.wait(), timeout=round_duration)
            except asyncio.TimeoutError:
                pass

            if state.voice_client and state.voice_client.is_playing():
                state.voice_client.stop()

            reveal = f"⏱️ Tempo! Il brano era: **{track['name']}** — {artists_display}"
            if not round_state.title_awarded and not round_state.artist_awarded:
                reveal += "\nNessuno ha indovinato questo giro 😅"
            await channel.send(reveal)

            # Classifica generale aggiornata dopo ogni round
            top = scores.leaderboard(guild_id)
            if top:
                lines = "\n".join(
                    f"{i+1}. **{name}** — {points} punti" for i, (name, points) in enumerate(top)
                )
                await channel.send(f"📊 Classifica generale:\n{lines}")
            else:
                await channel.send("📊 Nessun punto ancora assegnato.")

            state.round_state = None
            await asyncio.sleep(SECONDS_BETWEEN_ROUNDS)

    finally:
        state.active = False
        state.round_state = None
        if state.voice_client:
            try:
                await state.voice_client.disconnect()
            except Exception:
                pass
            state.voice_client = None

        if channel:
            await channel.send("🏁 Quiz terminato! Grazie per aver giocato 🎶")


def build_multiple_choice_options(track: dict, all_tracks: list[dict]):
    """Costruisce fino a 4 opzioni (1 corretta + fino a 3 'distrattori' presi
    dagli altri brani della sessione), mescolate. Ritorna (options, correct_number)."""
    decoy_pool = [t for t in all_tracks if t is not track]
    random.shuffle(decoy_pool)
    decoys = decoy_pool[:3]

    options = decoys + [track]
    random.shuffle(options)

    correct_number = next(i for i, opt in enumerate(options) if opt is track) + 1
    return options, correct_number


async def pick_next_track(state: GuildGameState):
    """Sceglie un brano non ancora usato con audio riproducibile (Spotify o fallback Deezer)."""
    remaining = [i for i in range(len(state.tracks)) if i not in state.used_indexes]
    random.shuffle(remaining)

    attempts = 0
    for idx in remaining:
        if attempts >= MAX_TRACK_FETCH_ATTEMPTS:
            break
        attempts += 1
        track = state.tracks[idx]
        try:
            audio_url = await asyncio.wait_for(
                asyncio.to_thread(resolve_playable_url, track), timeout=15
            )
        except asyncio.TimeoutError:
            audio_url = None
        state.used_indexes.add(idx)
        if audio_url:
            return track, audio_url

    return None, None


@quiz.command(name="stop")
async def quiz_stop(ctx: commands.Context):
    state = get_state(ctx.guild.id)
    if not state.active:
        await ctx.send("Non c'è nessun quiz in corso.")
        return

    state.active = False
    if state.round_state:
        state.round_state.done_event.set()
    if state.task:
        state.task.cancel()
    await ctx.send("🛑 Quiz fermato.")


@quiz.command(name="classifica")
async def quiz_classifica(ctx: commands.Context):
    top = scores.leaderboard(ctx.guild.id)
    if not top:
        await ctx.send("Nessun punteggio registrato ancora.")
        return
    lines = "\n".join(
        f"{i+1}. **{name}** — {points} punti" for i, (name, points) in enumerate(top)
    )
    await ctx.send(f"🏆 Classifica:\n{lines}")


@quiz.command(name="reset")
async def quiz_reset(ctx: commands.Context):
    scores.reset(ctx.guild.id)
    await ctx.send("🔄 Classifica azzerata.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    state = get_state(message.guild.id)
    round_state = state.round_state

    if round_state and not message.content.startswith("!"):
        text = message.content.strip()

        if round_state.mode == "scelta_multipla":
            if (
                not (round_state.title_awarded and round_state.artist_awarded)
                and text.isdigit()
                and int(text) == round_state.correct_number
            ):
                round_state.title_awarded = True
                round_state.artist_awarded = True
                scores.add_points(message.guild.id, message.author.id, str(message.author), 3)
                await message.channel.send(
                    f"✅ {message.author.mention} ha indovinato! +3 punti (titolo+artista)"
                )
                round_state.done_event.set()
        else:
            awarded_something = False

            if not round_state.title_awarded and is_close_match(text, round_state.title):
                round_state.title_awarded = True
                scores.add_points(message.guild.id, message.author.id, str(message.author), 2)
                await message.channel.send(f"✅ {message.author.mention} ha indovinato il **titolo**! +2 punti")
                awarded_something = True

            if not round_state.artist_awarded and any_artist_match(text, round_state.artists):
                round_state.artist_awarded = True
                scores.add_points(message.guild.id, message.author.id, str(message.author), 1)
                await message.channel.send(f"✅ {message.author.mention} ha indovinato l'**artista**! +1 punto")
                awarded_something = True

            if awarded_something and round_state.title_awarded and round_state.artist_awarded:
                round_state.done_event.set()

    await bot.process_commands(message)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN mancante nel file .env")
    keep_alive()  # no-op innocuo se non sei su Render; necessario per il free tier
    bot.run(DISCORD_TOKEN)
