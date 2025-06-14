import atexit
import logging
import re
import shutil
import urllib.parse
from pathlib import Path

import balaambot.config

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 48000  # Default sample rate for PCM audio
DEFAULT_CHANNELS = 2  # Default number of audio channels (stereo)


logger.info(
    "Using a sample rate of %dHz with %d channels",
    DEFAULT_SAMPLE_RATE,
    DEFAULT_CHANNELS,
)

AUDIO_CACHE_ROOT = Path(balaambot.config.PERSISTENT_DATA_DIR) / "audio_cache"
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


def is_valid_youtube_url(url: str) -> bool:
    """Check if a URL is a valid YouTube video URL."""
    return _VALID_YT_URL_RE.match(url) is not None


def get_video_id(url: str) -> str | None:
    """Extract the video ID from a YouTube URL."""
    match = _YT_ID_RE.match(url)
    return match.group("id") if match else None


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


def is_valid_youtube_playlist(url: str) -> bool:
    """Check if a URL is a valid YouTube playlist URL."""
    return _VALID_YT_PLAYLIST_URL.match(url) is not None


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
