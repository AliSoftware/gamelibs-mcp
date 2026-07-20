"""Offline tests for Epic local state: Legendary/Heroic + official launcher manifests."""

from __future__ import annotations

import shutil
from pathlib import Path

from gamelibs_mcp.epic import local

FIXTURES = Path(__file__).parent / "fixtures"
MANIFESTS = FIXTURES / "manifests"


def _make_legendary_dir(tmp_path: Path) -> Path:
    d = tmp_path / "legendary"
    d.mkdir()
    shutil.copy(FIXTURES / "user.json", d / "user.json")
    shutil.copy(FIXTURES / "installed.json", d / "installed.json")
    return d


def _disable_official(monkeypatch, tmp_path: Path) -> None:
    """Point official-manifest discovery at an empty dir so a real local Epic launcher
    on the dev machine can't leak into these tests."""
    monkeypatch.setattr(local.config, "epic_manifests_override", lambda: str(tmp_path / "no_egl"))


def test_read_legendary_user(tmp_path: Path, monkeypatch):
    d = _make_legendary_dir(tmp_path)
    monkeypatch.setattr(local.config, "legendary_config_override", lambda: str(d))
    monkeypatch.setattr(local.config, "heroic_config_override", lambda: None)

    user = local.read_legendary_user()
    assert user is not None
    assert user["access_token"] == "fake-access-token"
    assert user["displayName"] == "TestPlayer"


def test_installed_summary_legendary_only(tmp_path: Path, monkeypatch):
    d = _make_legendary_dir(tmp_path)
    monkeypatch.setattr(local.config, "legendary_config_override", lambda: str(d))
    monkeypatch.setattr(local.config, "heroic_config_override", lambda: None)
    _disable_official(monkeypatch, tmp_path)

    summary = local.installed_summary()
    assert set(summary) == {"Fortnite", "Rocket League"}
    assert summary["Fortnite"]["install_path"] == "C:\\Games\\Fortnite"
    assert summary["Fortnite"]["source"] == "legendary/heroic"
    assert summary["Rocket League"]["install_size"] == 20000000000


def test_no_local_state(tmp_path: Path, monkeypatch):
    empty = tmp_path / "nope"
    empty.mkdir()
    monkeypatch.setattr(local.config, "legendary_config_override", lambda: str(empty))
    monkeypatch.setattr(local.config, "heroic_config_override", lambda: None)
    _disable_official(monkeypatch, tmp_path)
    assert local.read_legendary_user() is None
    assert local.installed_summary() == {}


def test_official_launcher_manifests(monkeypatch):
    monkeypatch.setattr(local.config, "epic_manifests_override", lambda: str(MANIFESTS))
    official = local.read_official_installed()
    # Fully-installed game is present; the incomplete-install one is excluded.
    assert set(official) == {"Fortnite"}
    assert official["Fortnite"]["install_path"] == "C:\\Program Files\\Epic Games\\Fortnite"
    assert official["Fortnite"]["install_size"] == 45000000000
    assert official["Fortnite"]["version"] == "27.10"
    assert official["Fortnite"]["source"] == "epic-launcher"


def test_official_launcher_wins_over_legendary(tmp_path: Path, monkeypatch):
    d = _make_legendary_dir(tmp_path)  # legendary has Fortnite (C:\Games\Fortnite) + Rocket League
    monkeypatch.setattr(local.config, "legendary_config_override", lambda: str(d))
    monkeypatch.setattr(local.config, "heroic_config_override", lambda: None)
    monkeypatch.setattr(local.config, "epic_manifests_override", lambda: str(MANIFESTS))

    summary = local.installed_summary()
    # Union of both sources; Rocket League only exists in Legendary.
    assert set(summary) == {"Fortnite", "Rocket League"}
    # On conflict, the official launcher's data wins.
    assert summary["Fortnite"]["source"] == "epic-launcher"
    assert summary["Fortnite"]["install_path"] == "C:\\Program Files\\Epic Games\\Fortnite"
    assert summary["Rocket League"]["source"] == "legendary/heroic"
