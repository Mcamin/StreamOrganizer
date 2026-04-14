import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def module(monkeypatch):
    monkeypatch.setenv("RADARR_URL", "https://radarr.example.com")
    monkeypatch.setenv("RADARR_API_KEY", "radarr-key")
    monkeypatch.setenv("JELLYFIN_URL", "https://jellyfin.example.com")
    monkeypatch.setenv("JELLYFIN_API_KEY", "jellyfin-key")
    monkeypatch.setenv("JELLYFIN_USER_ID", "user-123")
    monkeypatch.setenv("WATCHED_TAG_ID", "1")
    monkeypatch.setenv("NEW_ROOT_FOLDER", "/movies/New")
    monkeypatch.setenv("WATCHED_ROOT_FOLDER", "/movies/Watched")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.delenv("LOG_FILE", raising=False)

    sys.modules.pop("sync_watched", None)
    import sync_watched as mod
    return mod


def test_require_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    monkeypatch.setenv("RADARR_URL", "x")
    monkeypatch.setenv("RADARR_API_KEY", "x")
    monkeypatch.setenv("JELLYFIN_URL", "x")
    monkeypatch.setenv("JELLYFIN_API_KEY", "x")
    monkeypatch.setenv("JELLYFIN_USER_ID", "x")
    sys.modules.pop("sync_watched", None)
    import sync_watched as mod
    with pytest.raises(SystemExit):
        mod.require_env("MISSING_VAR")
    captured = capsys.readouterr()
    assert "Missing required environment variable" in captured.err


def test_load_config(module):
    cfg = module.load_config()
    assert cfg["radarr_url"] == "https://radarr.example.com"
    assert cfg["watched_tag_id"] == 1
    assert cfg["new_root_folder"] == "/movies/New"


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


def test_api_request_unexpected_status(module, monkeypatch):
    class FakeResponse:
        status = 201
        def read(self):
            return b"{}"
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda req, timeout=30: FakeResponse())
    assert module.api_request("https://example.com") is None


def test_api_request_non_json(module, monkeypatch):
    class FakeResponse:
        status = 200
        def read(self):
            return b'not-json'
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


def test_api_request_http_error_read_fail(module, monkeypatch):
    class FakeHTTPError(module.urllib.error.HTTPError):
        def __init__(self):
            super().__init__("https://x", 500, "boom", hdrs=None, fp=None)
        def read(self):
            raise RuntimeError("no body")

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


def test_get_jellyfin_watched(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"Items": [{"Name": "Film", "ProviderIds": {"Tmdb": "123"}}]})
    assert module.get_jellyfin_watched() == {"123": "Film"}


