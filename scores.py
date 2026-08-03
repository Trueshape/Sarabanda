"""Simple score persistence, one JSON file per Discord server."""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def _path(guild_id: int) -> str:
    return os.path.join(DATA_DIR, f"scores_{guild_id}.json")


def load(guild_id: int) -> dict:
    path = _path(guild_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save(guild_id: int, data: dict) -> None:
    with open(_path(guild_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_points(guild_id: int, user_id: int, username: str, points: int) -> None:
    data = load(guild_id)
    key = str(user_id)
    if key not in data:
        data[key] = {"username": username, "points": 0}
    data[key]["username"] = username  # keep the display name up to date
    data[key]["points"] += points
    save(guild_id, data)


def leaderboard(guild_id: int, top: int = 10) -> list[tuple[str, int]]:
    data = load(guild_id)
    ranking = sorted(
        ((v["username"], v["points"]) for v in data.values()),
        key=lambda x: x[1],
        reverse=True,
    )
    return ranking[:top]


def reset(guild_id: int) -> None:
    save(guild_id, {})
