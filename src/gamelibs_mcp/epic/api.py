"""Epic library + catalog API (reverse-engineered, modeled on Legendary).

Ownership endpoints return only identifiers; human titles and "game vs DLC" categories come
from the catalog service, which we resolve per item and cache on disk (titles/categories are
effectively immutable). Bounded concurrency keeps the first (cache-warming) pass polite.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .. import config
from ..http import ApiError, make_client, request_with_retry
from ..util import TTLCache
from . import local
from .auth import USER_AGENT, get_access_token

ASSETS_URL = (
    "https://launcher-public-service-prod06.ol.epicgames.com"
    "/launcher/api/public/assets/Windows?label=Live"
)
LIBRARY_ITEMS_URL = (
    "https://library-service.live.use1a.on.epicgames.com"
    "/library/api/public/items?includeMetadata=true"
)
CATALOG_HOST = "https://catalog-public-service-prod06.ol.epicgames.com"

_CATALOG_CACHE_FILE = "epic_catalog_cache.json"
_MAX_CONCURRENCY = 8

# Raw owned-records list (identifiers only) — cheap to hold, cached briefly.
_owned_cache = TTLCache(ttl_seconds=300.0)
_catalog_mem: dict[str, dict] | None = None


# --- low-level HTTP --------------------------------------------------------

async def _authed_get(url: str) -> dict | list:
    token = await get_access_token()
    headers = {"User-Agent": USER_AGENT, "Authorization": f"bearer {token}"}
    async with make_client(headers=headers) as client:
        resp = await request_with_retry(client, "GET", url)
    if resp.status_code >= 400:
        raise ApiError(f"Epic GET {url} failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


# --- owned records ---------------------------------------------------------

async def _fetch_launcher_assets() -> list[dict]:
    data = await _authed_get(ASSETS_URL)
    return data if isinstance(data, list) else []


async def _fetch_library_items() -> list[dict]:
    records: list[dict] = []
    url = LIBRARY_ITEMS_URL
    token = await get_access_token()
    headers = {"User-Agent": USER_AGENT, "Authorization": f"bearer {token}"}
    async with make_client(headers=headers) as client:
        while True:
            resp = await request_with_retry(client, "GET", url)
            if resp.status_code >= 400:
                raise ApiError(f"Epic library items failed ({resp.status_code}): {resp.text[:200]}")
            data = resp.json()
            records.extend(data.get("records", []))
            cursor = (data.get("responseMetadata") or {}).get("nextCursor")
            if not cursor:
                break
            url = f"{LIBRARY_ITEMS_URL}&cursor={cursor}"
    return records


async def fetch_owned_records(force_refresh: bool = False) -> list[dict]:
    """Union of launcher assets + library-service items, de-duplicated by catalogItemId.

    Each record: ``{app_name, namespace, catalog_item_id}``. Titles/categories are resolved
    separately via the catalog service.
    """
    if not force_refresh:
        cached = _owned_cache.get()
        if cached is not None:
            return cached

    assets, items = await asyncio.gather(_fetch_launcher_assets(), _fetch_library_items())

    by_id: dict[str, dict] = {}
    for a in assets:
        cid = a.get("catalogItemId")
        if cid:
            by_id[cid] = {
                "app_name": a.get("appName"),
                "namespace": a.get("namespace"),
                "catalog_item_id": cid,
            }
    for r in items:
        if r.get("sandboxType") == "PRIVATE":
            continue
        cid = r.get("catalogItemId")
        if cid and cid not in by_id:
            by_id[cid] = {
                "app_name": r.get("appName"),
                "namespace": r.get("namespace"),
                "catalog_item_id": cid,
            }

    records = list(by_id.values())
    _owned_cache.set(records)
    return records


# --- catalog metadata (with disk cache) ------------------------------------

def _catalog_cache_path() -> Path:
    return config.config_dir() / _CATALOG_CACHE_FILE


def _load_catalog_cache() -> dict[str, dict]:
    global _catalog_mem
    if _catalog_mem is None:
        path = _catalog_cache_path()
        if path.is_file():
            try:
                _catalog_mem = json.loads(path.read_text())
            except (OSError, ValueError):
                _catalog_mem = {}
        else:
            _catalog_mem = {}
    return _catalog_mem


def _save_catalog_cache() -> None:
    if _catalog_mem is not None:
        _catalog_cache_path().write_text(json.dumps(_catalog_mem))


def _catalog_key(namespace: str, catalog_item_id: str) -> str:
    return f"{namespace}:{catalog_item_id}"


def clear_cache() -> None:
    """Drop Epic's owned-records (in-memory) and catalog-metadata (disk) caches.

    Auth tokens are left intact — this refreshes *data*, not the login session.
    """
    global _catalog_mem
    _owned_cache.clear()
    _catalog_mem = None
    _catalog_cache_path().unlink(missing_ok=True)


def _custom_attr(item: dict, key: str) -> str | None:
    """Read a catalog customAttribute value, tolerating both known encodings.

    Epic returns customAttributes as a dict ``{key: {"type", "value"}}`` (current API), but
    older/other responses use a list ``[{"key", "value"}]``. Handle either, ignore neither.
    """
    ca = item.get("customAttributes")
    if isinstance(ca, dict):
        entry = ca.get(key)
        if isinstance(entry, dict):
            return entry.get("value")
        return entry if isinstance(entry, str) else None
    if isinstance(ca, list):
        for a in ca:
            if isinstance(a, dict) and a.get("key") == key:
                return a.get("value")
    return None


def _shape_catalog_item(item: dict) -> dict:
    categories = [
        c.get("path")
        for c in item.get("categories", [])
        if isinstance(c, dict) and c.get("path")
    ]
    is_game = "games" in categories
    is_dlc = bool(item.get("mainGameItem")) or any(
        c in ("addons", "digitalextras") for c in categories
    )
    return {
        "title": item.get("title"),
        "description": item.get("description"),
        "long_description": item.get("longDescription") or _custom_attr(item, "longDescription"),
        "developer": item.get("developer") or _custom_attr(item, "developerName"),
        "categories": categories,
        "is_game": is_game,
        "is_dlc": is_dlc,
    }


async def _fetch_catalog_item(namespace: str, catalog_item_id: str) -> dict:
    url = (
        f"{CATALOG_HOST}/catalog/api/shared/namespace/{namespace}/bulk/items"
        f"?id={catalog_item_id}&includeDLCDetails=true&includeMainGameDetails=true"
        f"&country=US&locale=en"
    )
    data = await _authed_get(url)
    item = data.get(catalog_item_id) if isinstance(data, dict) else None
    return _shape_catalog_item(item) if isinstance(item, dict) else {}


async def resolve_metadata(records: list[dict]) -> dict[str, dict]:
    """Resolve catalog metadata for the given records, keyed by catalog_item_id.

    Uses the persistent disk cache; only cache-misses hit the network (bounded concurrency).
    """
    cache = _load_catalog_cache()
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    result: dict[str, dict] = {}
    misses: list[dict] = []

    for rec in records:
        key = _catalog_key(rec["namespace"], rec["catalog_item_id"])
        if key in cache:
            result[rec["catalog_item_id"]] = cache[key]
        else:
            misses.append(rec)

    async def _one(rec: dict) -> None:
        async with sem:
            try:
                meta = await _fetch_catalog_item(rec["namespace"], rec["catalog_item_id"])
            except Exception:
                # This is a reverse-engineered API over a large, varied library: one item
                # with an unexpected shape or a transient failure must not abort the whole
                # listing. Degrade to empty metadata (the record is then kept as "unknown").
                meta = {}
        cache[_catalog_key(rec["namespace"], rec["catalog_item_id"])] = meta
        result[rec["catalog_item_id"]] = meta

    if misses:
        await asyncio.gather(*(_one(r) for r in misses))
        _save_catalog_cache()
    return result


# --- public: listing & details --------------------------------------------

def _keep_record(meta: dict, include_dlcs: bool) -> bool:
    # Unknown metadata (empty) is kept — better to over-include than silently hide.
    if not meta:
        return True
    if meta.get("is_dlc") and not include_dlcs:
        return False
    # If we positively know it's not a game (e.g. an application/asset), drop it.
    if meta.get("categories") and not meta.get("is_game") and not meta.get("is_dlc"):
        return False
    return True


async def list_games(
    installed_only: bool, search: str | None, include_dlcs: bool
) -> list[dict]:
    """Return thin owned-game records (title/ids/installed), filtered but *unpaginated*.

    The caller paginates. We resolve catalog metadata for the whole library here because
    both DLC filtering and title search need it — the disk cache makes repeat calls cheap.
    """
    records = await fetch_owned_records()
    meta = await resolve_metadata(records)
    installed = local.installed_summary()

    needle = search.lower().strip() if search else None
    out: list[dict] = []
    for rec in records:
        m = meta.get(rec["catalog_item_id"], {})
        if not _keep_record(m, include_dlcs):
            continue
        app_name = rec["app_name"]
        inst = installed.get(app_name)
        if installed_only and not inst:
            continue
        title = m.get("title") or (inst or {}).get("title") or app_name
        if needle and needle not in (title or "").lower():
            continue
        out.append(
            {
                "app_name": app_name,
                "catalog_item_id": rec["catalog_item_id"],
                "namespace": rec["namespace"],
                "title": title,
                "installed": bool(inst),
            }
        )
    out.sort(key=lambda g: (g["title"] or "").lower())
    return out


async def get_game_details(app_names: list[str], include_dlcs: bool) -> list[dict]:
    records = await fetch_owned_records()
    by_app = {r["app_name"]: r for r in records}
    installed = local.installed_summary()

    wanted = [by_app[a] for a in app_names if a in by_app]
    meta = await resolve_metadata(wanted)

    out: list[dict] = []
    for app_name in app_names:
        rec = by_app.get(app_name)
        if rec is None:
            out.append({"app_name": app_name, "error": "not found in Epic library"})
            continue
        m = meta.get(rec["catalog_item_id"], {})
        if m.get("is_dlc") and not include_dlcs:
            continue
        inst = installed.get(app_name) or {}
        out.append(
            {
                "app_name": app_name,
                "catalog_item_id": rec["catalog_item_id"],
                "namespace": rec["namespace"],
                "title": m.get("title") or inst.get("title") or app_name,
                "description": m.get("description"),
                "long_description": m.get("long_description"),
                "developer": m.get("developer"),
                "categories": m.get("categories"),
                "is_dlc": m.get("is_dlc", False),
                "installed": bool(inst),
                "install_path": inst.get("install_path"),
                "install_size": inst.get("install_size"),
                "version": inst.get("version"),
            }
        )
    return out
