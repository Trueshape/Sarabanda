"""Persistence for Clemmy's weekly work schedule, one JSON file per Discord server.

Unrelated to the music quiz feature; just a small standalone utility bolted
onto the same bot.
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

WEEKDAY_KEYS = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]

# Python's date.weekday(): 0=Monday ... 6=Sunday, same order as WEEKDAY_KEYS
WEEKDAY_INDEX_TO_KEY = dict(enumerate(WEEKDAY_KEYS))


def _path(guild_id: int) -> str:
    return os.path.join(DATA_DIR, f"clemmy_{guild_id}.json")


def get_schedule(guild_id: int) -> dict:
    """Returns {'lunedi': bool, ..., 'domenica': bool}. Defaults to all False
    (not configured yet) for any day not explicitly set."""
    path = _path(guild_id)
    if not os.path.exists(path):
        return {day: False for day in WEEKDAY_KEYS}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {}
    return {day: bool(data.get(day, False)) for day in WEEKDAY_KEYS}


def update_schedule(guild_id: int, updates: dict) -> dict:
    """Applies only the provided day updates (True/False), leaves the rest
    untouched. Returns the full updated schedule."""
    schedule = get_schedule(guild_id)
    for day, value in updates.items():
        if value is not None and day in schedule:
            schedule[day] = bool(value)

    with open(_path(guild_id), "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)

    return schedule


def is_working_today(guild_id: int, weekday_index: int) -> bool:
    """weekday_index follows Python's date.weekday(): 0=Monday ... 6=Sunday."""
    schedule = get_schedule(guild_id)
    day_key = WEEKDAY_INDEX_TO_KEY[weekday_index]
    return schedule[day_key]
