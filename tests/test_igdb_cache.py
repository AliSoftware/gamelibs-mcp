"""Tests for IGDB's persistent per-id result cache (no real network)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gamelibs_mcp.igdb import client

# Canned IGDB rows the fake _query serves, keyed by game id.
_GAMES = {
    1: {"id": 1, "name": "Alpha", "category": 0},
    2: {"id": 2, "name": "Beta", "category": 0},
    3: {"id": 3, "name": "Some DLC", "category": 1},  # dlc_addon
}


@pytest.fixture
def fresh_cache(tmp_path: Path, monkeypatch):
    """Isolate the module cache to a temp dir and reset in-memory state."""
    monkeypatch.setattr(client.config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(client, "_cache_mem", None)


def _fake_query_counter(monkeypatch):
    calls = {"games": 0}

    async def fake_query(endpoint: str, body: str):
        calls["games"] += 1
        ids = [int(x) for x in re.search(r"id = \(([\d,]+)\)", body).group(1).split(",")]
        return [_GAMES[i] for i in ids if i in _GAMES]

    monkeypatch.setattr(client, "_query", fake_query)
    return calls


async def test_details_cache_hit_avoids_network(fresh_cache, monkeypatch):
    calls = _fake_query_counter(monkeypatch)

    first = await client.get_game_details([1, 2], include_dlcs=False)
    assert {g["name"] for g in first} == {"Alpha", "Beta"}
    assert calls["games"] == 1

    # Second identical call is served entirely from cache — no new request.
    second = await client.get_game_details([1, 2], include_dlcs=False)
    assert {g["name"] for g in second} == {"Alpha", "Beta"}
    assert calls["games"] == 1

    # Internal fields never leak into the returned payload.
    assert all(not any(k.startswith("_") for k in g) for g in second)


async def test_details_dlc_filter_applied_to_cached_record(fresh_cache, monkeypatch):
    calls = _fake_query_counter(monkeypatch)

    # DLC excluded by default -> fetched (cached) but filtered out of the result.
    excluded = await client.get_game_details([3], include_dlcs=False)
    assert excluded == []
    assert calls["games"] == 1

    # Same id, include_dlcs=True -> served from cache (no new request), now returned.
    included = await client.get_game_details([3], include_dlcs=True)
    assert [g["name"] for g in included] == ["Some DLC"]
    assert calls["games"] == 1


async def test_cache_persists_to_disk(fresh_cache, monkeypatch, tmp_path: Path):
    _fake_query_counter(monkeypatch)
    await client.get_game_details([1], include_dlcs=False)
    cache_file = tmp_path / "igdb_cache.json"
    assert cache_file.is_file()
    assert '"game:1"' in cache_file.read_text()