def test_get_jellyfin_watched_none(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    assert module.get_jellyfin_watched() is None


def test_get_radarr_movies(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: [{"tmdbId": 123, "id": 1, "title": "Film", "path": "/movies/New/Film", "tags": []}])
    result = module.get_radarr_movies()
    assert result["123"]["title"] == "Film"


def test_get_radarr_movies_none(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    assert module.get_radarr_movies() is None


def test_tag_radarr_movies(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"ok": True})
    assert module.tag_radarr_movies([1], dry_run=False) is True


def test_tag_radarr_movies_empty(module):
    assert module.tag_radarr_movies([], dry_run=False) is True


def test_tag_radarr_movies_dry_run(module):
    assert module.tag_radarr_movies([1], dry_run=True) is True


def test_tag_radarr_movies_fail(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    assert module.tag_radarr_movies([1], dry_run=False) is False


def test_move_radarr_movies(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"ok": True})
    assert module.move_radarr_movies([1], dry_run=False) == [1]


def test_move_radarr_movies_empty(module):
    assert module.move_radarr_movies([], dry_run=False) == []


def test_move_radarr_movies_dry_run(module):
    assert module.move_radarr_movies([1], dry_run=True) == []


def test_move_radarr_movies_fail(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    assert module.move_radarr_movies([1], dry_run=False) == []


def test_fetch_jellyfin_movie_map(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"Items": [{"Id": "abc", "ProviderIds": {"Tmdb": "123"}}]})
    assert module.fetch_jellyfin_movie_map() == {"123": "abc"}


def test_fetch_jellyfin_movie_map_none(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    assert module.fetch_jellyfin_movie_map() is None


def test_update_jellyfin_paths(module, monkeypatch):
    module.radarr_movies_cache = {"1": {"path": "/movies/New/A", "title": "A"}}
    monkeypatch.setattr(module, "fetch_jellyfin_movie_map", lambda: {"1": "jid"})
    calls = []
    monkeypatch.setattr(module, "api_request", lambda *a, **k: calls.append((a, k)) or {"ok": True})
    module.update_jellyfin_paths([1], dry_run=False)
    assert calls


def test_update_jellyfin_paths_dry_run(module, monkeypatch):
    module.radarr_movies_cache = {"1": {"path": "/movies/New/A", "title": "A"}}
    monkeypatch.setattr(module, "fetch_jellyfin_movie_map", lambda: {"1": "jid"})
    module.update_jellyfin_paths([1], dry_run=True)


def test_update_jellyfin_paths_no_map(module, monkeypatch):
    monkeypatch.setattr(module, "fetch_jellyfin_movie_map", lambda: None)
    module.update_jellyfin_paths([1], dry_run=False)


def test_update_jellyfin_paths_skip_missing_tmdb(module, monkeypatch):
    module.radarr_movies_cache = {}
    monkeypatch.setattr(module, "fetch_jellyfin_movie_map", lambda: {"1": "jid"})
    module.update_jellyfin_paths([1], dry_run=False)


def test_update_jellyfin_paths_skip_same_path(module, monkeypatch):
    module.radarr_movies_cache = {"1": {"path": "/movies/Watched/A", "title": "A"}}
    monkeypatch.setattr(module, "fetch_jellyfin_movie_map", lambda: {"1": "jid"})
    module.update_jellyfin_paths([1], dry_run=False)


def test_update_jellyfin_paths_api_fail(module, monkeypatch):
    module.radarr_movies_cache = {"1": {"path": "/movies/New/A", "title": "A"}}
    monkeypatch.setattr(module, "fetch_jellyfin_movie_map", lambda: {"1": "jid"})
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    module.update_jellyfin_paths([1], dry_run=False)


def test_trigger_path_refresh_dry_run(module):
    module.trigger_path_refresh(dry_run=True)


def test_trigger_path_refresh_success(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"ok": True})
    module.trigger_path_refresh(dry_run=False)


def test_trigger_path_refresh_fail(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    module.trigger_path_refresh(dry_run=False)


def test_mark_jellyfin_unwatched_to_watched_dry_run(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"Items": [{"Id": "x", "Name": "Film", "ProviderIds": {"Tmdb": "1"}, "UserData": {"Played": False}}]})
    module.mark_jellyfin_unwatched_to_watched({"1"}, dry_run=True)


def test_mark_jellyfin_unwatched_to_watched_success(module, monkeypatch):
    responses = [
        {"Items": [{"Id": "x", "Name": "Film", "ProviderIds": {"Tmdb": "1"}, "UserData": {"Played": False}}]},
        {"ok": True},
    ]
    monkeypatch.setattr(module, "api_request", lambda *a, **k: responses.pop(0))
    module.mark_jellyfin_unwatched_to_watched({"1"}, dry_run=False)


def test_mark_jellyfin_unwatched_to_watched_api_fail(module, monkeypatch):
    responses = [
        {"Items": [{"Id": "x", "Name": "Film", "ProviderIds": {"Tmdb": "1"}, "UserData": {"Played": False}}]},
        None,
    ]
    monkeypatch.setattr(module, "api_request", lambda *a, **k: responses.pop(0))
    module.mark_jellyfin_unwatched_to_watched({"1"}, dry_run=False)


def test_mark_jellyfin_unwatched_to_watched_none(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: None)
    module.mark_jellyfin_unwatched_to_watched({"1"}, dry_run=False)


def test_mark_jellyfin_unwatched_to_watched_nothing(module, monkeypatch):
    monkeypatch.setattr(module, "api_request", lambda *a, **k: {"Items": []})
    module.mark_jellyfin_unwatched_to_watched({"1"}, dry_run=False)


def test_main_success(module, monkeypatch):
    monkeypatch.setattr(module, "get_jellyfin_watched", lambda: {"1": "Film"})
    monkeypatch.setattr(module, "get_radarr_movies", lambda: {"1": {"id": 1, "title": "Film", "tags": [], "path": "/movies/New/A"}})
    monkeypatch.setattr(module, "tag_radarr_movies", lambda *a, **k: True)
    monkeypatch.setattr(module, "mark_jellyfin_unwatched_to_watched", lambda *a, **k: None)
    monkeypatch.setattr(module, "move_radarr_movies", lambda *a, **k: [1])
    monkeypatch.setattr(module, "update_jellyfin_paths", lambda *a, **k: None)
    monkeypatch.setattr(module, "trigger_path_refresh", lambda *a, **k: None)
    monkeypatch.setattr(module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", lambda self: SimpleNamespace(dry_run=False, skip_move=False, skip_scan=False))
    module.main()


def test_main_fetch_fail(module, monkeypatch):
    monkeypatch.setattr(module, "get_jellyfin_watched", lambda: None)
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", lambda self: SimpleNamespace(dry_run=False, skip_move=False, skip_scan=False))
    with pytest.raises(SystemExit):
        module.main()


def test_main_radarr_fail(module, monkeypatch):
    monkeypatch.setattr(module, "get_jellyfin_watched", lambda: {})
    monkeypatch.setattr(module, "get_radarr_movies", lambda: None)
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", lambda self: SimpleNamespace(dry_run=False, skip_move=False, skip_scan=False))
    with pytest.raises(SystemExit):
        module.main()


def test_main_no_tag_skip_move_skip_scan(module, monkeypatch):
    monkeypatch.setattr(module, "get_jellyfin_watched", lambda: {"1": "Film"})
    monkeypatch.setattr(module, "get_radarr_movies", lambda: {"1": {"id": 1, "title": "Film", "tags": [1], "path": "/movies/Watched/A"}})
    monkeypatch.setattr(module, "mark_jellyfin_unwatched_to_watched", lambda *a, **k: None)
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", lambda self: SimpleNamespace(dry_run=False, skip_move=True, skip_scan=True))
    module.main()


def test_main_dry_run_skip_scan(module, monkeypatch):
    monkeypatch.setattr(module, "get_jellyfin_watched", lambda: {"1": "Film"})
    monkeypatch.setattr(module, "get_radarr_movies", lambda: {"1": {"id": 1, "title": "Film", "tags": [], "path": "/movies/New/A"}})
    monkeypatch.setattr(module, "tag_radarr_movies", lambda *a, **k: True)
    monkeypatch.setattr(module, "mark_jellyfin_unwatched_to_watched", lambda *a, **k: None)
    monkeypatch.setattr(module, "move_radarr_movies", lambda *a, **k: [])
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", lambda self: SimpleNamespace(dry_run=True, skip_move=False, skip_scan=False))
    module.main()


def test_main_move_branch_full(module, monkeypatch):
    monkeypatch.setattr(module, "get_jellyfin_watched", lambda: {"1": "Film"})
    monkeypatch.setattr(module, "get_radarr_movies", lambda: {"1": {"id": 1, "title": "Film", "tags": [1], "path": "/movies/New/A"}})
    monkeypatch.setattr(module, "mark_jellyfin_unwatched_to_watched", lambda *a, **k: None)
    monkeypatch.setattr(module, "move_radarr_movies", lambda *a, **k: [1])
    monkeypatch.setattr(module, "update_jellyfin_paths", lambda *a, **k: None)
    monkeypatch.setattr(module, "trigger_path_refresh", lambda *a, **k: None)
    monkeypatch.setattr(module.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", lambda self: SimpleNamespace(dry_run=False, skip_move=False, skip_scan=False))
    module.main()
