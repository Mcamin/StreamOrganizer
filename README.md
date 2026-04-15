# 🎬 Jellyfin Media Status Sync

Synchronize watched status between Jellyfin, Radarr, and Sonarr, and automatically organize media by moving watched content from a `New` folder to a `Watched` folder.

---

## 📖 Overview

This project ensures consistent watched state across your media stack and automates file organization.

### Core workflow

For both **movies** and **series**, the script:

1. Syncs **Jellyfin → Arr** (apply watched tags)
2. Syncs **Arr → Jellyfin** (restore watched state if missing)
3. Moves watched media from `New` → `Watched`
4. Triggers a Jellyfin refresh (optional)
5. Performs a **post-refresh repair** to restore watched state

---

## ✨ Key Features

* 🔄 **Bidirectional sync** between Jellyfin and Radarr/Sonarr
* 📂 **Automatic file organization** via Arr (no direct file manipulation)
* 🛡️ **Safe refresh handling** with post-refresh state repair
* 🔁 **Retry logic** for API/network resilience
* ⚙️ **Fully configurable via `.env`**
* 🧪 **Tested with pytest**
* 🐳 **Docker-ready with cron scheduling**

---

## 📁 Project Structure

```
sync_watched.py
.env.example
requirements.txt
tests/
docker/
  entrypoint.sh
Dockerfile
docker-compose.yml
pytest.ini
```

---

## ⚙️ Setup

```bash
cd /home/amc/code/projects/sync-watched-project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env` with your actual values.

---

## 🔧 Configuration

### Required

* `RADARR_URL`
* `RADARR_API_KEY`
* `JELLYFIN_URL`
* `JELLYFIN_API_KEY`
* `JELLYFIN_USERNAME`

### Optional (enables series sync)

* `SONARR_URL`
* `SONARR_API_KEY`

### Tags

* `RADARR_WATCHED_TAG` (default: `watched`)
* `SONARR_WATCHED_TAG` (default: `watched`)

### Folder configuration

Movies:

* `MOVIE_NEW_ROOT_FOLDER`
* `MOVIE_WATCHED_ROOT_FOLDER`

Series:

* `SERIES_NEW_ROOT_FOLDER`
* `SERIES_WATCHED_ROOT_FOLDER`

### Runtime

* `LOG_LEVEL`
* `LOG_FILE`
* `REQUEST_TIMEOUT`
* `MOVE_WAIT_SECONDS`

### Docker / Scheduler

* `TZ` (e.g. `Europe/Berlin`)
* `CRON_SCHEDULE` (e.g. `0 */6 * * *`)
* `RUN_ON_START` (`true` / `false`)

---

## 🧠 Important Behavior

### 🔁 State synchronization model

This project uses **Arr (Radarr/Sonarr) as the source of truth** for watched state.

After a Jellyfin refresh:

* Jellyfin may lose watched state for moved files
* The script **automatically restores it from Arr tags**

---

### ❗ No direct Jellyfin path updates

The script **does not modify Jellyfin item paths directly**.

Why:

* Jellyfin API path updates are unreliable (`POST /Items/{id}`)
* Can lead to errors or inconsistent state

Instead:

* Files are moved by Radarr/Sonarr
* Jellyfin detects changes via refresh
* State is repaired afterward

---

### 🔍 Automatic ID resolution

No manual lookup required:

* Jellyfin user ID resolved from username
* Tag IDs resolved from tag names in Arr

---

## 📌 Preconditions

Before running:

* Folders must exist (`New`, `Watched`)
* Must be configured in Radarr/Sonarr
* Jellyfin must already see these paths
* Media must already be imported in Arr
* Watched tags must exist in Arr

---

## 🚀 Usage

### Dry run

```bash
python3 sync_watched.py --dry-run
```

### Normal run

```bash
python3 sync_watched.py
```

### Options

```bash
--skip-move     # do not move files
--skip-refresh  # skip Jellyfin refresh
```

---

## 🐳 Docker Usage

### Start

```bash
docker compose up -d --build
```

### Logs

```bash
docker compose logs -f
```

### Stop

```bash
docker compose down
```

### Run once on startup

```bash
RUN_ON_START=true docker compose up -d --build
```

---

### ⏱️ Cron Examples

| Schedule       | Expression     |
| -------------- | -------------- |
| Every 6 hours  | `0 */6 * * *`  |
| Daily at 03:00 | `0 3 * * *`    |
| Twice daily    | `0 3,15 * * *` |

---

## 📝 Logging

Uses **Loguru**.

Configure via `.env`:

```bash
LOG_LEVEL=INFO
LOG_FILE=/logs/sync.log
```

* Console logging always enabled
* File logging optional with rotation

---

## 🛠️ Error Handling

The script:

* validates environment variables at startup
* retries API calls (network/DNS safe)
* logs all failures clearly
* exits early on critical failures
* skips optional components safely (e.g. Sonarr)

---

## 🧪 Tests

Run:

```bash
pytest -q
```

With coverage:

```bash
pytest --cov=sync_watched
```

Coverage includes:

* API layer
* sync logic
* move operations
* retry handling
* edge cases

---

## 🧩 Design Philosophy

* **Arr manages files**
* **Jellyfin reflects state**
* **Script ensures consistency**

This avoids fragile direct manipulation of Jellyfin internals and keeps the system resilient.

---

## 📌 Summary

This tool provides a **safe, automated, and consistent workflow** for:

* syncing watched state
* organizing media
* maintaining clean library structure

while avoiding common pitfalls in Jellyfin’s API behavior.

