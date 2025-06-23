import importlib
import sys
from pathlib import Path

import pytest
import balaambot.config

# Helper to import the module under test with custom dirs
def import_utils(tmp_cache_root: Path):
    balaambot.config.PERSISTENT_DATA_DIR = str(tmp_cache_root)
    module_name = "src.balaambot.youtube.utils"
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


@pytest.fixture
def tmp_cache_root(tmp_path):
    root = tmp_path / "cache_root"
    root.mkdir()
    return root


def test_directories_created(tmp_cache_root):
    mod = import_utils(tmp_cache_root)
    expected_cache = (tmp_cache_root / "audio_cache/cached").resolve()
    expected_tmp = (tmp_cache_root / "audio_cache/downloading").resolve()

    assert mod.audio_cache_dir == expected_cache
    assert mod.audio_tmp_dir == expected_tmp
    assert expected_cache.exists()
    assert expected_tmp.exists()


@pytest.mark.parametrize(
    "url,expected_id",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("http://youtu.be/ABCDEFGHIJK", "ABCDEFGHIJK"),
        ("https://music.youtube.com/watch?v=12345678901&list=PL", "12345678901"),
    ],
)
def test_get_video_id(tmp_cache_root, url, expected_id):
    mod = import_utils(tmp_cache_root)
    assert mod.get_video_id(url) == expected_id


def test_get_video_id_invalid(tmp_cache_root):
    mod = import_utils(tmp_cache_root)
    with pytest.raises(ValueError):
        mod.get_video_id("invalid_url")


@pytest.mark.parametrize(
    "url,rate,channels,expected_suffix",
    [
        ("https://youtu.be/ABCDEFGHIJK", 48000, 2, "ABCDEFGHIJK_48000Hz_2ch.pcm"),
    ],
)
def test_get_cache_path(tmp_cache_root, url, rate, channels, expected_suffix):
    mod = import_utils(tmp_cache_root)
    path = mod.get_cache_path(url, rate, channels)
    expected_parent = (tmp_cache_root / "audio_cache/cached").resolve()
    assert path.parent == expected_parent
    assert path.name == expected_suffix


def test_get_cache_path_invalid(tmp_cache_root):
    mod = import_utils(tmp_cache_root)
    with pytest.raises(ValueError):
        mod.get_cache_path("foo/bar", 44100, 1)


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/IDABCDE1234",
        "https://www.youtube.com/watch?v=XYZ12345678",
    ],
)
def test_get_temp_paths(tmp_cache_root, url):
    mod = import_utils(tmp_cache_root)
    opus_tmp, pcm_tmp = mod.get_temp_paths(url)
    vid = mod.get_video_id(url)
    expected_tmp_dir = (tmp_cache_root / "audio_cache/downloading").resolve()
    assert opus_tmp.parent == expected_tmp_dir
    assert pcm_tmp.parent == expected_tmp_dir
    assert opus_tmp.name == f"{vid}.opus.part"
    assert pcm_tmp.name == f"{vid}.pcm.part"


@pytest.mark.parametrize(
    "url,valid",
    [
        ("https://www.youtube.com/watch?v=abcdEFGHijk", True),
        ("https://youtu.be/abcdEFGHijk", True),
        ("https://www.youtube.com/watch?v=shortID", False),
        ("not a url", False),
    ],
)
def test_is_valid_youtube_url(tmp_cache_root, url, valid):
    mod = import_utils(tmp_cache_root)
    assert mod.is_valid_youtube_url(url) == valid


@pytest.mark.parametrize(
    "url,valid",
    [
        ("https://www.youtube.com/playlist?list=PL67890", True),
        ("https://www.youtube.com/watch?v=PL123456789&list=PL67890", True),
        ("https://youtu.be/XYZ12345678?list=ABCDEF&foo=bar", True),
        ("https://www.youtube.com/watch?v=abcdefghiJK", False),
        ("not a playlist", False),
    ],
)
def test_is_valid_youtube_playlist(tmp_cache_root, url, valid):
    mod = import_utils(tmp_cache_root)
    assert mod.is_valid_youtube_playlist(url) == valid


def test_get_audio_pcm_and_remove(tmp_cache_root):
    mod = import_utils(tmp_cache_root)
    url = "https://youtu.be/TESTVIDEOID"
    cache_dir = mod.audio_cache_dir

    # Nothing cached initially
    assert mod.get_audio_pcm(url) is None

    filename = f"TESTVIDEOID_{mod.DEFAULT_SAMPLE_RATE}Hz_{mod.DEFAULT_CHANNELS}ch.pcm"
    pcm_path = cache_dir / filename
    content = b"\x01\x02\x03"
    pcm_path.write_bytes(content)

    data = mod.get_audio_pcm(url)
    assert isinstance(data, bytearray)
    assert data == bytearray(content)

    removed = mod.remove_audio_pcm(url)
    assert removed is True
    assert not pcm_path.exists()
    # Removing again
    assert not mod.remove_audio_pcm(url)


def test_add_auth_cookie_adds_cookie(tmp_path, monkeypatch):
    import balaambot.youtube.utils as utils
    import balaambot.config as config
    # Create a dummy cookie file
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("cookie-data")
    ydl_opts = {}
    monkeypatch.setattr(config, "COOKIE_FILE", cookie_file)
    out = utils.add_auth_cookie(ydl_opts)
    assert "cookiefile" in out and out["cookiefile"] == str(cookie_file)


def test_check_is_playlist():
    from balaambot.youtube.utils import check_is_playlist
    assert check_is_playlist("https://www.youtube.com/playlist?list=PL123")
    assert not check_is_playlist("https://youtu.be/abcdEFGHijk")
