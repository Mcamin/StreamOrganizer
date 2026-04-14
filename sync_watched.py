#!/usr/bin/env python3
"""
sync-watched.py — Sync watched status between Jellyfin and Radarr, then move files.

Flow:
  1. Query Jellyfin for watched movies from Jellyfin
  2. Tag matching movies as watched in Radarr
  3. Mark Jellyfin items as watched when Radarr already has the watched tag
  4. Move watched movies from the New root folder to the Watched root folder
  5. Update Jellyfin paths for moved items
  6. Trigger a Jellyfin path-only refresh

Usage:
  python3 sync-watched.py [--dry-run] [--skip-move] [--skip-scan]
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


def load_config() -> dict[str, Any]:
    return {
        "radarr_url": require_env("RADARR_URL"),
        "radarr_api_key": require_env("RADARR_API_KEY"),
        "jellyfin_url": require_env("JELLYFIN_URL"),
        "jellyfin_api_key": require_env("JELLYFIN_API_KEY"),
        "jellyfin_user_id": require_env("JELLYFIN_USER_ID"),
        "watched_tag_id": int(os.environ.get("WATCHED_TAG_ID", "1")),
        "new_root_folder": os.environ.get("NEW_ROOT_FOLDER", "/movies/New"),
        "watched_root_folder": os.environ.get("WATCHED_ROOT_FOLDER", "/movies/Watched"),
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


def get_jellyfin_watched() -> dict[str, str] | None:
    url = (
        f"{CONFIG['jellyfin_url']}/Users/{CONFIG['jellyfin_user_id']}/Items"
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


def tag_radarr_movies(movie_ids: list[int], dry_run: bool = False) -> bool:
    if not movie_ids:
        return True

    if dry_run:
        logger.info("[DRY-RUN] Would tag {} movies as watched in Radarr", len(movie_ids))
        return True

    url = f"{CONFIG['radarr_url']}/api/v3/movie/editor"
    payload = {
        "movieIds": movie_ids,
        "tags": [CONFIG["watched_tag_id"]],
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
        logger.info("[DRY-RUN] Would move {} movies to {}", len(movie_ids), CONFIG["watched_root_folder"])
        return []

    url = f"{CONFIG['radarr_url']}/api/v3/movie/editor"
    payload = {
        "movieIds": movie_ids,
        "rootFolderPath": CONFIG["watched_root_folder"],
        "moveFiles": True,
    }
    result = api_request(url, method="PUT", data=payload, api_key=CONFIG["radarr_api_key"])
    if result is None:
        logger.error("Failed to move movies in Radarr")
        return []
    return movie_ids


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


def update_jellyfin_paths(moved_items: list[int], dry_run: bool = False) -> None:
    jellyfin_map = fetch_jellyfin_movie_map()
    if jellyfin_map is None:
        logger.error("Could not fetch Jellyfin items for path updates")
        return

    for movie_id in moved_items:
        tmdb = str(movie_id)
        if tmdb not in jellyfin_map or tmdb not in radarr_movies_cache:
            continue

        old_path = radarr_movies_cache[tmdb]["path"]
        new_path = old_path.replace(CONFIG["new_root_folder"], CONFIG["watched_root_folder"])
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


def mark_jellyfin_unwatched_to_watched(radarr_watched_tmdb: set[str], dry_run: bool = False) -> None:
    url = (
        f"{CONFIG['jellyfin_url']}/Items?Recursive=true&IncludeItemTypes=Movie"
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
            f"{CONFIG['jellyfin_url']}/Users/{CONFIG['jellyfin_user_id']}/PlayedItems/{item_id}"
            f"?api_key={CONFIG['jellyfin_api_key']}"
        )
        result = api_request(mark_url, method="POST", emby_token=CONFIG["jellyfin_api_key"])
        if result is None:
            logger.error("Failed to mark '{}' as watched in Jellyfin", name)
        else:
            logger.info("Marked '{}' as watched in Jellyfin", name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync watched status between Jellyfin and Radarr")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    parser.add_argument("--skip-move", action="store_true", help="Skip moving files (only tag)")
    parser.add_argument("--skip-scan", action="store_true", help="Skip Jellyfin library scan")
    args = parser.parse_args()

    logger.info("{}", "=" * 60)
    logger.info("Watched Sync: Jellyfin <-> Radarr")
    logger.info("{}", "=" * 60)

    global radarr_movies_cache

    logger.info("[1/6] Fetching watched movies from Jellyfin...")
    jf_watched = get_jellyfin_watched()
    if jf_watched is None:
        logger.error("Failed to fetch watched movies from Jellyfin")
        sys.exit(1)
    logger.info("Found {} watched movies in Jellyfin", len(jf_watched))

    logger.info("[2/6] Fetching movies from Radarr...")
    radarr_movies_cache = get_radarr_movies()
    if radarr_movies_cache is None:
        logger.error("Failed to fetch movies from Radarr")
        sys.exit(1)
    logger.info("Found {} movies in Radarr", len(radarr_movies_cache))

    logger.info("[3/6] Syncing watched tag to Radarr...")
    to_tag: list[int] = []
    for tmdb in jf_watched:
        if tmdb in radarr_movies_cache:
            movie = radarr_movies_cache[tmdb]
            if CONFIG["watched_tag_id"] not in movie["tags"]:
                to_tag.append(movie["id"])
                logger.info("Tag needed: {}", movie["title"])

    if to_tag:
        if tag_radarr_movies(to_tag, dry_run=args.dry_run):
            logger.info("Tagged {} movies as watched in Radarr", len(to_tag))
    else:
        logger.info("All watched movies already tagged in Radarr")

    logger.info("[3b/6] Checking Radarr watched -> Jellyfin...")
    radarr_watched_tmdb = {
        tmdb for tmdb, movie in radarr_movies_cache.items() if CONFIG["watched_tag_id"] in movie["tags"]
    }
    mark_jellyfin_unwatched_to_watched(radarr_watched_tmdb, dry_run=args.dry_run)

    logger.info("[4/6] Moving watched movies to Watched folder...")
    if args.skip_move:
        logger.info("Skipped (--skip-move)")
    else:
        to_move: list[int] = []
        for tmdb in radarr_watched_tmdb:
            if tmdb in radarr_movies_cache:
                movie = radarr_movies_cache[tmdb]
                if movie["path"].startswith(CONFIG["new_root_folder"]):
                    to_move.append(movie["id"])
                    logger.info("Move needed: {}", movie["title"])

        if to_move:
            moved = move_radarr_movies(to_move, dry_run=args.dry_run)
            if moved:
                logger.info("Moved {} movies to {}", len(moved), CONFIG["watched_root_folder"])
                logger.info("Waiting {}s for Radarr to process moves...", CONFIG["move_wait_seconds"])
                time.sleep(CONFIG["move_wait_seconds"])

                logger.info("[4b/6] Updating Jellyfin paths for moved items...")
                update_jellyfin_paths(moved, dry_run=args.dry_run)

                logger.info("[4c/6] Triggering Jellyfin path-only refresh...")
                trigger_path_refresh(dry_run=args.dry_run)
        else:
            logger.info("All watched movies already in Watched folder")

    logger.info("[5/6] Triggering Jellyfin library scan...")
    if args.skip_scan:
        logger.info("Skipped (--skip-scan). Relying on Radarr -> Jellyfin sync for played status.")
    elif args.dry_run:
        logger.info("[DRY-RUN] Would skip Jellyfin library scan (due to potential unmark issue).")
    else:
        logger.info("Skipping Jellyfin library scan (due to potential unmark issue).")

    logger.info("[6/6] Final verification: Jellyfin watched -> Radarr tag...")
    mark_jellyfin_unwatched_to_watched(set(jf_watched.keys()), dry_run=args.dry_run)

    logger.info("{}", "=" * 60)
    logger.info("Done")
    logger.info("{}", "=" * 60)


if __name__ == "__main__":
    main()
