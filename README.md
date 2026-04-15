# Jellyfin Media Status Sync

Sync watched state between __Jellyfin__, __Radarr__, and __Sonarr__, and automatically move watched movies and series from a `New` folder to a `Watched` folder.

## Overview
This project keeps Jellyfin, Radarr, and Sonarr aligned for watched media.

It can:
1. read watched movies from Jellyfin and apply the watched tag in Radarr
2. read watched series from Jellyfin and apply the watched tag in Sonarr
3. mark Jellyfin movies as watched when Radarr already has the watched tag
4. mark Jellyfin series as watched when Sonarr already has the watched tag
5. move watched movies from the new folder to the watched folder via Radarr
6. move watched series from the new folder to the watched folder via Sonarr
7. update Jellyfin paths for moved movies and series
8. trigger a Jellyfin path-only refresh

## Project files
- `sync_watched.py`
- `.env.example`
- `requirements.txt`
- `.gitignore`
- `tests/`
- `pytest.ini`
- `Dockerfile`
- `docker-compose.yml`
- `docker/entrypoint.sh`

## Setup
```bash
cd /home/amc/code/projects/sync-watched-project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env` with your real values.

## Configuration
Example values are provided in `.env.example`.

Main settings:
- `RADARR_URL`
- `RADARR_API_KEY`
- `SONARR_URL` optional, enables Sonarr series sync
- `SONARR_API_KEY` optional, required when `SONARR_URL` is set
- `JELLYFIN_URL`
- `JELLYFIN_API_KEY`
- `JELLYFIN_USERNAME`
- `RADARR_WATCHED_TAG` defaults to `watched`
- `SONARR_WATCHED_TAG` defaults to `watched`
- `MOVIE_NEW_ROOT_FOLDER`
- `MOVIE_WATCHED_ROOT_FOLDER`
- `SERIES_NEW_ROOT_FOLDER`
- `SERIES_WATCHED_ROOT_FOLDER`
- `LOG_LEVEL`
- `LOG_FILE`
- `REQUEST_TIMEOUT`
- `MOVE_WAIT_SECONDS`

Docker scheduler settings:
- `TZ` for container timezone, for example `Europe/Berlin`
- `CRON_SCHEDULE` for when the script should run, for example `0 */6 * * *`
- `RUN_ON_START` set to `true` if you want one immediate run when the container starts

The script validates required environment variables at startup and exits clearly if any are missing. It also exits if it cannot resolve the Jellyfin username or the watched tag names in Radarr or Sonarr.

## Important behavior notes

### Auto-resolution of IDs
The script does not require you to look up internal IDs from the web UI. It resolves the following automatically at startup:
- Jellyfin user ID from `JELLYFIN_USERNAME`
- Radarr watched tag ID from `RADARR_WATCHED_TAG`
- Sonarr watched tag ID from `SONARR_WATCHED_TAG`

### Folder variables
Movies and series have separate root folder variables:

Movie folders:
- `MOVIE_NEW_ROOT_FOLDER`
- `MOVIE_WATCHED_ROOT_FOLDER`

Series folders:
- `SERIES_NEW_ROOT_FOLDER`
- `SERIES_WATCHED_ROOT_FOLDER`

## Preconditions
Before running the script:
- the source and target movie folders must already exist
- those movie folders must already be configured in Radarr as valid root folders
- if Sonarr is used, your series folders must already exist and be configured there too
- Jellyfin must already be configured to see those paths correctly
- the media should already be managed in Radarr and/or Sonarr and visible in Jellyfin
- the watched tag names you configure must already exist in Radarr and Sonarr

This project assumes the media server and folder structure already exist. It does not create or configure them for you.

## Usage
Dry run:
```bash
python3 sync_watched.py --dry-run
```

Normal run:
```bash
python3 sync_watched.py
```

Optional flags:
```bash
python3 sync_watched.py --skip-move
python3 sync_watched.py --skip-scan
```

## Docker usage
Build and start the scheduled container:
```bash
docker compose up -d --build
```

View logs:
```bash
docker compose logs -f
```

Stop it:
```bash
docker compose down
```

Run once immediately on startup:
```bash
RUN_ON_START=true docker compose up -d --build
```

Example cron schedules:
- every 6 hours: `0 */6 * * *`
- every day at 03:00: `0 3 * * *`
- every day at 03:00 and 15:00: `0 3,15 * * *`

The container stays alive by running cron in the foreground and executes `sync_watched.py` on the schedule you define.

## Logging
The script uses **Loguru** for structured logging.

Control logging with `.env`:
- `LOG_LEVEL=INFO`
- `LOG_FILE=` to disable file logging, or set a file path to enable rotating file logs

If `LOG_FILE` is set, logs are also written to that file with rotation.

## Error handling
The script:
- validates required environment variables at startup
- resolves Jellyfin user ID from username
- resolves Radarr and Sonarr watched tag IDs from tag names
- handles HTTP, URL, and timeout failures cleanly
- exits early if the initial Jellyfin or Radarr fetch fails
- logs failures in a consistent and readable way

## Tests
The project has a pytest suite covering all sync, tagging, move, and path-update logic for both movies and series.

Run the tests:
```bash
python3 -m pytest tests/
```

