"""
Discord music quiz bot — slash commands.

Commands:
  /quiz start   -> start the quiz. ALL parameters are set directly in the
                   slash command menu: songs, source (playlist/track links
                   or random), category, artist, mode, round duration
  /quiz stop    -> stop the quiz and disconnect the bot
  /quiz leaderboard -> show the server's overall leaderboard
  /quiz reset   -> reset the server's leaderboard
  /quiz help    -> show help

Scoring rules:
  - OPEN ANSWER mode: first to type the correct TITLE -> 2 points,
    first to type the correct ARTIST -> 1 point (independent; typos,
    "feat.", "(Remix)" etc. are ignored)
  - MULTIPLE CHOICE mode: 4 buttons, one attempt per person. Answers are
    ephemeral (only visible to the person who clicked), so nobody can spoil
    the result for others. First correct click -> 3 points (title+artist
    together). Wrong on your one attempt -> no points, no retry this round.
    The round ends early once everyone currently in the voice channel has
    answered, otherwise it waits for the full round duration.
  - The overall leaderboard is shown after every round.
"""

import asyncio
import os
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import clemmy_schedule
import scores
from keep_alive import keep_alive
from matching import any_artist_match, is_close_match
from music_source import (
    SPECIAL_CATEGORIES,
    SpotifyProvider,
    fetch_deezer_genres,
    fetch_deezer_varied_random_tracks,
    fetch_random_tracks_by_category,
    fetch_special_category_tracks,
    fetch_tracks_by_artist_name,
    fetch_tracks_from_source,
    resolve_playable_url,
    search_deezer_artists,
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
    print(f"[WARNING] {e}")


class GuildGameState:
    """Game state for a single server."""

    def __init__(self):
        self.tracks: list[dict] = []
        self.used_indexes: set[int] = set()
        # Set when the quiz was started with the "artist" filter, so that
        # multiple-choice decoys can be double-checked against it (belt and
        # braces on top of the tracks already being artist-only).
        self.locked_artist: str | None = None
        self.voice_client: discord.VoiceClient | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self.active: bool = False
        self.round_state: "RoundState | None" = None
        self.task: asyncio.Task | None = None


class RoundState:
    def __init__(self, track: dict, mode: str = "open_answer"):
        self.track = track
        self.title = track["name"]
        self.artists = track["artists"]
        self.title_awarded = False
        self.artist_awarded = False
        self.done_event = asyncio.Event()
        self.mode = mode  # "open_answer" or "multiple_choice"
        self.correct_number: int | None = None
        self.options: list[dict] | None = None
        # Only used in multiple_choice mode (buttons):
        self.answered_users: set[int] = set()  # who already clicked a button (1 attempt each)
        self.expected_users: set[int] = set()  # who was in voice when the round started
        self.view: "MultipleChoiceView | None" = None
        self.message: discord.Message | None = None


game_states: dict[int, GuildGameState] = {}


def get_state(guild_id: int) -> GuildGameState:
    if guild_id not in game_states:
        game_states[guild_id] = GuildGameState()
    return game_states[guild_id]


class MultipleChoiceView(discord.ui.View):
    """Buttons for multiple choice mode. Every answer is visible ONLY to the
    person who clicked (ephemeral), so nobody can spoil the result for
    others. One attempt per person: if you get it wrong, no more points for
    this round (you can still see the outcome in the final reveal)."""

    def __init__(self, round_state: RoundState, guild_id: int, timeout: float):
        super().__init__(timeout=timeout)
        self.round_state = round_state
        self.guild_id = guild_id

        for i, opt in enumerate(round_state.options):
            label = f"{i + 1}) {opt['name']} — {', '.join(opt['artists'])}"
            if len(label) > 80:
                label = label[:77] + "..."
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            button.callback = self._make_callback(i + 1)
            self.add_item(button)

    def _make_callback(self, choice_number: int):
        async def callback(interaction: discord.Interaction):
            await self._handle_answer(interaction, choice_number)

        return callback

    async def _handle_answer(self, interaction: discord.Interaction, choice_number: int):
        rs = self.round_state
        user_id = interaction.user.id

        if user_id in rs.answered_users:
            await interaction.response.send_message(
                "You've already answered this round — wait for the next song!",
                ephemeral=True,
            )
            return

        rs.answered_users.add(user_id)
        is_correct = choice_number == rs.correct_number

        if is_correct and not rs.title_awarded:
            rs.title_awarded = True
            rs.artist_awarded = True
            scores.add_points(self.guild_id, user_id, str(interaction.user), 3)
            await interaction.response.send_message(
                "✅ Correct! +3 points (title+artist). Don't spoil it for the others 🤫",
                ephemeral=True,
            )
        elif is_correct:
            await interaction.response.send_message(
                "✅ That was the right answer, but someone else was faster: no points this round.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Wrong answer: no points this round (one attempt per person).",
                ephemeral=True,
            )

        # If everyone who was in voice when the round started has now answered, end early
        if rs.expected_users and rs.expected_users.issubset(rs.answered_users):
            rs.done_event.set()

    async def disable_all(self):
        for item in self.children:
            item.disabled = True
        if self.round_state.message:
            try:
                await self.round_state.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()


@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"[WARNING] Failed to sync slash commands: {e}")


