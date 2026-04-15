# Changelog

## [Released]

### Added
- Sonarr series file move: watched series are now moved from the new root folder to the watched root folder via the Sonarr editor API, mirroring the existing Radarr movie move behavior
- Jellyfin → Sonarr tag sync: series watched in Jellyfin are now tagged as watched in Sonarr automatically
- `get_jellyfin_watched_series` — fetches watched series from Jellyfin by TVDB ID
- `tag_sonarr_series` — bulk-tags series in Sonarr via `/api/v3/series/editor`
- `move_sonarr_series` — moves series to the watched root folder via Sonarr editor API
- `fetch_jellyfin_series_map` — maps TVDB IDs to Jellyfin item IDs for series
- `update_jellyfin_series_paths` — updates Jellyfin paths for series after Sonarr moves them
- `path` and `rootFolderPath` fields captured in `get_sonarr_series` (required for move detection)
- Tests for all new Sonarr move and path-update functions

## [0.5.0]

### Added
- Sonarr watched-tag to Jellyfin series played-state sync: series tagged as watched in Sonarr are now marked as played in Jellyfin

## [0.4.0]

### Added
- Docker support with cron-based scheduled execution
- `Dockerfile`, `docker-compose.yml`, and `docker/entrypoint.sh`
- `TZ`, `CRON_SCHEDULE`, and `RUN_ON_START` environment variables for container scheduling

## [0.3.0]

### Added
- pytest suite with high coverage
- Tests for all config, API, sync, move, and path-update logic

## [0.2.0]

### Added
- Professional structured logging via Loguru (`LOG_LEVEL`, `LOG_FILE`)
- Config validation at startup with clear error messages
- Automatic resolution of Jellyfin user ID from `JELLYFIN_USERNAME`
- Automatic resolution of Radarr and Sonarr watched tag IDs from tag names — no more manual ID lookups
- Separate movie and series root folder variables (`MOVIE_NEW_ROOT_FOLDER`, `MOVIE_WATCHED_ROOT_FOLDER`, `SERIES_NEW_ROOT_FOLDER`, `SERIES_WATCHED_ROOT_FOLDER`)
- `.env.example` with all supported variables documented
- `requirements.txt`
- `.gitignore`

### Changed
- All configuration moved from hardcoded values into environment variables
- Main script renamed to `sync_watched.py`

## [0.1.0]

### Added
- Initial project setup under standalone project folder
- README