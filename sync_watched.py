#!/usr/bin/env python3
"""
media-status-sync.py — Sync watched status between Jellyfin, Radarr, and Sonarr.

Flow:  1. Query Jellyfin for watched movies and tag them in Radarr
  2. Mark Jellyfin movies as watched when Radarr already has the watched tag
  3. Query Jellyfin for watched series and tag them in Sonarr
  4. Mark Jellyfin series as watched when Sonarr already has the watched tag
  5. Move watched movies from the new root folder to the watched root folder
  6. Update Jellyfin paths for moved movies
  7. Move watched series from the new root folder to the watched root folder
  8. Update Jellyfin paths for moved series
  9. Trigger a Jellyfin path-only refresh

Usage:
  python3 sync_watched.py [--dry-run] [--skip-move] [--skip-scan]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv
from loguru import logger

load_dotenv()


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def normalize_url(url: str) -> str:
    return url.rstrip("/")


def load_config() -> dict[str, Any]:
    return {
        "radarr_url": normalize_url(require_env("RADARR_URL")),
        "radarr_api_key": require_env("RADARR_API_KEY"),

        "sonarr_url": normalize_url(os.environ.get("SONARR_URL", "").strip()),
        "sonarr_api_key": os.environ.get("SONARR_API_KEY", "").strip(),

        "jellyfin_url": normalize_url(require_env("JELLYFIN_URL")),
        "jellyfin_api_key": require_env("JELLYFIN_API_KEY"),
        "jellyfin_username": require_env("JELLYFIN_USERNAME"),

        "radarr_watched_tag": os.environ.get("RADARR_WATCHED_TAG", "watched").strip() or "watched",
        "sonarr_watched_tag": os.environ.get("SONARR_WATCHED_TAG", "watched").strip() or "watched",

        "movie_new_root_folder": os.environ.get("MOVIE_NEW_ROOT_FOLDER", "/movies/New"),
        "movie_watched_root_folder": os.environ.get("MOVIE_WATCHED_ROOT_FOLDER", "/movies/Watched"),

        "series_new_root_folder": os.environ.get("SERIES_NEW_ROOT_FOLDER", "/series/New"),
        "series_watched_root_folder": os.environ.get("SERIES_WATCHED_ROOT_FOLDER", "/series/Watched"),

        "log_level": os.environ.get("LOG_LEVEL", "INFO"),
        "log_file": os.environ.get("LOG_FILE", "").strip(),

        "request_timeout": int(os.environ.get("REQUEST_TIMEOUT", "30")),
        "move_wait_seconds": int(os.environ.get("MOVE_WAIT_SECONDS", "10")),
    }


def setup_logging(config: dict[str, Any]) -> None:
    logger.remove()
    logger.add(sys.stderr, level=config["log_level"], enqueue=False, backtrace=False, diagnose=False)

    log_file = config["log_file"]
    if log_file:
        logger.add(
            log_file,
            level=config["log_level"],
            rotation="10 MB",
            retention=5,
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )


CONFIG = load_config()
setup_logging(CONFIG)

radarr_movies_cache: dict[str, dict[str, Any]] = {}
sonarr_series_cache: dict[str, dict[str, Any]] = {}


def api_request(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    api_key: str | None = None,
    emby_token: str | None = None,
) -> dict[str, Any] | list[Any] | None:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    if emby_token:
        headers["X-Emby-Token"] = emby_token

    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=CONFIG["request_timeout"]) as resp:
            if resp.status not in (200, 204):
                logger.error("Unexpected HTTP status {} for {} {}", resp.status, method, url)
                return None

            raw = resp.read()
            if not raw:
                return {"status": resp.status}

            try:
                return json.loads(raw.decode())
            except json.JSONDecodeError:
                logger.warning("Non-JSON response for {} {}", method, url)
                return {"status": resp.status, "raw": raw.decode(errors="replace")}

    except urllib.error.HTTPError as exc:
        logger.error("HTTP {} for {} {}", exc.code, method, url)
        try:
            error_body = exc.read().decode(errors="replace")
            if error_body:
                logger.error("Response body: {}", error_body[:500])
        except Exception:
            pass
        return None
    except urllib.error.URLError as exc:
        logger.error("Request failed for {} {}: {}", method, url, exc.reason)
        return None
    except socket.timeout:
        logger.error("Request timed out for {} {}", method, url)
        return None


def get_jellyfin_user_id() -> str | None:
    url = f"{CONFIG['jellyfin_url']}/Users?api_key={CONFIG['jellyfin_api_key']}"
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])
    if not isinstance(data, list):
        return None

    target = CONFIG["jellyfin_username"].casefold()
    for user in data:
        name = str(user.get("Name", "")).casefold()
        if name == target:
            user_id = user.get("Id")
            if user_id:
                return str(user_id)
    return None


def get_arr_tag_id(base_url: str, api_key: str, tag_name: str) -> int | None:
    url = f"{base_url}/api/v3/tag"
    data = api_request(url, api_key=api_key)
    if not isinstance(data, list):
        return None

    target = tag_name.casefold()
    for tag in data:
        label = str(tag.get("label", "")).casefold()
        if label == target:
            tag_id = tag.get("id")
            if tag_id is not None:
                return int(tag_id)
    return None


def get_jellyfin_watched_movies(jellyfin_user_id: str) -> dict[str, str] | None:
    url = (
        f"{CONFIG['jellyfin_url']}/Users/{jellyfin_user_id}/Items"
        f"?Filters=IsPlayed&IncludeItemTypes=Movie&Recursive=true"
        f"&Fields=ProviderIds&api_key={CONFIG['jellyfin_api_key']}&Limit=500"
    )
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])
    if not isinstance(data, dict):
        return None

    result: dict[str, str] = {}
    for item in data.get("Items", []):
        providers = item.get("ProviderIds", {})
        tmdb = providers.get("Tmdb")
        if tmdb:
            result[str(tmdb)] = item["Name"]
    return result

def get_jellyfin_watched_series(jellyfin_user_id: str) -> dict[str, str] | None:
    url = (
        f"{CONFIG['jellyfin_url']}/Users/{jellyfin_user_id}/Items"
        f"?Filters=IsPlayed&IncludeItemTypes=Series&Recursive=true"
        f"&Fields=ProviderIds&api_key={CONFIG['jellyfin_api_key']}&Limit=500"
    )
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])
    if not isinstance(data, dict):
        return None

    result: dict[str, str] = {}
    for item in data.get("Items", []):
        providers = item.get("ProviderIds", {})
        tvdb = providers.get("Tvdb")
        if tvdb:
            result[str(tvdb)] = item["Name"]
    return result



def get_radarr_movies() -> dict[str, dict[str, Any]] | None:
    url = f"{CONFIG['radarr_url']}/api/v3/movie"
    data = api_request(url, api_key=CONFIG["radarr_api_key"])
    if not isinstance(data, list):
        return None

    result: dict[str, dict[str, Any]] = {}
    for movie in data:
        result[str(movie["tmdbId"])] = {
            "id": movie["id"],
            "title": movie["title"],
            "path": movie["path"],
            "rootFolderPath": movie.get("rootFolderPath", ""),
            "tags": movie.get("tags", []),
            "qualityProfileId": movie.get("qualityProfileId", 1),
            "monitored": movie.get("monitored", True),
        }
    return result


def get_sonarr_series() -> dict[str, dict[str, Any]] | None:
    url = f"{CONFIG['sonarr_url']}/api/v3/series"
    data = api_request(url, api_key=CONFIG["sonarr_api_key"])
    if not isinstance(data, list):
        return None

    result: dict[str, dict[str, Any]] = {}
    for series in data:
        tvdb_id = series.get("tvdbId")
        if not tvdb_id:
            continue

        result[str(tvdb_id)] = {
            "id": series["id"],
            "title": series["title"],
            "path": series.get("path", ""),
            "rootFolderPath": series.get("rootFolderPath", ""),
            "tags": series.get("tags", []),
        }
    return result


def tag_radarr_movies(movie_ids: list[int], watched_tag_id: int, dry_run: bool = False) -> bool:
    if not movie_ids:
        return True

    if dry_run:
        logger.info("[DRY-RUN] Would tag {} movies as watched in Radarr", len(movie_ids))
        return True

    url = f"{CONFIG['radarr_url']}/api/v3/movie/editor"
    payload = {
        "movieIds": movie_ids,
        "tags": [watched_tag_id],
        "applyTags": "add",
    }
    result = api_request(url, method="PUT", data=payload, api_key=CONFIG["radarr_api_key"])
    if result is None:
        logger.error("Failed to tag movies in Radarr")
        return False
    return True


def move_radarr_movies(movie_ids: list[int], dry_run: bool = False) -> list[int]:
    if not movie_ids:
        return []

    if dry_run:
        logger.info("[DRY-RUN] Would move {} movies to {}", len(movie_ids), CONFIG["movie_watched_root_folder"])
        return []

    url = f"{CONFIG['radarr_url']}/api/v3/movie/editor"
    payload = {
        "movieIds": movie_ids,
        "rootFolderPath": CONFIG["movie_watched_root_folder"],
        "moveFiles": True,
    }
    result = api_request(url, method="PUT", data=payload, api_key=CONFIG["radarr_api_key"])
    if result is None:
        logger.error("Failed to move movies in Radarr")
        return []
    return movie_ids



def tag_sonarr_series(series_ids: list[int], watched_tag_id: int, dry_run: bool = False) -> bool:
    if not series_ids:
        return True

    if dry_run:
        logger.info("[DRY-RUN] Would tag {} series as watched in Sonarr", len(series_ids))
        return True

    url = f"{CONFIG['sonarr_url']}/api/v3/series/editor"
    payload = {
        "seriesIds": series_ids,
        "tags": [watched_tag_id],
        "applyTags": "add",
    }
    result = api_request(url, method="PUT", data=payload, api_key=CONFIG["sonarr_api_key"])
    if result is None:
        logger.error("Failed to tag series in Sonarr")
        return False
    return True


def move_sonarr_series(series_ids: list[int], dry_run: bool = False) -> list[int]:
    if not series_ids:
        return []

    if dry_run:
        logger.info("[DRY-RUN] Would move {} series to {}", len(series_ids), CONFIG["series_watched_root_folder"])
        return []

    url = f"{CONFIG['sonarr_url']}/api/v3/series/editor"
    payload = {
        "seriesIds": series_ids,
        "rootFolderPath": CONFIG["series_watched_root_folder"],
        "moveFiles": True,
    }
    result = api_request(url, method="PUT", data=payload, api_key=CONFIG["sonarr_api_key"])
    if result is None:
        logger.error("Failed to move series in Sonarr")
        return []
    return series_ids


def fetch_jellyfin_series_map() -> dict[str, str] | None:
    url = (
        f"{CONFIG['jellyfin_url']}/Items?Recursive=true&IncludeItemTypes=Series"
        f"&Fields=ProviderIds&api_key={CONFIG['jellyfin_api_key']}&Limit=500"
    )
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])
    if not isinstance(data, dict):
        return None

    result: dict[str, str] = {}
    for item in data.get("Items", []):
        providers = item.get("ProviderIds", {})
        tvdb = providers.get("Tvdb")
        if tvdb:
            result[str(tvdb)] = item["Id"]
    return result


def update_jellyfin_series_paths(moved_tvdb_ids: list[str], dry_run: bool = False) -> None:
    jellyfin_map = fetch_jellyfin_series_map()
    if jellyfin_map is None:
        logger.error("Could not fetch Jellyfin series for path updates")
        return

    for tvdb in moved_tvdb_ids:
        if tvdb not in jellyfin_map or tvdb not in sonarr_series_cache:
            continue

        old_path = sonarr_series_cache[tvdb]["path"]
        new_path = old_path.replace(CONFIG["series_new_root_folder"], CONFIG["series_watched_root_folder"])
        if old_path == new_path:
            continue

        if dry_run:
            logger.info("[DRY-RUN] Would update Jellyfin series path: {} -> {}", old_path, new_path)
            continue

        url = f"{CONFIG['jellyfin_url']}/Items/{jellyfin_map[tvdb]}"
        payload = {"Path": new_path}
        result = api_request(url, method="POST", data=payload, api_key=CONFIG["jellyfin_api_key"])
        if result is None:
            logger.error("Failed to update Jellyfin path for {}", sonarr_series_cache[tvdb]["title"])
        else:
            logger.info("Updated Jellyfin series path for {}", sonarr_series_cache[tvdb]["title"])


def fetch_jellyfin_movie_map() -> dict[str, str] | None:
    url = (
        f"{CONFIG['jellyfin_url']}/Items?Recursive=true&IncludeItemTypes=Movie"
        f"&Fields=ProviderIds&api_key={CONFIG['jellyfin_api_key']}&Limit=500"
    )
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])
    if not isinstance(data, dict):
        return None

    result: dict[str, str] = {}
    for item in data.get("Items", []):
        providers = item.get("ProviderIds", {})
        tmdb = providers.get("Tmdb")
        if tmdb:
            result[str(tmdb)] = item["Id"]
    return result


def update_jellyfin_movies_paths(moved_items: list[int], dry_run: bool = False) -> None:
    jellyfin_map = fetch_jellyfin_movie_map()
    if jellyfin_map is None:
        logger.error("Could not fetch Jellyfin items for path updates")
        return

    for movie_id in moved_items:
        tmdb = str(movie_id)
        if tmdb not in jellyfin_map or tmdb not in radarr_movies_cache:
            continue

        old_path = radarr_movies_cache[tmdb]["path"]
        new_path = old_path.replace(CONFIG["movie_new_root_folder"], CONFIG["movie_watched_root_folder"])
        if old_path == new_path:
            continue

        if dry_run:
            logger.info("[DRY-RUN] Would update Jellyfin path: {} -> {}", old_path, new_path)
            continue

        url = f"{CONFIG['jellyfin_url']}/Items/{jellyfin_map[tmdb]}"
        payload = {"Path": new_path}
        result = api_request(url, method="POST", data=payload, api_key=CONFIG["jellyfin_api_key"])
        if result is None:
            logger.error("Failed to update Jellyfin path for {}", radarr_movies_cache[tmdb]["title"])
        else:
            logger.info("Updated Jellyfin path for {}", radarr_movies_cache[tmdb]["title"])


def trigger_path_refresh(dry_run: bool = False) -> None:
    if dry_run:
        logger.info("[DRY-RUN] Would trigger path-only refresh")
        return

    url = f"{CONFIG['jellyfin_url']}/Library/Refresh"
    payload = {
        "PathUpdatesOnly": True,
        "MediaTypes": ["Video"],
        "ImageRefreshMode": "Default",
        "MetadataRefreshMode": "Default",
    }
    result = api_request(url, method="POST", data=payload, emby_token=CONFIG["jellyfin_api_key"])
    if result is None:
        logger.error("Jellyfin path-only refresh failed")
    else:
        logger.info("Jellyfin path-only refresh triggered")


def mark_jellyfin_movies_unwatched_to_watched(radarr_watched_tmdb: set[str], jellyfin_user_id: str, dry_run: bool = False) -> None:
    url = (
        f"{CONFIG['jellyfin_url']}/Users/{jellyfin_user_id}/Items?Recursive=true&IncludeItemTypes=Movie"
        f"&Fields=ProviderIds,UserData&api_key={CONFIG['jellyfin_api_key']}&Limit=500"
    )
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])
    if not isinstance(data, dict):
        logger.error("Could not fetch Jellyfin items for watched sync")
        return

    to_mark: list[tuple[str, str]] = []
    for item in data.get("Items", []):
        providers = item.get("ProviderIds", {})
        tmdb = providers.get("Tmdb")
        played = item.get("UserData", {}).get("Played", False)
        if tmdb and str(tmdb) in radarr_watched_tmdb and not played:
            to_mark.append((item["Id"], item["Name"]))

    if not to_mark:
        logger.info("All Jellyfin movies already synced, nothing to mark")
        return

    for item_id, name in to_mark:
        if dry_run:
            logger.info("[DRY-RUN] Would mark '{}' as watched in Jellyfin", name)
            continue

        mark_url = (
            f"{CONFIG['jellyfin_url']}/Users/{jellyfin_user_id}/PlayedItems/{item_id}"
            f"?api_key={CONFIG['jellyfin_api_key']}"
        )
        result = api_request(mark_url, method="POST", emby_token=CONFIG["jellyfin_api_key"])
        if result is None:
            logger.error("Failed to mark '{}' as watched in Jellyfin", name)
        else:
            logger.info("Marked '{}' as watched in Jellyfin", name)


def mark_jellyfin_series_unwatched_to_watched(
    sonarr_watched_tvdb: set[str], jellyfin_user_id: str, dry_run: bool = False
) -> None:
    url = (
        f"{CONFIG['jellyfin_url']}/Users/{jellyfin_user_id}/Items?Recursive=true&IncludeItemTypes=Series"
        f"&Fields=ProviderIds,UserData&api_key={CONFIG['jellyfin_api_key']}&Limit=500"
    )
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])
    if not isinstance(data, dict):
        logger.error("Could not fetch Jellyfin series for watched sync")
        return

    to_mark: list[tuple[str, str]] = []
    for item in data.get("Items", []):
        providers = item.get("ProviderIds", {})
        tvdb = providers.get("Tvdb")
        played = item.get("UserData", {}).get("Played", False)
        if tvdb and str(tvdb) in sonarr_watched_tvdb and not played:
            to_mark.append((item["Id"], item["Name"]))

    if not to_mark:
        logger.info("All Jellyfin series already synced, nothing to mark")
        return

    for item_id, name in to_mark:
        if dry_run:
            logger.info("[DRY-RUN] Would mark '{}' as watched in Jellyfin", name)
            continue

        mark_url = (
            f"{CONFIG['jellyfin_url']}/Users/{jellyfin_user_id}/PlayedItems/{item_id}"
            f"?api_key={CONFIG['jellyfin_api_key']}"
        )
        result = api_request(mark_url, method="POST", emby_token=CONFIG["jellyfin_api_key"])
        if result is None:
            logger.error("Failed to mark series '{}' as watched in Jellyfin", name)
        else:
            logger.info("Marked series '{}' as watched in Jellyfin", name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync watched status between Jellyfin, Radarr, and Sonarr")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    parser.add_argument("--skip-move", action="store_true", help="Skip moving files (only tag)")
    parser.add_argument("--skip-scan", action="store_true", help="Skip Jellyfin library scan")
    args = parser.parse_args()

    logger.info("{}", "=" * 60)
    logger.info("Media Status Sync: Jellyfin <-> Radarr / Sonarr if configured)")
    logger.info("{}", "=" * 60)

    global radarr_movies_cache, sonarr_series_cache

    jellyfin_user_id = get_jellyfin_user_id()
    if not jellyfin_user_id:
        logger.error("Could not resolve Jellyfin user '{}'", CONFIG["jellyfin_username"])
        sys.exit(1)

    radarr_watched_tag_id = get_arr_tag_id(
        CONFIG["radarr_url"], CONFIG["radarr_api_key"], CONFIG["radarr_watched_tag"]
    )
    if radarr_watched_tag_id is None:
        logger.error("Could not resolve Radarr tag '{}'", CONFIG["radarr_watched_tag"])
        sys.exit(1)

    sonarr_watched_tag_id: int | None = None
    if CONFIG["sonarr_url"] and CONFIG["sonarr_api_key"]:
        sonarr_watched_tag_id = get_arr_tag_id(
            CONFIG["sonarr_url"], CONFIG["sonarr_api_key"], CONFIG["sonarr_watched_tag"]
        )
        if sonarr_watched_tag_id is None:
            logger.error("Could not resolve Sonarr tag '{}'", CONFIG["sonarr_watched_tag"])
            sys.exit(1)

    logger.info("[1/7] Fetching watched movies from Jellyfin...")
    jf_watched = get_jellyfin_watched_movies(jellyfin_user_id)
    if jf_watched is None:
        logger.error("Failed to fetch watched movies from Jellyfin")
        sys.exit(1)
    logger.info("Found {} watched movies in Jellyfin", len(jf_watched))

    logger.info("[2/7] Fetching movies from Radarr...")
    radarr_movies_cache = get_radarr_movies()
    if radarr_movies_cache is None:
        logger.error("Failed to fetch movies from Radarr")
        sys.exit(1)
    logger.info("Found {} movies in Radarr", len(radarr_movies_cache))

    logger.info("[3/7] Syncing watched tag to Radarr...")
    to_tag: list[int] = []
    for tmdb in jf_watched:
        if tmdb in radarr_movies_cache:
            movie = radarr_movies_cache[tmdb]
            if radarr_watched_tag_id not in movie["tags"]:
                to_tag.append(movie["id"])
                logger.info("Tag needed: {}", movie["title"])

    if to_tag:
        if tag_radarr_movies(to_tag, radarr_watched_tag_id, dry_run=args.dry_run):
            logger.info("Tagged {} movies as watched in Radarr", len(to_tag))
    else:
        logger.info("All watched movies already tagged in Radarr")

    logger.info("[4/7] Checking Radarr watched -> Jellyfin...")
    radarr_watched_tmdb = {
        tmdb for tmdb, movie in radarr_movies_cache.items() if radarr_watched_tag_id in movie["tags"]
    }
    mark_jellyfin_movies_unwatched_to_watched(radarr_watched_tmdb, jellyfin_user_id, dry_run=args.dry_run)

    logger.info("[4/9] Sonarr sync...")
    if CONFIG["sonarr_url"] and CONFIG["sonarr_api_key"] and sonarr_watched_tag_id is not None:
        logger.info("[4a/9] Fetching watched series from Jellyfin...")
        jf_watched_series = get_jellyfin_watched_series(jellyfin_user_id)
        if jf_watched_series is None:
            logger.error("Failed to fetch watched series from Jellyfin")
            jf_watched_series = {}
        else:
            logger.info("Found {} watched series in Jellyfin", len(jf_watched_series))

        logger.info("[4b/9] Fetching series from Sonarr...")
        sonarr_series_cache = get_sonarr_series()
        if sonarr_series_cache is None:
            logger.error("Failed to fetch series from Sonarr")
            sonarr_series_cache = {}
        else:
            logger.info("Found {} series in Sonarr", len(sonarr_series_cache))

            logger.info("[4c/9] Syncing watched tag to Sonarr...")
            to_tag_series: list[int] = []
            for tvdb in jf_watched_series:
                if tvdb in sonarr_series_cache:
                    series = sonarr_series_cache[tvdb]
                    if sonarr_watched_tag_id not in series["tags"]:
                        to_tag_series.append(series["id"])
                        logger.info("Tag needed: {}", series["title"])

            if to_tag_series:
                if tag_sonarr_series(to_tag_series, sonarr_watched_tag_id, dry_run=args.dry_run):
                    logger.info("Tagged {} series as watched in Sonarr", len(to_tag_series))
            else:
                logger.info("All watched series already tagged in Sonarr")

            logger.info("[4d/9] Checking Sonarr watched -> Jellyfin series...")
            sonarr_watched_tvdb = {
                tvdb for tvdb, series in sonarr_series_cache.items()
                if sonarr_watched_tag_id in series["tags"]
            }
            logger.info("Found {} Sonarr series tagged as watched", len(sonarr_watched_tvdb))
            mark_jellyfin_series_unwatched_to_watched(
                sonarr_watched_tvdb, jellyfin_user_id, dry_run=args.dry_run
            )
    else:
        logger.info("Skipped, Sonarr sync is not configured")
        sonarr_watched_tvdb = set()

    logger.info("[5/9] Moving watched movies to watched folder...")
    if args.skip_move:
        logger.info("Skipped (--skip-move)")
    else:
        to_move: list[int] = []
        for tmdb in radarr_watched_tmdb:
            if tmdb in radarr_movies_cache:
                movie = radarr_movies_cache[tmdb]
                if movie["path"].startswith(CONFIG["movie_new_root_folder"]):
                    to_move.append(movie["id"])
                    logger.info("Move needed: {}", movie["title"])

        if to_move:
            moved = move_radarr_movies(to_move, dry_run=args.dry_run)
            if moved:
                logger.info("Moved {} movies to {}", len(moved), CONFIG["movie_watched_root_folder"])
                logger.info("Waiting {}s for Radarr to process moves...", CONFIG["move_wait_seconds"])
                time.sleep(CONFIG["move_wait_seconds"])

                logger.info("[5b/9] Updating Jellyfin paths for moved movies...")
                update_jellyfin_movies_paths(moved, dry_run=args.dry_run)
        else:
            logger.info("All watched movies already in watched folder")

    logger.info("[6/9] Moving watched series to watched folder...")
    if args.skip_move:
        logger.info("Skipped (--skip-move)")
    elif not (CONFIG["sonarr_url"] and CONFIG["sonarr_api_key"]):
        logger.info("Skipped, Sonarr sync is not configured")
    else:
        to_move_ids: list[int] = []
        to_move_tvdb: list[str] = []
        for tvdb in sonarr_watched_tvdb:
            if tvdb in sonarr_series_cache:
                series = sonarr_series_cache[tvdb]
                if series["path"].startswith(CONFIG["series_new_root_folder"]):
                    to_move_ids.append(series["id"])
                    to_move_tvdb.append(tvdb)
                    logger.info("Move needed: {}", series["title"])

        if to_move_ids:
            moved_series = move_sonarr_series(to_move_ids, dry_run=args.dry_run)
            if moved_series:
                logger.info("Moved {} series to {}", len(moved_series), CONFIG["series_watched_root_folder"])
                logger.info("Waiting {}s for Sonarr to process moves...", CONFIG["move_wait_seconds"])
                time.sleep(CONFIG["move_wait_seconds"])

                logger.info("[6b/9] Updating Jellyfin paths for moved series...")
                update_jellyfin_series_paths(to_move_tvdb, dry_run=args.dry_run)
        else:
            logger.info("All watched series already in watched folder")

    logger.info("[7/9] Triggering Jellyfin path-only refresh...")
    if args.skip_move:
        logger.info("Skipped (--skip-move)")
    else:
        trigger_path_refresh(dry_run=args.dry_run)

    logger.info("[8/9] Triggering Jellyfin library scan...")
    if args.skip_scan:
        logger.info("Skipped (--skip-scan). Relying on Radarr -> Jellyfin sync for played status.")
    elif args.dry_run:
        logger.info("[DRY-RUN] Would skip Jellyfin library scan (due to potential unmark issue).")
    else:
        logger.info("Skipping Jellyfin library scan (due to potential unmark issue).")

    logger.info("[9/9] Final verification: Jellyfin watched -> Radarr tag...")
    mark_jellyfin_movies_unwatched_to_watched(set(jf_watched.keys()), jellyfin_user_id, dry_run=args.dry_run)

    logger.info("{}", "=" * 60)
    logger.info("Done")
    logger.info("{}", "=" * 60)


if __name__ == "__main__":
    main()
