import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def module(monkeypatch):
    for key in [
        "RADARR_URL",
        "RADARR_API_KEY",
        "SONARR_URL",
        "SONARR_API_KEY",
        "SONARR_WATCHED_TAG",
        "JELLYFIN_URL",
        "JELLYFIN_API_KEY",
        "JELLYFIN_USERNAME",
        "RADARR_WATCHED_TAG",
        "MOVIE_NEW_ROOT_FOLDER",
        "MOVIE_WATCHED_ROOT_FOLDER",
        "SERIES_NEW_ROOT_FOLDER",
        "SERIES_WATCHED_ROOT_FOLDER",
        "LOG_LEVEL",
        "LOG_FILE",
        "REQUEST_TIMEOUT",
        "REQUEST_RETRIES",
        "REQUEST_RETRY_DELAY",
        "JELLYFIN_LIMIT",
        "JELLYFIN_PATH_UPDATES_ONLY",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("RADARR_URL", "https://radarr.example.com")
    monkeypatch.setenv("RADARR_API_KEY", "radarr-key")
    monkeypatch.setenv("JELLYFIN_URL", "https://jellyfin.example.com")
    monkeypatch.setenv("JELLYFIN_API_KEY", "jellyfin-key")
    monkeypatch.setenv("JELLYFIN_USERNAME", "testuser")
    monkeypatch.setenv("RADARR_WATCHED_TAG", "watched")
    monkeypatch.setenv("MOVIE_NEW_ROOT_FOLDER", "/movies/New")
    monkeypatch.setenv("MOVIE_WATCHED_ROOT_FOLDER", "/movies/Watched")
    monkeypatch.setenv("SERIES_NEW_ROOT_FOLDER", "/series/New")
    monkeypatch.setenv("SERIES_WATCHED_ROOT_FOLDER", "/series/Watched")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("REQUEST_TIMEOUT", "30")
    monkeypatch.setenv("REQUEST_RETRIES", "2")
    monkeypatch.setenv("REQUEST_RETRY_DELAY", "0")
    monkeypatch.setenv("JELLYFIN_LIMIT", "1000")
    monkeypatch.setenv("JELLYFIN_PATH_UPDATES_ONLY", "true")

    sys.modules.pop("sync_watched", None)
    import sync_watched as mod
    return mod

def test_require_env_missing(monkeypatch, capsys):
    monkeypatch.setenv("RADARR_URL", "x")
    monkeypatch.setenv("RADARR_API_KEY", "x")
    monkeypatch.setenv("JELLYFIN_URL", "x")
    monkeypatch.setenv("JELLYFIN_API_KEY", "x")
    monkeypatch.setenv("JELLYFIN_USERNAME", "x")

    sys.modules.pop("sync_watched", None)
    import sync_watched as mod

    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(SystemExit):
        mod.require_env("MISSING_VAR")

    captured = capsys.readouterr()
    assert "Missing required environment variable" in captured.err


def test_load_config(module):
    cfg = module.load_config()
    assert cfg["radarr_url"] == "https://radarr.example.com"
    assert cfg["radarr_api_key"] == "radarr-key"
    assert cfg["jellyfin_url"] == "https://jellyfin.example.com"
    assert cfg["jellyfin_username"] == "testuser"
    assert cfg["radarr_watched_tag"] == "watched"
    assert cfg["movie_new_root_folder"] == "/movies/New"
    assert cfg["movie_watched_root_folder"] == "/movies/Watched"
    assert cfg["sonarr_url"] == ""
    assert cfg["jellyfin_path_updates_only"] is True


def test_load_config_sonarr_override(monkeypatch):
    monkeypatch.setenv("RADARR_URL", "https://radarr.example.com")
    monkeypatch.setenv("RADARR_API_KEY", "radarr-key")
    monkeypatch.setenv("SONARR_URL", "https://sonarr.example.com")
    monkeypatch.setenv("SONARR_API_KEY", "sonarr-key")
    monkeypatch.setenv("SONARR_WATCHED_TAG", "watched-series")
    monkeypatch.setenv("JELLYFIN_URL", "https://jellyfin.example.com")
    monkeypatch.setenv("JELLYFIN_API_KEY", "jellyfin-key")
    monkeypatch.setenv("JELLYFIN_USERNAME", "testuser")

    sys.modules.pop("sync_watched", None)
    import sync_watched as mod

    cfg = mod.load_config()
    assert cfg["sonarr_url"] == "https://sonarr.example.com"
    assert cfg["sonarr_api_key"] == "sonarr-key"
    assert cfg["sonarr_watched_tag"] == "watched-series"


def test_setup_logging_with_file(module, tmp_path):
    log_file = tmp_path / "sync.log"
    module.setup_logging({"log_level": "INFO", "log_file": str(log_file)})
    module.logger.info("hello")
    assert True


def test_api_request_json_response(module, monkeypatch):
    class FakeResponse:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda req, timeout=30: FakeResponse())
    result = module.api_request("https://example.com")
    assert result == {"ok": True}


