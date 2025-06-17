import asyncio
import json
from pathlib import Path

import pytest
from yt_dlp import DownloadError

from balaambot import utils

# Adjust import path if module name differs
from balaambot.youtube import download, metadata

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def clear_state():
    # Clear cached metadata and locks before each test
    download._download_locks.clear()
    yield
    download._download_locks.clear()


# Tests for get_youtube_track_metadata
async def test_invalid_url(monkeypatch):
    monkeypatch.setattr(metadata, "is_valid_youtube_url", lambda url: False)
    with pytest.raises(ValueError):
        await metadata.get_youtube_track_metadata("bad_url")


async def test_download_error(monkeypatch):
    monkeypatch.setattr(metadata, "is_valid_youtube_url", lambda url: True)

    class DummyYDL:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            raise DownloadError("fail")

    monkeypatch.setattr(metadata, "YoutubeDL", lambda opts: DummyYDL())
    with pytest.raises(DownloadError):
        await metadata.get_youtube_track_metadata("u")


async def test_unexpected_exception(monkeypatch):
    monkeypatch.setattr(metadata, "is_valid_youtube_url", lambda url: True)

    class DummyYDL:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            raise ValueError("boom")

    monkeypatch.setattr(metadata, "YoutubeDL", lambda opts: DummyYDL())
    with pytest.raises(ValueError):
        await metadata.get_youtube_track_metadata("u")


async def test_successful_metadata(monkeypatch, tmp_path):
    # Test that metadata is fetched and cached
    monkeypatch.setattr(metadata, "is_valid_youtube_url", lambda url: True)
    info = {"title": "My Video", "duration": 123}

    class DummyYDL:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return info

    monkeypatch.setattr(metadata, "YoutubeDL", lambda opts: DummyYDL())
    monkeypatch.setattr(utils, "sec_to_string", lambda s: "2:03")

    # Override metadata path to tmp
    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(metadata, "get_metadata_path", lambda u: meta_path)
    result = await metadata.get_youtube_track_metadata("url1")
    assert result == {
        "url": "url1",
        "title": "My Video",
        "runtime": 123,
        "runtime_str": "2:03",
    }
    # Ensure cache file is created
    assert meta_path.exists()


async def test_metadata_cache_hit(monkeypatch, tmp_path):
    # Test returning metadata from existing cache
    data = {"url": "u", "title": "t", "runtime": 1, "runtime_str": "0:01"}
    cache_file = tmp_path / "meta.json"
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(metadata, "is_valid_youtube_url", lambda url: True)
    monkeypatch.setattr(metadata, "get_metadata_path", lambda u: cache_file)
    # Should return without invoking YoutubeDL
    monkeypatch.setattr(
        metadata,
        "YoutubeDL",
        lambda opts: (_ for _ in ()).throw(RuntimeError("Shouldn't be called")),
    )
    result = await metadata.get_youtube_track_metadata("u")
    assert result == data


async def test_metadata_defaults(monkeypatch, tmp_path):
    # Test default title and duration
    monkeypatch.setattr(metadata, "is_valid_youtube_url", lambda url: True)
    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(metadata, "get_metadata_path", lambda u: meta_path)

    class DummyYDL:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            return {"title": None, "duration": None}

    monkeypatch.setattr(metadata, "YoutubeDL", lambda opts: DummyYDL())
    monkeypatch.setattr(utils, "sec_to_string", lambda s: "0:00")
    result = await metadata.get_youtube_track_metadata("u")
    assert result["title"] == "u"
    assert result["runtime"] == 0
    assert result["runtime_str"] == "0:00"


# Tests for fetch_cached_youtube_track_metadata
async def test_fetch_cached_metadata_success(monkeypatch, tmp_path):
    data = {"url": "u", "title": "t", "runtime": 1, "runtime_str": "0:01"}
    meta_file = tmp_path / "meta.json"
    meta_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(metadata, "get_metadata_path", lambda u: meta_file)
    result = metadata.fetch_cached_youtube_track_metadata("u")
    assert result == data


