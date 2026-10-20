"""Read GOG Galaxy's local database for game titles and playtime."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from .. import config
from ..http import ApiError

_GAMES_QUERY = """
SELECT
    lr.releaseKey AS release_key,
    COALESCE(
        json_extract(gp.value, '$.title'),
        json_extract(gp.value, '$.originalTitle')
    ) AS title,
    gt.minutesInGame AS minutes_in_game,
    lpd.lastPlayedDate AS last_played
FROM GameTimes gt
JOIN LibraryReleases lr
    ON lr.releaseKey = gt.releaseKey
JOIN GamePieces gp
    ON gp.releaseKey = lr.releaseKey
JOIN GamePieceTypes gpt
    ON gpt.id = gp.gamePieceTypeId
   AND gpt.type = 'title'
LEFT JOIN LastPlayedDates lpd
    ON lpd.userId = gt.userId
   AND lpd.gameReleaseKey = gt.releaseKey
WHERE gt.userId = ?
ORDER BY gt.minutesInGame DESC
"""


def _candidate_database_paths() -> list[Path]:
    home = Path.home()
    if sys.platform.startswith("win"):
        program_data = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        local_app_data = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roots = [program_data, local_app_data]
    elif sys.platform == "darwin":
        roots = [home / "Library" / "Application Support"]
    else:
        data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
        roots = [data_home]
    return [
        root / "GOG.com" / "Galaxy" / "storage" / "galaxy-2.0.db"
        for root in roots
    ]


def find_database() -> Path | None:
    """Find the GOG Galaxy database, honoring GOG_DB_PATH when configured."""
    override = config.gog_database_override()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    return next((path for path in _candidate_database_paths() if path.is_file()), None)


def _database_path() -> Path:
    path = find_database()
    if path is None:
        override = config.gog_database_override()
        if override:
            raise config.ConfigError(f"GOG Galaxy database not found at GOG_DB_PATH: {override}")
        raise config.ConfigError(
            "GOG Galaxy database not found. Set GOG_DB_PATH to the galaxy-2.0.db file."
        )
    return path


def _selected_user_id(connection: sqlite3.Connection) -> str | None:
    configured = config.gog_user_id()
    if configured:
        return configured

    user_ids = [str(row[0]) for row in connection.execute(
        "SELECT DISTINCT userId FROM GameTimes WHERE userId IS NOT NULL"
    )]
    if len(user_ids) > 1:
        raise config.ConfigError(
            "Multiple GOG profiles were found in the database. Set GOG_USER_ID to select one."
        )
    return user_ids[0] if user_ids else None


def _read_games() -> list[dict[str, Any]]:
    path = _database_path()
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            user_id = _selected_user_id(connection)
            if user_id is None:
                return []
            rows = connection.execute(_GAMES_QUERY, (user_id,)).fetchall()
    except sqlite3.Error as exc:
        raise ApiError(f"Could not read GOG Galaxy database at {path}: {exc}") from exc

    games = []
    for row in rows:
        release_key = str(row["release_key"])
        title = row["title"]
        if not isinstance(title, str) or not title:
            title = release_key
        games.append(
            {
                "release_key": release_key,
                "title": title,
                "playtime_minutes": int(row["minutes_in_game"] or 0),
                "playtime_hours": round((row["minutes_in_game"] or 0) / 60, 1),
                "last_played": row["last_played"],
            }
        )
    return games


def list_games() -> list[dict[str, Any]]:
    """Return game records from the selected local GOG Galaxy profile."""
    return _read_games()


def get_game_details(release_keys: list[str]) -> list[dict[str, Any]]:
    """Return local playtime details for requested GOG release keys."""
    by_release_key = {game["release_key"]: game for game in _read_games()}
    return [
        by_release_key.get(
            release_key,
            {"release_key": release_key, "error": "not found in GOG Galaxy database"},
        )
        for release_key in release_keys
    ]
