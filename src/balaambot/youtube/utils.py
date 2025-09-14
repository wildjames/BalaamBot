import atexit
import logging
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Any, TypedDict, cast

from balaambot import config
from balaambot.utils import get_cache, sec_to_string, set_cache

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 48000  # Default sample rate for PCM audio
DEFAULT_CHANNELS = 2  # Default number of audio channels (stereo)


logger.info(
    "Using a sample rate of %dHz with %d channels",
    DEFAULT_SAMPLE_RATE,
    DEFAULT_CHANNELS,
)

AUDIO_CACHE_ROOT = Path(config.PERSISTENT_DATA_DIR) / "audio_cache"
logger.info("Using audio download and caching directory: '%s'", AUDIO_CACHE_ROOT)

# Directory for caching PCM audio files
audio_cache_dir = (AUDIO_CACHE_ROOT / "cached").resolve()
audio_cache_dir.mkdir(parents=True, exist_ok=True)

# Temporary directory for in-progress downloads
audio_tmp_dir = (AUDIO_CACHE_ROOT / "downloading").resolve()
audio_tmp_dir.mkdir(parents=True, exist_ok=True)


# Cleanup temp directory on exit
def _cleanup_tmp() -> None:
    shutil.rmtree(audio_tmp_dir, ignore_errors=True)


atexit.register(_cleanup_tmp)


# Dictionary to store video metadata by URL
class VideoMetadata(TypedDict):
    """Metadata for a YouTube video."""

    url: str
    title: str
    runtime: int  # in seconds
    runtime_str: str  # formatted as H:MM:SS or M:SS


# Regex to extract YouTube video ID
_YT_ID_RE = re.compile(
    r"""
    ^(?:https?://)?                     # optional scheme
    (?:(?:www|music)\.)?                # optional www. or music.
    (?:                                 # host + path alternatives:
        youtube\.com/
        (?:
            watch\?(?:.*&)?v=
            |embed/
            |shorts/
        )
      |youtu\.be/                       # optional short URL
    )
    (?P<id>[A-Za-z0-9_-]{11})           # the 11-char video ID
    .*                                  # Any extra query parameters
    """,
    re.VERBOSE,
)
_VALID_YT_URL_RE = re.compile(
    r"""
    ^(?:https?://)?                     # optional scheme
    (?:(?:www|music)\.)?                # optional www. or music.
    (?:                                 # host + path alternatives:
        youtube\.com/
        (?:
            watch\?(?:.*&)?v=
            |embed/
            |shorts/
            |playlist/
        )
      |youtu\.be/                       # optional short URL
    )
    [A-Za-z0-9_-]{11}                   # the 11-char video ID
    .*                                  # Any extra query parameters
    """,
    re.VERBOSE,
)
_VALID_YT_PLAYLIST_URL = re.compile(
    r"""
    ^(?:https?://)?                    # optional scheme
    (?:(?:www|music)\.)?               # optional www. or music.
    (?:
        youtube\.com/
        (?:
            playlist\?list=           #   /playlist?list=ID
          | watch\?(?:.*&)?list=      #   /watch?...&list=ID
          | embed/videoseries\?list=  #   /embed/videoseries?list=ID
        )
      | youtu\.be/[A-Za-z0-9_-]{11}\? #   youtu.be/VIDEO_ID?
        (?:.*&)?list=                 #   ...&list=ID
    )
    (?P<playlist_id>[A-Za-z0-9_-]+)    # capture the playlist ID
    (?:[&?].*)?                        # optional extra params
    $
    """,
    re.VERBOSE,
)


async def extract_metadata(data: dict[str, Any]) -> VideoMetadata:
    """Takes the dict from youtube, makes a metadata object, and stores it on disk."""
    url = cast("str", data.get("url"))
    title = cast("str", data.get("title")) or url
    duration_s = cast("int", data.get("duration")) or 0

    meta: VideoMetadata = VideoMetadata(
        url=url,
        title=title,
        runtime=duration_s,
        runtime_str=sec_to_string(duration_s),
    )

    await cache_set_metadata(meta)

    return meta


async def cache_set_metadata(meta: VideoMetadata) -> None:
    """Cache metadata for given video by using the video ID and updating the cache."""
    video_id = get_video_id(meta["url"])
    logger.info("Caching metadata for video ID '%s'", video_id)
    await set_cache(video_id, dict(meta))


