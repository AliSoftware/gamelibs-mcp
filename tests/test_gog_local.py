"""Offline tests for reading the local GOG Galaxy database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gamelibs_mcp.gog import gog


def _make_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE GameTimes (
                userId TEXT,
                releaseKey TEXT,
                minutesInGame INTEGER
            );
            CREATE TABLE LibraryReleases (releaseKey TEXT);
            CREATE TABLE GamePieces (
                releaseKey TEXT,
                gamePieceTypeId INTEGER,
                value TEXT
            );
            CREATE TABLE GamePieceTypes (id INTEGER, type TEXT);
            CREATE TABLE LastPlayedDates (
                userId TEXT,
                gameReleaseKey TEXT,
                lastPlayedDate TEXT
            );
            INSERT INTO GamePieceTypes VALUES (1, 'title');
            INSERT INTO LibraryReleases VALUES ('release-a'), ('release-b');
            INSERT INTO GamePieces VALUES
                ('release-a', 1, '{"title":"Alpha","originalTitle":"Alpha Original"}'),
                ('release-b', 1, '{"originalTitle":"Beta"}');
            INSERT INTO GameTimes VALUES
                ('profile-a', 'release-a', 125),
                ('profile-a', 'release-b', 60),
                ('profile-b', 'release-a', 10);
            INSERT INTO LastPlayedDates VALUES
                ('profile-a', 'release-a', '2025-01-02T03:04:05Z');
            """
        )


def test_list_games_uses_only_selected_profile(tmp_path: Path, monkeypatch):
    database = tmp_path / "galaxy-2.0.db"
    _make_database(database)
    monkeypatch.setattr(gog.config, "gog_database_override", lambda: str(database))
    monkeypatch.setattr(gog.config, "gog_user_id", lambda: "profile-a")

    assert gog.list_games() == [
        {
            "release_key": "release-a",
            "title": "Alpha",
            "playtime_minutes": 125,
            "playtime_hours": 2.1,
            "last_played": "2025-01-02T03:04:05Z",
        },
        {
            "release_key": "release-b",
            "title": "Beta",
            "playtime_minutes": 60,
            "playtime_hours": 1.0,
            "last_played": None,
        },
    ]


def test_user_is_detected_when_database_has_one_profile(tmp_path: Path, monkeypatch):
    database = tmp_path / "galaxy-2.0.db"
    _make_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM GameTimes WHERE userId = 'profile-b'")
    monkeypatch.setattr(gog.config, "gog_database_override", lambda: str(database))
    monkeypatch.setattr(gog.config, "gog_user_id", lambda: None)

    assert len(gog.list_games()) == 2


def test_multiple_profiles_require_selection(tmp_path: Path, monkeypatch):
    database = tmp_path / "galaxy-2.0.db"
    _make_database(database)
    monkeypatch.setattr(gog.config, "gog_database_override", lambda: str(database))
    monkeypatch.setattr(gog.config, "gog_user_id", lambda: None)

    with pytest.raises(gog.config.ConfigError, match="GOG_USER_ID"):
        gog.list_games()


def test_get_game_details_reports_unknown_release_keys(tmp_path: Path, monkeypatch):
    database = tmp_path / "galaxy-2.0.db"
    _make_database(database)
    monkeypatch.setattr(gog.config, "gog_database_override", lambda: str(database))
    monkeypatch.setattr(gog.config, "gog_user_id", lambda: "profile-a")

    details = gog.get_game_details(["release-b", "unknown"])
    assert details[0]["title"] == "Beta"
    assert details[1] == {
        "release_key": "unknown",
        "error": "not found in GOG Galaxy database",
    }


def test_missing_database_is_reported(monkeypatch):
    monkeypatch.setattr(gog.config, "gog_database_override", lambda: None)
    monkeypatch.setattr(gog, "_candidate_database_paths", lambda: [])

    with pytest.raises(gog.config.ConfigError, match="GOG_DB_PATH"):
        gog.list_games()


def test_find_database_uses_detected_candidate(tmp_path: Path, monkeypatch):
    database = tmp_path / "galaxy-2.0.db"
    database.touch()
    monkeypatch.setattr(gog.config, "gog_database_override", lambda: None)
    monkeypatch.setattr(gog, "_candidate_database_paths", lambda: [database])

    assert gog.find_database() == database
