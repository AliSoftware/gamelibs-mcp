"""Offline tests for pure helpers: pagination, timestamps, IGDB shaping, Epic filtering."""

from __future__ import annotations

from gamelibs_mcp.epic import api as epic_api
from gamelibs_mcp.igdb import client as igdb
from gamelibs_mcp.util import epoch_to_iso, paginate


def test_paginate_envelope():
    items = list(range(10))
    page = paginate(items, limit=3, offset=6)
    assert page["total"] == 10
    assert page["offset"] == 6
    assert page["count"] == 3
    assert page["games"] == [6, 7, 8]


def test_paginate_clamps_limit():
    page = paginate(list(range(1000)), limit=10_000, offset=0)
    assert page["limit"] == 500
    assert page["count"] == 500


def test_epoch_to_iso():
    assert epoch_to_iso(None) is None
    assert epoch_to_iso(0) is None
    assert epoch_to_iso(1596087856).startswith("2020-")


def test_igdb_dlc_clause():
    assert igdb._dlc_clause(include_dlcs=True) == ""
    clause = igdb._dlc_clause(include_dlcs=False)
    assert "category" in clause and "null" in clause


def test_igdb_detail_dict_splits_companies():
    raw = {
        "id": 1,
        "name": "Test Game",
        "summary": "A game.",
        "first_release_date": 1596087856,
        "genres": [{"name": "Adventure"}],
        "involved_companies": [
            {"developer": True, "publisher": False, "company": {"name": "DevCo"}},
            {"developer": False, "publisher": True, "company": {"name": "PubCo"}},
        ],
        "similar_games": [{"name": "Other Game"}],
    }
    d = igdb._detail_dict(raw)
    assert d["developers"] == ["DevCo"]
    assert d["publishers"] == ["PubCo"]
    assert d["genres"] == ["Adventure"]
    assert d["release_year"] == 2020
    assert d["similar_games"] == ["Other Game"]
    # No image fields should ever be present.
    assert "cover" not in d and "screenshots" not in d


def test_epic_keep_record_filters_dlc():
    dlc = {"is_dlc": True, "is_game": False, "categories": ["addons"]}
    game = {"is_dlc": False, "is_game": True, "categories": ["games"]}
    application = {"is_dlc": False, "is_game": False, "categories": ["applications"]}

    assert epic_api._keep_record(game, include_dlcs=False) is True
    assert epic_api._keep_record(dlc, include_dlcs=False) is False
    assert epic_api._keep_record(dlc, include_dlcs=True) is True
    # Non-game, non-dlc (e.g. an application) is dropped.
    assert epic_api._keep_record(application, include_dlcs=False) is False
    # Unknown metadata is kept rather than silently hidden.
    assert epic_api._keep_record({}, include_dlcs=False) is True


def test_epic_shape_catalog_item_list_customattributes():
    item = {
        "title": "Cool Game",
        "description": "Short desc.",
        "categories": [{"path": "games"}, {"path": "games/edition"}],
        "customAttributes": [{"key": "developerName", "value": "Studio X"}],
    }
    shaped = epic_api._shape_catalog_item(item)
    assert shaped["title"] == "Cool Game"
    assert shaped["is_game"] is True
    assert shaped["is_dlc"] is False
    assert shaped["developer"] == "Studio X"


def test_epic_shape_catalog_item_dict_customattributes():
    # The real Epic API returns customAttributes as a dict — this is the shape that
    # previously crashed with "'str' object has no attribute 'get'".
    item = {
        "title": "Real Game",
        "categories": [{"path": "games"}],
        "customAttributes": {
            "developerName": {"type": "STRING", "value": "Studio Y"},
            "longDescription": {"type": "STRING", "value": "A long description."},
        },
    }
    shaped = epic_api._shape_catalog_item(item)
    assert shaped["developer"] == "Studio Y"
    assert shaped["long_description"] == "A long description."
    assert shaped["is_game"] is True


def test_epic_shape_catalog_item_survives_odd_shapes():
    # Missing/None customAttributes and non-dict category entries must not raise.
    item = {"title": "Sparse", "categories": ["oops", {"path": "addons"}]}
    shaped = epic_api._shape_catalog_item(item)
    assert shaped["title"] == "Sparse"
    assert shaped["developer"] is None
    assert shaped["is_dlc"] is True  # addons category