async def test_fetch_cached_metadata_missing(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(metadata, "get_metadata_path", lambda u: Path("/nonexistent"))
    with pytest.raises(FileNotFoundError):
        metadata.fetch_cached_youtube_track_metadata("u")


# Tests for fetch_audio_pcm
async def test_fetch_audio_cache_hit(monkeypatch, tmp_path):
    cache = tmp_path / "audio.pcm"
    cache.write_bytes(b"data")
    monkeypatch.setattr(download, "get_cache_path", lambda u, sr, ch: cache)
    result = await download.fetch_audio_pcm("any_url")
    assert result == cache


async def test_fetch_audio_auth(monkeypatch):
    monkeypatch.setattr(
        download, "get_cache_path", lambda u, sr, ch: Path("/tmp/nonexistent")
    )
    with pytest.raises(NotImplementedError):
        await download.fetch_audio_pcm("u", username="user", password="pass")


async def test_fetch_audio_download_error(monkeypatch, tmp_path):
    cache = tmp_path / "out.pcm"
    monkeypatch.setattr(download, "get_cache_path", lambda u, sr, ch: cache)
    monkeypatch.setattr(
        download, "get_temp_paths", lambda u: (tmp_path / "a.opus", tmp_path / "b.pcm")
    )

    async def fail_download(u, p, username=None, password=None):
        raise DownloadError("dl fail")

    monkeypatch.setattr(download, "_download_opus", fail_download)

    async def dummy_meta(u):
        return None

    monkeypatch.setattr(metadata, "get_youtube_track_metadata", dummy_meta)
    with pytest.raises(RuntimeError) as ei:
        await download.fetch_audio_pcm("u")
    assert "Failed to download audio for u" in str(ei.value)


async def test_fetch_audio_success(monkeypatch, tmp_path):
    cache = tmp_path / "cached.pcm"
    opus_tmp = tmp_path / "t.opus"
    pcm_tmp = tmp_path / "t.pcm"
    monkeypatch.setattr(download, "get_cache_path", lambda u, sr, ch: cache)
    monkeypatch.setattr(download, "get_temp_paths", lambda u: (opus_tmp, pcm_tmp))

    async def fake_download(u, p, username=None, password=None):
        p.write_bytes(b"o")

    monkeypatch.setattr(download, "_download_opus", fake_download)

    async def fake_meta(u):
        return {"url": u, "title": "t", "runtime": 1, "runtime_str": "0:01"}

    monkeypatch.setattr(metadata, "get_youtube_track_metadata", fake_meta)

    async def fake_convert(o, p, c, sr, ch):
        p.write_bytes(b"p")
        p.replace(c)

    monkeypatch.setattr(download, "_convert_opus_to_pcm", fake_convert)
    result = await download.fetch_audio_pcm("u")
    assert result == cache


# Tests for _sync_download
async def test_sync_download_success(monkeypatch):
    called = {}

    class DummyYDL:
        def __init__(self, opts):
            called["opts"] = opts

        def download(self, urls):
            called["urls"] = urls

    monkeypatch.setattr(metadata, "YoutubeDL", DummyYDL)
    opts = {"format": "bestaudio"}
    url = "https://youtu.be/test"
    download._sync_download(opts, url)
    assert called["opts"] is opts
    assert called["urls"] == [url]


async def test_sync_download_propagates_error(monkeypatch):
    class DummyYDL:
        def __init__(self, opts):
            pass

        def download(self, urls):
            raise DownloadError("dl fail")

    monkeypatch.setattr(metadata, "YoutubeDL", DummyYDL)
    with pytest.raises(DownloadError):
        download._sync_download({"a": 1}, "u")


# Tests for _download_opus
async def test_download_opus_failure(monkeypatch, tmp_path):
    url = "u"
    opus_tmp = tmp_path / "file"

    class DummyYDL:
        def __init__(self, opts):
            pass

        def download(self, lst):
            pass

    monkeypatch.setattr(metadata, "YoutubeDL", lambda opts: DummyYDL(opts))
    with pytest.raises(RuntimeError) as ei:
        await download._download_opus(url, opus_tmp)
    assert "yt-dlp failed to produce" in str(ei.value)


async def test_download_opus_success(monkeypatch, tmp_path):
    url = "u"
    opus_tmp = tmp_path / "file"

    # Fake download writes .opus
    def fake_sync_download(opts, target_url):
        outtmpl = opts["outtmpl"]
        path = Path(f"{outtmpl}.opus")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"dummy")

    monkeypatch.setattr(download, "_sync_download", fake_sync_download)
    monkeypatch.setattr(download.utils, "FUTURES_EXECUTOR", None)
    await download._download_opus(url, opus_tmp)
    assert opus_tmp.exists() and opus_tmp.read_bytes() == b"dummy"


