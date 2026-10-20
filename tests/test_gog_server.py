"""Tests for GOG MCP tool filtering and pagination."""

from __future__ import annotations

import pytest

from gamelibs_mcp import server


@pytest.mark.asyncio
async def test_gog_list_games_filters_sorts_and_paginates(monkeypatch):
    games = [
        {
            "release_key": "z",
            "title": "Zeta",
            "playtime_minutes": 120,
            "playtime_hours": 2.0,
            "last_played": None,
        },
        {
            "release_key": "a",
            "title": "Alpha",
            "playtime_minutes": 60,
            "playtime_hours": 1.0,
            "last_played": "2025-01-01T00:00:00Z",
        },
        {
            "release_key": "b",
            "title": "Beta",
            "playtime_minutes": 30,
            "playtime_hours": 0.5,
            "last_played": None,
        },
    ]
    monkeypatch.setattr(server.gog_api, "list_games", lambda: games)

    result = await server.gog_list_games(search="a", sort="name", limit=1)

    assert result == {
        "total": 3,
        "limit": 1,
        "offset": 0,
        "count": 1,
        "games": [games[1]],
    }


@pytest.mark.asyncio
async def test_gog_get_game_details_caps_release_keys(monkeypatch):
    monkeypatch.setattr(server.gog_api, "get_game_details", lambda _: pytest.fail("called"))

    result = await server.gog_get_game_details(["release"] * 51)

    assert "error" in result
    assert "max 50" in result["error"]