# ---------------------------------------------------------------------------
# Unrelated standalone commands: Clemmy's weekly work schedule
# (nothing to do with the music quiz, just bolted onto the same bot)
# ---------------------------------------------------------------------------

ROME_TZ = ZoneInfo("Europe/Rome")


@bot.tree.command(name="edit_settimana", description="Set which days of the week Clemmy is at work")
@app_commands.describe(
    lunedi="Is Clemmy at work on Monday?",
    martedi="Is Clemmy at work on Tuesday?",
    mercoledi="Is Clemmy at work on Wednesday?",
    giovedi="Is Clemmy at work on Thursday?",
    venerdi="Is Clemmy at work on Friday?",
    sabato="Is Clemmy at work on Saturday?",
    domenica="Is Clemmy at work on Sunday?",
)
async def edit_settimana(
    interaction: discord.Interaction,
    lunedi: bool = None,
    martedi: bool = None,
    mercoledi: bool = None,
    giovedi: bool = None,
    venerdi: bool = None,
    sabato: bool = None,
    domenica: bool = None,
):
    updates = {
        "lunedi": lunedi,
        "martedi": martedi,
        "mercoledi": mercoledi,
        "giovedi": giovedi,
        "venerdi": venerdi,
        "sabato": sabato,
        "domenica": domenica,
    }

    if all(v is None for v in updates.values()):
        schedule = clemmy_schedule.get_schedule(interaction.guild.id)
    else:
        schedule = clemmy_schedule.update_schedule(interaction.guild.id, updates)

    lines = "\n".join(
        f"{'✅' if schedule[day] else '❌'} {day.capitalize()}"
        for day in clemmy_schedule.WEEKDAY_KEYS
    )
    await interaction.response.send_message(f"📅 Orario settimanale di Clemmy:\n{lines}")


@bot.tree.command(name="oggyclemmy", description="Controlla se oggi Clemmy è al lavoro")
async def oggyclemmy(interaction: discord.Interaction):
    today_weekday = datetime.now(ROME_TZ).weekday()  # 0=Monday ... 6=Sunday
    working = clemmy_schedule.is_working_today(interaction.guild.id, today_weekday)

    if working:
        await interaction.response.send_message("Oggi si lavora e si fattura")
    else:
        await interaction.response.send_message("Sì è a casa, GOGOGO")


# ---------------------------------------------------------------------------
# "/quiz" slash command group
# ---------------------------------------------------------------------------

quiz_group = app_commands.Group(name="quiz", description="Music quiz commands")
bot.tree.add_command(quiz_group)