# Tests for _convert_opus_to_pcm
async def test_convert_opus_to_pcm_failure(monkeypatch, tmp_path):
    opus_tmp = tmp_path / "in.opus"
    pcm_tmp = tmp_path / "out.pcm"
    cache = tmp_path / "c.pcm"
    opus_tmp.write_bytes(b"d")

    class DummyProcess:
        def __init__(self):
            self.returncode = 1

        async def communicate(self):
            return (b"", b"err")

    async def fake_exec(*args, **kwargs):
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError) as ei:
        await download._convert_opus_to_pcm(opus_tmp, pcm_tmp, cache, 8000, 1)
    assert "ffmpeg failed:" in str(ei.value)


async def test_convert_opus_to_pcm_success(monkeypatch, tmp_path):
    opus_tmp = tmp_path / "in.opus"
    pcm_tmp = tmp_path / "out.pcm"
    cache = tmp_path / "c.pcm"
    opus_tmp.write_bytes(b"d")
    pcm_tmp.write_bytes(b"p")

    class DummyProcess:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def fake_exec(*args, **kwargs):
        return DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await download._convert_opus_to_pcm(opus_tmp, pcm_tmp, cache, 16000, 2)
    assert not opus_tmp.exists()
    assert cache.exists()


# Tests for get_playlist_video_urls
async def test_playlist_not_playlist(monkeypatch):
    monkeypatch.setattr(metadata, "check_is_playlist", lambda u: False)
    result = await metadata.get_playlist_video_urls("u")
    assert result == []


async def test_playlist_download_error(monkeypatch):
    monkeypatch.setattr(metadata, "check_is_playlist", lambda u: True)

    class DummyYDL:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, u, download):
            raise DownloadError("fail")

    monkeypatch.setattr(metadata, "YoutubeDL", lambda opts: DummyYDL())
    result = await metadata.get_playlist_video_urls("u")
    assert result == []


async def test_playlist_exception(monkeypatch):
    monkeypatch.setattr(metadata, "check_is_playlist", lambda u: True)

    class DummyYDL:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def replicate_info(self, u, download):
            raise ValueError("err")

    monkeypatch.setattr(metadata, "YoutubeDL", lambda opts: DummyYDL())
    result = await metadata.get_playlist_video_urls("u")
    assert result == []


async def test_playlist_info_none(monkeypatch):
    monkeypatch.setattr(metadata, "check_is_playlist", lambda u: True)

    class DummyYDL:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, u, download):
            return None

    monkeypatch.setattr(metadata, "YoutubeDL", lambda opts: DummyYDL())
    with pytest.raises(TypeError):
        await metadata.get_playlist_video_urls("u")


async def test_playlist_valid(monkeypatch):
    # Test valid playlist extraction and metadata side-effects
    playlist_url = "pl"
    monkeypatch.setattr(metadata, "check_is_playlist", lambda u: True)
    entries = [
        {"id": "id1"},
        {"id": None},
        {"id": "id2"},
    ]
    info = {"entries": entries}

    # Stub thread call to return our info
    async def fake_to_thread(func, opts, url):
        return info

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    # Capture calls to extract_metadata
    called = []
    monkeypatch.setattr(
        metadata, "extract_metadata", lambda entry: called.append(entry)
    )
    result = await metadata.get_playlist_video_urls(playlist_url)
    assert result == [
        "https://www.youtube.com/watch?v=id1",
        "https://www.youtube.com/watch?v=id2",
    ]
    # Ensure extract_metadata called for each non-null entry
    assert called == [entries[0], entries[2]]


# Tests for search_youtube
async def test_search_download_error(monkeypatch):
    async def fake_to_thread(func):
        raise DownloadError("fail")

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    result = await metadata.search_youtube("query")
    assert result == []


async def test_search_exception(monkeypatch):
    async def fake_to_thread(func):
        raise ValueError("boom")

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    result = await metadata.search_youtube("query")
    assert result == []


async def test_search_info_none(monkeypatch):
    async def fake_to_thread(func, *args):
        return None

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    with pytest.raises(TypeError):
        await metadata.search_youtube("query")


async def test_search_valid(monkeypatch):
    entries = [
        {"id": "1", "title": "A", "duration": 10},
        {"id": "2", "title": None, "duration": 5},
        {"id": None, "title": "C", "duration": 5},
        {"id": "3", "title": "D", "duration": 15},
    ]
    info = {"entries": entries}

    async def fake_to_thread(func, *args):
        return info

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    result = await metadata.search_youtube("q", n=2)
    assert result == [
        ("https://www.youtube.com/watch?v=1", "A", 10),
        ("https://www.youtube.com/watch?v=3", "D", 15),
    ]
