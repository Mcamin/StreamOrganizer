# Changelog

## [0.6.0]

### Added

* Unified sync flow for movies and series using shared logic
* Post-refresh **Jellyfin state repair** to restore watched status after file moves
* Retry logic for API requests (improves reliability during startup/network issues)
* Configurable Jellyfin refresh behavior (path-only vs full)

### Changed

* Refactored sync workflow:

  * Jellyfin → Arr (apply watched tags)
  * Arr → Jellyfin (pre-move sync)
  * Move via Arr
  * Jellyfin refresh
  * Post-refresh repair (Arr → Jellyfin)
* Improved logging with clearer phase separation
* Simplified architecture using shared sync pipeline for Radarr and Sonarr

### Removed

* Direct Jellyfin path updates (`POST /Items/{id}`)

  * Replaced with refresh + repair strategy
  * Avoids API instability and HTTP 400 errors

---

## [0.5.0]

### Added

* Sonarr watched-tag to Jellyfin series played-state sync
  (series tagged as watched in Sonarr are now marked as played in Jellyfin)

---

## [0.4.0]

### Added

* Docker support with cron-based scheduled execution
* `Dockerfile`, `docker-compose.yml`, and `docker/entrypoint.sh`
* `TZ`, `CRON_SCHEDULE`, and `RUN_ON_START` environment variables

---

## [0.3.0]

### Added

* pytest suite with high coverage
* Tests for config, API, sync, and move logic

---

## [0.2.0]

### Added

* Structured logging via Loguru (`LOG_LEVEL`, `LOG_FILE`)
* Config validation with clear error messages
* Automatic Jellyfin user ID resolution from username
* Automatic watched tag resolution in Radarr and Sonarr
* Separate movie and series root folder configuration
* `.env.example`
* `requirements.txt`
* `.gitignore`

### Changed

* All configuration moved to environment variables
* Main script renamed to `sync_watched.py`

---

## [0.1.0]

### Added

* Initial project setup
* Basic Jellyfin ↔ Radarr sync
* README