def test_api_request_with_headers_and_body(module, monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=30):
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    module.api_request(
        "https://example.com",
        method="POST",
        data={"x": 1},
        api_key="k1",
        emby_token="k2",
    )

    assert captured["headers"]["X-api-key"] == "k1"
    assert captured["headers"]["X-emby-token"] == "k2"
    assert captured["data"] is not None


def test_api_request_empty_response(module, monkeypatch):
    class FakeResponse:
        status = 204

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda req, timeout=30: FakeResponse())
    result = module.api_request("https://example.com")
    assert result == {"status": 204}


def test_api_request_non_json(module, monkeypatch):
    class FakeResponse:
        status = 200

        def read(self):
            return b"not-json"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda req, timeout=30: FakeResponse())
    result = module.api_request("https://example.com")
    assert result["status"] == 200
    assert "raw" in result


def test_api_request_http_error(module, monkeypatch):
    class FakeHTTPError(module.urllib.error.HTTPError):
        def __init__(self):
            super().__init__("https://x", 500, "boom", hdrs=None, fp=None)

        def read(self):
            return b"server error"

    def raise_error(req, timeout=30):
        raise FakeHTTPError()

    monkeypatch.setattr(module.urllib.request, "urlopen", raise_error)
    assert module.api_request("https://example.com") is None


def test_api_request_url_error(module, monkeypatch):
    def raise_error(req, timeout=30):
        raise module.urllib.error.URLError("down")

    monkeypatch.setattr(module.urllib.request, "urlopen", raise_error)
    assert module.api_request("https://example.com") is None


def test_api_request_timeout(module, monkeypatch):
    def raise_error(req, timeout=30):
        raise module.socket.timeout()

    monkeypatch.setattr(module.urllib.request, "urlopen", raise_error)
    assert module.api_request("https://example.com") is None


def test_get_jellyfin_user_id(module, monkeypatch):
    monkeypatch.setattr(
        module,
        "api_request",
        lambda *a, **k: [
            {"Name": "other", "Id": "1"},
            {"Name": "testuser", "Id": "abc123"},
        ],
    )
    assert module.get_jellyfin_user_id() == "abc123"


def test_get_jellyfin_user_id_none(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    assert module.get_jellyfin_user_id() is None


def test_get_arr_tag_id(module, monkeypatch):
    monkeypatch.setattr(
        module,
        "api_request",
        lambda *a, **k: [{"id": 7, "label": "watched"}],
    )
    assert module.get_arr_tag_id("https://radarr.example.com", "key", "watched") == 7


def test_get_arr_tag_id_missing(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: [])
    assert module.get_arr_tag_id("https://radarr.example.com", "key", "watched") is None


def test_get_jellyfin_items_movies(module, monkeypatch):
    monkeypatch.setattr(
        module,
        "api_request",
        lambda *a, **k: {
            "Items": [
                {
                    "Id": "jid1",
                    "Name": "Film",
                    "ProviderIds": {"Tmdb": "123"},
                    "UserData": {"Played": True},
                }
            ]
        },
    )
    result = module.get_jellyfin_items("user1", "movie")
    assert result["123"].title == "Film"
    assert result["123"].played is True


def test_get_jellyfin_items_series(module, monkeypatch):
    monkeypatch.setattr(
        module,
        "api_request",
        lambda *a, **k: {
            "Items": [
                {
                    "Id": "sid1",
                    "Name": "Show",
                    "ProviderIds": {"Tvdb": "321"},
                    "UserData": {"Played": False},
                }
            ]
        },
    )
    result = module.get_jellyfin_items("user1", "series")
    assert result["321"].title == "Show"
    assert result["321"].played is False


def test_get_arr_items_movie(module, monkeypatch):
    spec = module.SyncSpec(
        kind="movie",
        arr_name="Radarr",
        arr_url="https://radarr.example.com",
        arr_api_key="radarr-key",
        watched_tag_name="watched",
        new_root="/movies/New",
        watched_root="/movies/Watched",
    )

    monkeypatch.setattr(
        module,
        "api_request",
        lambda *a, **k: [
            {
                "id": 1,
                "tmdbId": 123,
                "title": "Film",
                "path": "/movies/New/Film",
                "rootFolderPath": "/movies/New",
                "tags": [7],
            }
        ],
    )

    result = module.get_arr_items(spec)
    assert result["123"].title == "Film"
    assert result["123"].arr_id == 1


def test_get_arr_items_series(module, monkeypatch):
    spec = module.SyncSpec(
        kind="series",
        arr_name="Sonarr",
        arr_url="https://sonarr.example.com",
        arr_api_key="sonarr-key",
        watched_tag_name="watched",
        new_root="/series/New",
        watched_root="/series/Watched",
    )

    monkeypatch.setattr(
        module,
        "api_request",
        lambda *a, **k: [
            {
                "id": 5,
                "tvdbId": 321,
                "title": "Show",
                "path": "/series/New/Show",
                "rootFolderPath": "/series/New",
                "tags": [1],
            }
        ],
    )

    result = module.get_arr_items(spec)
    assert result["321"].title == "Show"
    assert result["321"].arr_id == 5


def test_apply_watched_tag_to_arr_movie(module, monkeypatch):
    spec = module.SyncSpec(
        kind="movie",
        arr_name="Radarr",
        arr_url="https://radarr.example.com",
        arr_api_key="radarr-key",
        watched_tag_name="watched",
        new_root="/movies/New",
        watched_root="/movies/Watched",
    )
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"ok": True})
    assert module.apply_watched_tag_to_arr(spec, [1], 7, dry_run=False) is True