async def cache_get_metadata(
    url: str | None = None,
    video_id: str | None = None,
) -> VideoMetadata:
    """Retrieve cached video metadata using the provided URL or video ID.

    Raises:
        ValueError: If both video_id and url are None or if the cache key is None.

    """
    if video_id is None and url is None:
        msg = "Either video ID or url must be a string. Both were None"
        raise ValueError(msg)

    # video_id is guaranteed to be non-None if url is None
    key = get_video_id(url) if url is not None else video_id
    if key is None:
        msg = "Derived key for cache is None"
        raise ValueError(msg)

    meta = await get_cache(key)
    return VideoMetadata(**meta)


def is_valid_youtube_url(url: str) -> bool:
    """Check if a URL is a valid YouTube video URL."""
    return _VALID_YT_URL_RE.match(url) is not None


def get_video_id(url: str) -> str:
    """Extract the video ID from a YouTube URL."""
    match = _YT_ID_RE.match(url)
    if match is not None:
        return match.group("id")

    msg = f"Failed to get video ID from url '{url}'"
    raise ValueError(msg)


def get_cache_path(url: str, sample_rate: int, channels: int) -> Path:
    """Compute the cache file path for a URL and audio parameters."""
    vid = get_video_id(url)
    base = vid or url.replace("/", "_")
    filename = f"{base}_{sample_rate}Hz_{channels}ch.pcm"
    return audio_cache_dir / filename


def get_temp_paths(url: str) -> tuple[Path, Path]:
    """Construct the tempfile paths for youtube downloading."""
    vid = get_video_id(url)
    base = vid or url.replace("/", "_")
    opus_tmp = audio_tmp_dir / f"{base}.opus.part"
    pcm_tmp = audio_tmp_dir / f"{base}.pcm.part"
    return opus_tmp, pcm_tmp


def get_metadata_path(url: str) -> Path:
    """Returns the metadata file path for a url."""
    vid = get_video_id(url)
    base = vid or url.replace("/", "_")
    filename = f"{base}_metadata.json"
    return audio_cache_dir / filename


def is_valid_youtube_playlist(url: str) -> bool:
    """Check if a URL is a valid YouTube playlist URL."""
    return _VALID_YT_PLAYLIST_URL.match(url) is not None


def get_playlist_id(url: str) -> str:
    """Extract the playlist ID from the youtube URL."""
    match = _VALID_YT_PLAYLIST_URL.match(url)
    if match is not None:
        return match.group("playlist_id")

    msg = f"Failed to get playlist ID from url '{url}'"
    raise ValueError(msg)


def get_audio_pcm(
    url: str, sample_rate: int = DEFAULT_SAMPLE_RATE, channels: int = DEFAULT_CHANNELS
) -> bytearray | None:
    """Retrieve PCM audio data for a previously fetched URL."""
    path = get_cache_path(url, sample_rate, channels)
    if not path.exists():
        logger.error("No cached audio for URL: %s", url)
        return None
    data = path.read_bytes()
    return bytearray(data)


def remove_audio_pcm(
    url: str, sample_rate: int = DEFAULT_SAMPLE_RATE, channels: int = DEFAULT_CHANNELS
) -> bool:
    """Remove cached PCM audio for a URL."""
    path = get_cache_path(url, sample_rate, channels)
    if path.exists():
        path.unlink()
        logger.info("Removed cached audio for %s", url)
        return True
    logger.warning("Attempted to remove non-existent cache for %s", url)
    return False


def check_is_playlist(url: str) -> bool:
    """Check if the given url is a playlist by looking for the 'link' parameter.

    Returns True if a non-empty 'list' query parameter is present.
    """
    if not is_valid_youtube_playlist(url):
        return False

    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)

    # 'list' will be a key if there's a playlist ID in the URL
    return bool(params.get("list", False))


def add_auth_cookie(ydl_opts: dict[str, Any]) -> dict[str, Any]:
    """Add the cookie file to the yt-dlp options if it exists."""
    if config.COOKIE_FILE:
        if not config.COOKIE_FILE.exists():
            msg = f"Cookie file '{config.COOKIE_FILE}' does not exist."
            raise FileNotFoundError(msg)
        logger.info("Using cookie file: %s", config.COOKIE_FILE)
        ydl_opts["cookiefile"] = str(config.COOKIE_FILE)

    return ydl_opts
