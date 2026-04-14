# Sync Watched

Sync watched movie state between Jellyfin and Radarr, and automatically move watched movies from a `New` root folder to a `Watched` root folder.

## Overview
This project keeps Jellyfin and Radarr aligned for watched movies.

It can:
1. read watched movies from Jellyfin
2. apply a watched tag in Radarr
3. mark Jellyfin items as watched when Radarr already has the watched tag
4. move watched movies from one root folder to another in Radarr
5. update Jellyfin paths after the move
6. trigger a Jellyfin path-only refresh

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
- `JELLYFIN_URL`
- `JELLYFIN_API_KEY`
- `JELLYFIN_USER_ID`
- `WATCHED_TAG_ID`
- `NEW_ROOT_FOLDER`
- `WATCHED_ROOT_FOLDER`
- `LOG_LEVEL`
- `LOG_FILE`
- `REQUEST_TIMEOUT`
- `MOVE_WAIT_SECONDS`

Docker scheduler settings:
- `TZ` for container timezone, for example `Europe/Berlin`
- `CRON_SCHEDULE` for when the script should run, for example `0 */6 * * *`
- `RUN_ON_START` set to `true` if you want one immediate run when the container starts

The script validates required environment variables at startup and exits clearly if any are missing.

## Preconditions
Before running the script:
- the source and target movie folders must already exist
- those folders must already be configured in Radarr as valid root folders
- Jellyfin must already be configured to see those paths correctly
- the movies should already be managed in Radarr and visible in Jellyfin

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
- handles HTTP, URL, and timeout failures more cleanly
- exits early if the initial Jellyfin or Radarr fetch fails
- logs failures in a more professional and consistent way

## Tests
The project has a pytest suite with strong coverage.

Current status:
- `46` tests passing
- `99%` coverage

## Security note
Do not commit your real `.env` file.
Keep credentials out of source control.
Avoid putting real API keys directly into `docker-compose.yml`.

## Current status
Implemented:
- env-based configuration
- config validation
- structured logging
- cleaned error handling
- automated tests
- high coverage test suite
- Dockerized scheduled runtime

Still missing:
- final publish-safe polish pass
