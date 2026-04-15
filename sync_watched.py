#!/usr/bin/env python3
"""
media_status_sync.py

Safe sync flow:
1. Fetch Jellyfin + Radarr/Sonarr items
2. Sync Jellyfin played -> Arr watched tag
3. Sync Arr watched tag -> Jellyfin played
4. Move watched items in Arr from New -> Watched
5. Trigger Jellyfin refresh
6. Re-fetch Jellyfin items
7. Re-apply Arr watched tag -> Jellyfin played as repair
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

MediaKind = Literal["movie", "series"]


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
        "request_retries": int(os.environ.get("REQUEST_RETRIES", "3")),
        "request_retry_delay": int(os.environ.get("REQUEST_RETRY_DELAY", "5")),
        "jellyfin_limit": int(os.environ.get("JELLYFIN_LIMIT", "1000")),
        "jellyfin_path_updates_only": os.environ.get("JELLYFIN_PATH_UPDATES_ONLY", "true").lower() == "true",
    }


def setup_logging(config: dict[str, Any]) -> None:
    logger.remove()
    logger.add(sys.stderr, level=config["log_level"], enqueue=False, backtrace=False, diagnose=False)
    if config["log_file"]:
        logger.add(
            config["log_file"],
            level=config["log_level"],
            rotation="10 MB",
            retention=5,
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )


CONFIG = load_config()
setup_logging(CONFIG)


def api_request(
    url: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | list[Any] | None = None,
    api_key: str | None = None,
    emby_token: str | None = None,
) -> dict[str, Any] | list[Any] | None:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    if emby_token:
        headers["X-Emby-Token"] = emby_token

    body = json.dumps(data).encode("utf-8") if data is not None else None

    for attempt in range(1, CONFIG["request_retries"] + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=CONFIG["request_timeout"]) as resp:
                if resp.status not in (200, 201, 202, 204):
                    logger.error("Unexpected HTTP status {} for {} {}", resp.status, method, url)
                    return None

                raw = resp.read()
                if not raw:
                    return {"status": resp.status}

                try:
                    return json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    return {"status": resp.status, "raw": raw.decode(errors="replace")}

        except urllib.error.HTTPError as exc:
            logger.error("HTTP {} for {} {}", exc.code, method, url)
            try:
                error_body = exc.read().decode(errors="replace")
                if error_body:
                    logger.error("Response body: {}", error_body[:1000])
            except Exception:
                pass
            return None

        except (urllib.error.URLError, socket.timeout) as exc:
            reason = getattr(exc, "reason", exc)
            if attempt < CONFIG["request_retries"]:
                logger.warning(
                    "Attempt {}/{} failed for {} {}: {}",
                    attempt,
                    CONFIG["request_retries"],
                    method,
                    url,
                    reason,
                )
                time.sleep(CONFIG["request_retry_delay"])
            else:
                logger.error("Request failed for {} {}: {}", method, url, reason)
                return None

    return None


@dataclass
class ArrItem:
    arr_id: int
    external_id: str
    title: str
    path: str
    root_folder_path: str
    tags: list[int]


@dataclass
class JellyfinItem:
    jellyfin_id: str
    external_id: str
    title: str
    played: bool


@dataclass
class SyncSpec:
    kind: MediaKind
    arr_name: str
    arr_url: str
    arr_api_key: str
    watched_tag_name: str
    new_root: str
    watched_root: str


def get_jellyfin_user_id() -> str | None:
    url = f"{CONFIG['jellyfin_url']}/Users?api_key={CONFIG['jellyfin_api_key']}"
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])
    if not isinstance(data, list):
        return None

    target = CONFIG["jellyfin_username"].casefold()
    for user in data:
        if str(user.get("Name", "")).casefold() == target:
            user_id = user.get("Id")
            if user_id:
                return str(user_id)
    return None


def get_arr_tag_id(base_url: str, api_key: str, tag_name: str) -> int | None:
    data = api_request(f"{base_url}/api/v3/tag", api_key=api_key)
    if not isinstance(data, list):
        return None

    target = tag_name.casefold()
    for tag in data:
        if str(tag.get("label", "")).casefold() == target:
            tag_id = tag.get("id")
            if tag_id is not None:
                return int(tag_id)
    return None


def get_jellyfin_items(user_id: str, kind: MediaKind) -> dict[str, JellyfinItem]:
    include_type = "Movie" if kind == "movie" else "Series"
    provider_key = "Tmdb" if kind == "movie" else "Tvdb"

    query = urllib.parse.urlencode(
        {
            "Recursive": "true",
            "IncludeItemTypes": include_type,
            "Fields": "ProviderIds,UserData",
            "api_key": CONFIG["jellyfin_api_key"],
            "Limit": CONFIG["jellyfin_limit"],
        }
    )
    url = f"{CONFIG['jellyfin_url']}/Users/{user_id}/Items?{query}"
    data = api_request(url, emby_token=CONFIG["jellyfin_api_key"])

    result: dict[str, JellyfinItem] = {}
    if not isinstance(data, dict):
        return result

    for item in data.get("Items", []):
        provider_ids = item.get("ProviderIds", {})
        external_id = provider_ids.get(provider_key)
        if not external_id:
            continue

        result[str(external_id)] = JellyfinItem(
            jellyfin_id=str(item["Id"]),
            external_id=str(external_id),
            title=str(item.get("Name", "")),
            played=bool(item.get("UserData", {}).get("Played", False)),
        )
    return result


def get_arr_items(spec: SyncSpec) -> dict[str, ArrItem]:
    endpoint = "movie" if spec.kind == "movie" else "series"
    external_key = "tmdbId" if spec.kind == "movie" else "tvdbId"

    data = api_request(f"{spec.arr_url}/api/v3/{endpoint}", api_key=spec.arr_api_key)

    result: dict[str, ArrItem] = {}
    if not isinstance(data, list):
        return result

    for item in data:
        external_id = item.get(external_key)
        if not external_id:
            continue

        result[str(external_id)] = ArrItem(
            arr_id=int(item["id"]),
            external_id=str(external_id),
            title=str(item.get("title", "")),
            path=str(item.get("path", "")),
            root_folder_path=str(item.get("rootFolderPath", "")),
            tags=list(item.get("tags", [])),
        )
    return result


def apply_watched_tag_to_arr(spec: SyncSpec, arr_ids: list[int], watched_tag_id: int, dry_run: bool) -> bool:
    if not arr_ids:
        return True

    if dry_run:
        logger.info("[DRY-RUN] Would tag {} {} items in {}", len(arr_ids), spec.kind, spec.arr_name)
        return True

    endpoint = "movie/editor" if spec.kind == "movie" else "series/editor"
    ids_key = "movieIds" if spec.kind == "movie" else "seriesIds"

    payload = {
        ids_key: arr_ids,
        "tags": [watched_tag_id],
        "applyTags": "add",
    }

    result = api_request(
        f"{spec.arr_url}/api/v3/{endpoint}",
        method="PUT",
        data=payload,
        api_key=spec.arr_api_key,
    )
    return result is not None


def mark_jellyfin_played(user_id: str, items: list[JellyfinItem], dry_run: bool) -> None:
    for item in items:
        if dry_run:
            logger.info("[DRY-RUN] Would mark '{}' as watched in Jellyfin", item.title)
            continue

        url = (
            f"{CONFIG['jellyfin_url']}/Users/{user_id}/PlayedItems/{item.jellyfin_id}"
            f"?api_key={CONFIG['jellyfin_api_key']}"
        )
        result = api_request(url, method="POST", emby_token=CONFIG["jellyfin_api_key"])
        if result is None:
            logger.error("Failed to mark '{}' as watched in Jellyfin", item.title)
        else:
            logger.info("Marked '{}' as watched in Jellyfin", item.title)


def move_arr_items(spec: SyncSpec, arr_ids: list[int], dry_run: bool) -> bool:
    if not arr_ids:
        return True

    if dry_run:
        logger.info("[DRY-RUN] Would move {} {} items to {}", len(arr_ids), spec.kind, spec.watched_root)
        return True

    endpoint = "movie/editor" if spec.kind == "movie" else "series/editor"
    ids_key = "movieIds" if spec.kind == "movie" else "seriesIds"

    payload = {
        ids_key: arr_ids,
        "rootFolderPath": spec.watched_root,
        "moveFiles": True,
    }

    result = api_request(
        f"{spec.arr_url}/api/v3/{endpoint}",
        method="PUT",
        data=payload,
        api_key=spec.arr_api_key,
    )
    return result is not None


def trigger_jellyfin_refresh(dry_run: bool) -> None:
    if dry_run:
        logger.info(
            "[DRY-RUN] Would trigger Jellyfin refresh (PathUpdatesOnly={})",
            CONFIG["jellyfin_path_updates_only"],
        )
        return

    payload = {
        "PathUpdatesOnly": CONFIG["jellyfin_path_updates_only"],
        "MediaTypes": ["Video"],
        "ImageRefreshMode": "Default",
        "MetadataRefreshMode": "Default",
        "ReplaceAllMetadata": False,
        "ReplaceAllImages": False,
    }

    result = api_request(
        f"{CONFIG['jellyfin_url']}/Library/Refresh",
        method="POST",
        data=payload,
        emby_token=CONFIG["jellyfin_api_key"],
    )
    if result is None:
        logger.error("Jellyfin refresh failed")
    else:
        logger.info("Triggered Jellyfin refresh")


def sync_arr_to_jellyfin_played(
    user_id: str,
    jellyfin_items: dict[str, JellyfinItem],
    arr_items: dict[str, ArrItem],
    watched_tag_id: int,
    dry_run: bool,
    spec: SyncSpec,
    phase: str,
) -> None:
    to_mark: list[JellyfinItem] = []

    for external_id, arr_item in arr_items.items():
        jf_item = jellyfin_items.get(external_id)
        if not jf_item:
            continue

        if watched_tag_id in arr_item.tags and not jf_item.played:
            to_mark.append(jf_item)
            logger.info("[{}] Will mark watched in Jellyfin: {}", phase, jf_item.title)

    if to_mark:
        mark_jellyfin_played(user_id, to_mark, dry_run)
        logger.info("[{}] Marked {} {} items as watched in Jellyfin", phase, len(to_mark), spec.kind)
    else:
        logger.info("[{}] No {} items needed marking in Jellyfin", phase, spec.kind)


def sync_media_type(
    spec: SyncSpec,
    user_id: str,
    dry_run: bool,
    skip_move: bool,
    skip_refresh: bool,
) -> None:
    logger.info("{}", "-" * 60)
    logger.info("Processing {} via {}", spec.kind, spec.arr_name)
    logger.info("{}", "-" * 60)

    watched_tag_id = get_arr_tag_id(spec.arr_url, spec.arr_api_key, spec.watched_tag_name)
    if watched_tag_id is None:
        logger.error("Could not resolve {} tag '{}'", spec.arr_name, spec.watched_tag_name)
        return

    jellyfin_items = get_jellyfin_items(user_id, spec.kind)
    arr_items = get_arr_items(spec)

    logger.info("Fetched {} Jellyfin {} items", len(jellyfin_items), spec.kind)
    logger.info("Fetched {} {} items", len(arr_items), spec.arr_name)

    # 1) Jellyfin played -> Arr watched tag
    to_tag: list[int] = []
    for external_id, jf_item in jellyfin_items.items():
        arr_item = arr_items.get(external_id)
        if not arr_item:
            continue

        if jf_item.played and watched_tag_id not in arr_item.tags:
            to_tag.append(arr_item.arr_id)
            logger.info("Will tag in {}: {}", spec.arr_name, arr_item.title)

    if to_tag:
        if apply_watched_tag_to_arr(spec, to_tag, watched_tag_id, dry_run):
            logger.info("Tagged {} {} items in {}", len(to_tag), spec.kind, spec.arr_name)
            tagged_ids = set(to_tag)
            for arr_item in arr_items.values():
                if arr_item.arr_id in tagged_ids and watched_tag_id not in arr_item.tags:
                    arr_item.tags.append(watched_tag_id)
        else:
            logger.error("Failed tagging watched {} in {}", spec.kind, spec.arr_name)
    else:
        logger.info("No {} items needed tagging in {}", spec.kind, spec.arr_name)

    # 2) Arr watched tag -> Jellyfin played
    sync_arr_to_jellyfin_played(
        user_id=user_id,
        jellyfin_items=jellyfin_items,
        arr_items=arr_items,
        watched_tag_id=watched_tag_id,
        dry_run=dry_run,
        spec=spec,
        phase="pre-move",
    )

    # 3) Move watched items in Arr
    moved_any = False
    if skip_move:
        logger.info("Skipping move for {} (--skip-move)", spec.kind)
    else:
        to_move: list[ArrItem] = []
        for arr_item in arr_items.values():
            if watched_tag_id in arr_item.tags and arr_item.path.startswith(spec.new_root):
                to_move.append(arr_item)
                logger.info("Will move: {}", arr_item.title)

        if to_move:
            if move_arr_items(spec, [x.arr_id for x in to_move], dry_run):
                moved_any = True
                logger.info("Moved {} {} items in {}", len(to_move), spec.kind, spec.arr_name)
                if not dry_run:
                    logger.info("Waiting {}s for {} to finish moving...", CONFIG["move_wait_seconds"], spec.arr_name)
                    time.sleep(CONFIG["move_wait_seconds"])
            else:
                logger.error("Failed moving {} items in {}", spec.kind, spec.arr_name)
        else:
            logger.info("No {} items needed moving", spec.kind)

    # 4) Refresh Jellyfin
    if skip_refresh:
        logger.info("Skipping Jellyfin refresh (--skip-refresh)")
    elif moved_any or dry_run:
        trigger_jellyfin_refresh(dry_run=dry_run)
    else:
        logger.info("Skipping Jellyfin refresh because no {} items moved", spec.kind)

    # 5) Re-fetch Jellyfin after refresh and repair watched state
    jellyfin_items_after = get_jellyfin_items(user_id, spec.kind)
    if not jellyfin_items_after:
        logger.warning("Could not re-fetch Jellyfin {} items after refresh", spec.kind)
        return

    sync_arr_to_jellyfin_played(
        user_id=user_id,
        jellyfin_items=jellyfin_items_after,
        arr_items=arr_items,
        watched_tag_id=watched_tag_id,
        dry_run=dry_run,
        spec=spec,
        phase="post-refresh-repair",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync watched state between Jellyfin, Radarr, and Sonarr")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without changing anything")
    parser.add_argument("--skip-move", action="store_true", help="Skip Arr file moves")
    parser.add_argument("--skip-refresh", action="store_true", help="Skip Jellyfin refresh")
    args = parser.parse_args()

    logger.info("{}", "=" * 60)
    logger.info("Media Status Sync")
    logger.info("{}", "=" * 60)

    user_id = get_jellyfin_user_id()
    if not user_id:
        logger.error("Could not resolve Jellyfin user '{}'", CONFIG["jellyfin_username"])
        sys.exit(1)

    movie_spec = SyncSpec(
        kind="movie",
        arr_name="Radarr",
        arr_url=CONFIG["radarr_url"],
        arr_api_key=CONFIG["radarr_api_key"],
        watched_tag_name=CONFIG["radarr_watched_tag"],
        new_root=CONFIG["movie_new_root_folder"],
        watched_root=CONFIG["movie_watched_root_folder"],
    )

    sync_media_type(
        spec=movie_spec,
        user_id=user_id,
        dry_run=args.dry_run,
        skip_move=args.skip_move,
        skip_refresh=args.skip_refresh,
    )

    if CONFIG["sonarr_url"] and CONFIG["sonarr_api_key"]:
        series_spec = SyncSpec(
            kind="series",
            arr_name="Sonarr",
            arr_url=CONFIG["sonarr_url"],
            arr_api_key=CONFIG["sonarr_api_key"],
            watched_tag_name=CONFIG["sonarr_watched_tag"],
            new_root=CONFIG["series_new_root_folder"],
            watched_root=CONFIG["series_watched_root_folder"],
        )

        sync_media_type(
            spec=series_spec,
            user_id=user_id,
            dry_run=args.dry_run,
            skip_move=args.skip_move,
            skip_refresh=args.skip_refresh,
        )
    else:
        logger.info("Sonarr not configured, skipping series sync")

    logger.info("{}", "=" * 60)
    logger.info("Done")
    logger.info("{}", "=" * 60)


if __name__ == "__main__":
    main()