def test_apply_watched_tag_to_arr_empty(module):
    spec = module.SyncSpec(
        kind="movie",
        arr_name="Radarr",
        arr_url="https://radarr.example.com",
        arr_api_key="radarr-key",
        watched_tag_name="watched",
        new_root="/movies/New",
        watched_root="/movies/Watched",
    )
    assert module.apply_watched_tag_to_arr(spec, [], 7, dry_run=False) is True


def test_apply_watched_tag_to_arr_fail(module, monkeypatch):
    spec = module.SyncSpec(
        kind="series",
        arr_name="Sonarr",
        arr_url="https://sonarr.example.com",
        arr_api_key="sonarr-key",
        watched_tag_name="watched",
        new_root="/series/New",
        watched_root="/series/Watched",
    )
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    assert module.apply_watched_tag_to_arr(spec, [5], 1, dry_run=False) is False


def test_mark_jellyfin_played(module, monkeypatch):
    item = module.JellyfinItem(
        jellyfin_id="jid1",
        external_id="123",
        title="Film",
        played=False,
    )
    calls = []
    monkeypatch.setattr(module, "api_request", lambda *a, **k: calls.append((a, k)) or {"ok": True})
    module.mark_jellyfin_played("user1", [item], dry_run=False)
    assert calls


def test_mark_jellyfin_played_dry_run(module):
    item = module.JellyfinItem(
        jellyfin_id="jid1",
        external_id="123",
        title="Film",
        played=False,
    )
    module.mark_jellyfin_played("user1", [item], dry_run=True)


def test_move_arr_items(module, monkeypatch):
    spec = module.SyncSpec(
        kind="movie",
        arr_name="Radarr",
        arr_url="https://radarr.example.com",
        arr_api_key="radarr-key",
        watched_tag_name="watched",
        new_root="/movies/New",
        watched_root="/movies/Watched",
    )
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"ok": True})
    assert module.move_arr_items(spec, [1], dry_run=False) is True


def test_move_arr_items_empty(module):
    spec = module.SyncSpec(
        kind="series",
        arr_name="Sonarr",
        arr_url="https://sonarr.example.com",
        arr_api_key="sonarr-key",
        watched_tag_name="watched",
        new_root="/series/New",
        watched_root="/series/Watched",
    )
    assert module.move_arr_items(spec, [], dry_run=False) is True


def test_trigger_jellyfin_refresh_success(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"ok": True})
    module.trigger_jellyfin_refresh(dry_run=False)


def test_trigger_jellyfin_refresh_dry_run(module):
    module.trigger_jellyfin_refresh(dry_run=True)


def test_sync_arr_to_jellyfin_played_marks_missing(module, monkeypatch):
    spec = module.SyncSpec(
        kind="movie",
        arr_name="Radarr",
        arr_url="https://radarr.example.com",
        arr_api_key="radarr-key",
        watched_tag_name="watched",
        new_root="/movies/New",
        watched_root="/movies/Watched",
    )

    jellyfin_items = {
        "123": module.JellyfinItem("jid1", "123", "Film", False),
    }
    arr_items = {
        "123": module.ArrItem(1, "123", "Film", "/movies/New/Film", "/movies/New", [7]),
    }

    marked = []
    monkeypatch.setattr(module, "mark_jellyfin_played", lambda uid, items, dry_run: marked.extend(items))
    module.sync_arr_to_jellyfin_played(
        user_id="user1",
        jellyfin_items=jellyfin_items,
        arr_items=arr_items,
        watched_tag_id=7,
        dry_run=False,
        spec=spec,
        phase="repair",
    )
    assert len(marked) == 1
    assert marked[0].title == "Film"