def help_embed() -> discord.Embed:
    return discord.Embed(
        title="🎵 Music Quiz — Commands",
        color=discord.Color.blurple(),
        description=(
            "`/quiz start` — start the quiz: pick songs, source, category, "
            "artist, mode and round duration right in the slash command menu\n"
            "`/quiz stop` — stop the quiz\n"
            "`/quiz leaderboard` — show the leaderboard\n"
            "`/quiz reset` — reset the leaderboard\n\n"
            "**Open answer mode:** first to type the **title** gets **2 points**, "
            "first to type the **artist** gets **1 point**.\n"
            "**Multiple choice mode:** 4 buttons, one attempt each. First correct "
            "click gets **3 points**. Answers are private (ephemeral) until the "
            "round ends, so nobody spoils it for anyone else.\n"
            "Small typos, casing, 'feat.', '(Remix)' etc. are ignored.\n"
            "The overall leaderboard is shown after every round."
        ),
    )


@quiz_group.command(name="help", description="Show help for the music quiz")
async def quiz_help(interaction: discord.Interaction):
    await interaction.response.send_message(embed=help_embed())


async def category_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    current_lower = current.lower()

    try:
        genres = await asyncio.to_thread(fetch_deezer_genres)
    except Exception:
        genres = []

    choices = [
        app_commands.Choice(name=g["name"], value=f"genre:{g['id']}")
        for g in genres
        if current_lower in g["name"].lower()
    ]

    choices += [
        app_commands.Choice(name=info["label"], value=f"special:{key}")
        for key, info in SPECIAL_CATEGORIES.items()
        if current_lower in info["label"].lower()
    ]

    return choices[:25]


async def artist_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if not current or len(current.strip()) < 2:
        return []
    try:
        candidates = await asyncio.to_thread(search_deezer_artists, current.strip(), 15)
    except Exception:
        candidates = []
    return [app_commands.Choice(name=c["name"], value=c["name"]) for c in candidates[:25]]


@quiz_group.command(name="start", description="Start the music quiz")
@app_commands.describe(
    songs="How many songs to play (e.g. 10)",
    mode="How answers work: type freely, or pick from 4 buttons",
    source="Spotify/Deezer/iTunes link (playlist, single track, or multiple links separated by commas). Empty = random",
    category="Genre or special category (decades, J-Pop, K-Pop, anime, etc). Ignored if you use 'source' or 'artist'",
    artist="A specific artist's name (typo-tolerant). Ignored if you use 'source'",
    duration="Duration of each round in seconds (default 30)",
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="Open answer (type title/artist)", value="open_answer"),
        app_commands.Choice(name="Multiple choice (4 buttons)", value="multiple_choice"),
    ]
)
@app_commands.autocomplete(category=category_autocomplete, artist=artist_autocomplete)
async def quiz_start(
    interaction: discord.Interaction,
    songs: app_commands.Range[int, 1, 100],
    mode: app_commands.Choice[str],
    source: str = None,
    category: str = None,
    artist: str = None,
    duration: app_commands.Range[int, 5, 120] = ROUND_DURATION,
):
    state = get_state(interaction.guild.id)
    game_mode = mode.value

    if state.active:
        await interaction.response.send_message("⚠️ A quiz is already running in this server.")
        return

    member = interaction.user
    if not member.voice or not member.voice.channel:
        await interaction.response.send_message("❌ You need to be in a voice channel to start the quiz.")
        return

    # Immediate ack (Discord requires a response within 3 seconds)
    await interaction.response.send_message("🔎 Getting the songs ready, one moment...")
    channel = interaction.channel

    try:
        if source and source.strip().lower() != "random":
            if category or artist:
                await channel.send(
                    "ℹ️ The `category`/`artist` parameters are ignored when you specify a `source` (playlist/tracks)."
                )
            tracks = await asyncio.wait_for(
                asyncio.to_thread(fetch_tracks_from_source, source.strip(), spotify_provider),
                timeout=45,
            )
        elif artist:
            tracks = await asyncio.wait_for(
                asyncio.to_thread(fetch_tracks_by_artist_name, artist.strip(), limit=max(songs * 3, 30)),
                timeout=45,
            )
        elif category:
            if category.startswith("special:"):
                key = category.split(":", 1)[1]
                tracks = await asyncio.wait_for(
                    asyncio.to_thread(fetch_special_category_tracks, key, limit=max(songs * 3, 30)),
                    timeout=45,
                )
            else:
                genre_id = int(category.split(":", 1)[1]) if category.startswith("genre:") else int(category)
                try:
                    genres = await asyncio.to_thread(fetch_deezer_genres)
                    genre_name = next((g["name"] for g in genres if g["id"] == genre_id), "Pop")
                except Exception:
                    genre_name = "Pop"
                tracks = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetch_random_tracks_by_category,
                        genre_name,
                        genre_id,
                        limit=max(songs * 3, 30),
                    ),
                    timeout=45,
                )
        else:
            tracks = await asyncio.wait_for(
                asyncio.to_thread(fetch_deezer_varied_random_tracks, limit=max(songs * 3, 30)),
                timeout=45,
            )
    except asyncio.TimeoutError:
        await channel.send("❌ Fetching songs took too long (slow/unreachable network). Try `/quiz start` again.")
        return
    except Exception as e:
        await channel.send(f"❌ Error while fetching songs: `{e}`")
        return

    if not tracks:
        await channel.send("❌ No songs found for this category/artist/source, aborting startup.")
        return

    rounds = songs
    if len(tracks) < rounds:
        await channel.send(
            f"⚠️ I only found {len(tracks)} available songs: I'll do {len(tracks)} round(s) instead of {rounds}."
        )
        rounds = len(tracks)

    state.tracks = tracks
    state.used_indexes = set()
    # Only ever set for a real artist-locked quiz: use the artist name as it
    # actually came back on the fetched tracks (post fuzzy-resolution), so
    # the decoy filter below matches exactly.
    state.locked_artist = tracks[0]["artists"][0] if (artist and not source) else None

    voice_channel = member.voice.channel
    try:
        state.voice_client = await voice_channel.connect()
    except discord.ClientException:
        state.voice_client = interaction.guild.voice_client

    state.text_channel = channel
    state.active = True
    state.task = asyncio.create_task(run_quiz(interaction.guild.id, rounds, duration, game_mode))
    mode_label = "open answer" if game_mode == "open_answer" else "multiple choice (4 buttons)"
    await channel.send(
        f"🎬 Quiz started: **{rounds} round(s)**, **{duration}s** per round, **{mode_label}** mode. Get ready!"
    )