def test_sync_media_type_movie_flow(module, monkeypatch):
    spec = module.SyncSpec(
        kind="movie",
        arr_name="Radarr",
        arr_url="https://radarr.example.com",
        arr_api_key="radarr-key",
        watched_tag_name="watched",
        new_root="/movies/New",
        watched_root="/movies/Watched",
    )

    monkeypatch.setattr(module, "get_arr_tag_id", lambda *a, **k: 7)

    first_jellyfin = {
        "123": module.JellyfinItem("jid1", "123", "Film", True),
    }
    second_jellyfin = {
        "123": module.JellyfinItem("jid1", "123", "Film", False),
    }
    jellyfin_calls = [first_jellyfin, second_jellyfin]
    monkeypatch.setattr(module, "get_jellyfin_items", lambda *a, **k: jellyfin_calls.pop(0))

    arr_items = {
        "123": module.ArrItem(1, "123", "Film", "/movies/New/Film", "/movies/New", []),
    }
    monkeypatch.setattr(module, "get_arr_items", lambda *a, **k: arr_items)
    monkeypatch.setattr(module, "apply_watched_tag_to_arr", lambda *a, **k: True)

    sync_phases = []
    monkeypatch.setattr(
        module,
        "sync_arr_to_jellyfin_played",
        lambda **kwargs: sync_phases.append(kwargs["phase"]),
    )

    moved_ids = []
    monkeypatch.setattr(
        module,
        "move_arr_items",
        lambda spec, ids, dry_run: (moved_ids.extend(ids) or True),
    )
    refresh_called = []
    monkeypatch.setattr(module, "trigger_jellyfin_refresh", lambda dry_run: refresh_called.append(True))
    monkeypatch.setattr(module.time, "sleep", lambda *a, **k: None)

    module.sync_media_type(
        spec=spec,
        user_id="user1",
        dry_run=False,
        skip_move=False,
        skip_refresh=False,
    )

    assert moved_ids == [1]
    assert refresh_called == [True]
    assert sync_phases == ["pre-move", "post-refresh-repair"]


def test_sync_media_type_skip_refresh(module, monkeypatch):
    spec = module.SyncSpec(
        kind="movie",
        arr_name="Radarr",
        arr_url="https://radarr.example.com",
        arr_api_key="radarr-key",
        watched_tag_name="watched",
        new_root="/movies/New",
        watched_root="/movies/Watched",
    )

    monkeypatch.setattr(module, "get_arr_tag_id", lambda *a, **k: 7)
    monkeypatch.setattr(module, "get_jellyfin_items", lambda *a, **k: {})
    monkeypatch.setattr(module, "get_arr_items", lambda *a, **k: {})
    monkeypatch.setattr(module, "sync_arr_to_jellyfin_played", lambda **kwargs: None)

    called = []
    monkeypatch.setattr(module, "trigger_jellyfin_refresh", lambda dry_run: called.append(True))

    module.sync_media_type(
        spec=spec,
        user_id="user1",
        dry_run=False,
        skip_move=False,
        skip_refresh=True,
    )

    assert called == []


def test_main_movies_only(module, monkeypatch):
    monkeypatch.setattr(module, "get_jellyfin_user_id", lambda: "user1")
    called = []

    monkeypatch.setattr(
        module,
        "sync_media_type",
        lambda **kwargs: called.append(kwargs["spec"].arr_name),
    )

    monkeypatch.setattr(
        module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(dry_run=False, skip_move=False, skip_refresh=False),
    )

    module.main()
    assert called == ["Radarr"]


def test_main_with_sonarr(module, monkeypatch):
    module.CONFIG["sonarr_url"] = "https://sonarr.example.com"
    module.CONFIG["sonarr_api_key"] = "sonarr-key"

    monkeypatch.setattr(module, "get_jellyfin_user_id", lambda: "user1")
    called = []

    monkeypatch.setattr(
        module,
        "sync_media_type",
        lambda **kwargs: called.append(kwargs["spec"].arr_name),
    )

    monkeypatch.setattr(
        module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(dry_run=False, skip_move=False, skip_refresh=False),
    )

    module.main()
    assert called == ["Radarr", "Sonarr"]


def test_main_no_jellyfin_user(module, monkeypatch):
    monkeypatch.setattr(module, "get_jellyfin_user_id", lambda: None)
    monkeypatch.setattr(
        module.argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(dry_run=False, skip_move=False, skip_refresh=False),
    )

    with pytest.raises(SystemExit):
        module.main()