@quiz_group.command(name="stop", description="Stop the current quiz")
async def quiz_stop(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if not state.active:
        await interaction.response.send_message("There's no quiz running right now.")
        return

    state.active = False
    if state.round_state:
        state.round_state.done_event.set()
    if state.task:
        state.task.cancel()
    await interaction.response.send_message("🛑 Quiz stopped.")


@quiz_group.command(name="leaderboard", description="Show the server's overall leaderboard")
async def quiz_leaderboard(interaction: discord.Interaction):
    top = scores.leaderboard(interaction.guild.id)
    if not top:
        await interaction.response.send_message("No scores recorded yet.")
        return
    lines = "\n".join(
        f"{i+1}. **{name}** — {points} point(s)" for i, (name, points) in enumerate(top)
    )
    await interaction.response.send_message(f"🏆 Leaderboard:\n{lines}")


@quiz_group.command(name="reset", description="Reset the server's leaderboard")
async def quiz_reset(interaction: discord.Interaction):
    scores.reset(interaction.guild.id)
    await interaction.response.send_message("🔄 Leaderboard reset.")


# ---------------------------------------------------------------------------
# Game loop: rounds, playback, waiting for answers
# ---------------------------------------------------------------------------

async def run_quiz(
    guild_id: int,
    rounds: int,
    round_duration: int = ROUND_DURATION,
    game_mode: str = "open_answer",
):
    state = get_state(guild_id)
    channel = state.text_channel

    try:
        for round_number in range(1, rounds + 1):
            if not state.active:
                break

            track, audio_url = await pick_next_track(state)
            if track is None:
                await channel.send("⚠️ No more playable songs found, stopping here.")
                break

            round_state = RoundState(track, mode=game_mode)
            state.round_state = round_state

            artists_display = ", ".join(track["artists"])

            if game_mode == "multiple_choice":
                options, correct_number = build_multiple_choice_options(
                    track, state.tracks, locked_artist=state.locked_artist
                )
                round_state.options = options
                round_state.correct_number = correct_number

                if state.voice_client and state.voice_client.channel:
                    round_state.expected_users = {
                        m.id for m in state.voice_client.channel.members if not m.bot
                    }

                view = MultipleChoiceView(round_state, guild_id, timeout=round_duration + 5)
                round_state.view = view

                message = await channel.send(
                    f"🎧 **Round {round_number}/{rounds}** — now playing!\n"
                    f"Click the button with what you think is the correct answer. "
                    f"Your answer is private — nobody else will see it until the round ends.",
                    view=view,
                )
                round_state.message = message
            else:
                await channel.send(
                    f"🎧 **Round {round_number}/{rounds}** — now playing! "
                    f"Type the **title** (+2) and the **artist** (+1) here in chat."
                )

            try:
                audio_source = discord.FFmpegPCMAudio(audio_url)
                state.voice_client.play(audio_source)
            except Exception as e:
                await channel.send(f"⚠️ Playback error, skipping this song: `{e}`")
                continue

            try:
                await asyncio.wait_for(round_state.done_event.wait(), timeout=round_duration)
            except asyncio.TimeoutError:
                pass

            if state.voice_client and state.voice_client.is_playing():
                state.voice_client.stop()

            if round_state.view:
                await round_state.view.disable_all()

            reveal = f"⏱️ Time's up! The song was: **{track['name']}** — {artists_display}"
            if not round_state.title_awarded and not round_state.artist_awarded:
                reveal += "\nNobody got it this round 😅"
            await channel.send(reveal)

            # Updated overall leaderboard after every round
            top = scores.leaderboard(guild_id)
            if top:
                lines = "\n".join(
                    f"{i+1}. **{name}** — {points} point(s)" for i, (name, points) in enumerate(top)
                )
                await channel.send(f"📊 Overall leaderboard:\n{lines}")
            else:
                await channel.send("📊 No points scored yet.")

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
            await channel.send("🏁 Quiz finished! Thanks for playing 🎶")


def build_multiple_choice_options(track: dict, all_tracks: list[dict], locked_artist: str | None = None):
    """Builds up to 4 options (1 correct + up to 3 'decoys' taken from the
    other tracks in this session), shuffled. Returns (options, correct_number).

    When `locked_artist` is set (artist-only quiz), decoys are additionally
    filtered to that exact artist as a safety net, so a multiple-choice
    question never shows another artist's song as a wrong answer even if
    something unexpected ended up in the track pool."""
    decoy_pool = [t for t in all_tracks if t is not track]
    if locked_artist:
        target = locked_artist.strip().lower()
        artist_only_pool = [t for t in decoy_pool if t["artists"][0].strip().lower() == target]
        if artist_only_pool:
            decoy_pool = artist_only_pool
    random.shuffle(decoy_pool)
    decoys = decoy_pool[:3]

    options = decoys + [track]
    random.shuffle(options)

    correct_number = next(i for i, opt in enumerate(options) if opt is track) + 1
    return options, correct_number


async def pick_next_track(state: GuildGameState):
    """Picks a track not used yet with playable audio (Spotify or Deezer fallback)."""
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


# ---------------------------------------------------------------------------
# Listening for answers in open_answer mode (multiple_choice uses buttons)
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    state = get_state(message.guild.id)
    round_state = state.round_state

    if round_state and round_state.mode == "open_answer":
        text = message.content.strip()
        awarded_something = False

        if not round_state.title_awarded and is_close_match(text, round_state.title):
            round_state.title_awarded = True
            scores.add_points(message.guild.id, message.author.id, str(message.author), 2)
            await message.channel.send(f"✅ {message.author.mention} got the **title**! +2 points")
            awarded_something = True

        if not round_state.artist_awarded and any_artist_match(text, round_state.artists):
            round_state.artist_awarded = True
            scores.add_points(message.guild.id, message.author.id, str(message.author), 1)
            await message.channel.send(f"✅ {message.author.mention} got the **artist**! +1 point")
            awarded_something = True

        if awarded_something and round_state.title_awarded and round_state.artist_awarded:
            round_state.done_event.set()


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN missing from the .env file")
    keep_alive()  # harmless no-op if you're not on Render; required for the free tier
    bot.run(DISCORD_TOKEN)
