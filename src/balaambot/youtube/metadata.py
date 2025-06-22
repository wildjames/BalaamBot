import asyncio
import logging
from typing import Any, cast

from yt_dlp import DownloadError, YoutubeDL

from balaambot import utils
from balaambot.youtube.utils import (
    VideoMetadata,
    add_auth_cookie,
    cache_get_metadata,
    cache_set_metadata,
    check_is_playlist,
    extract_metadata,
    is_valid_youtube_url,
)

logger = logging.getLogger(__name__)


async def get_youtube_track_metadata(url: str) -> VideoMetadata:
    """Get the track metadata from a YouTube URL, caching to JSON files."""
    if not is_valid_youtube_url(url):
        msg = "Invalid YouTube URL: %s"
        raise ValueError(msg, url)

    try:
        meta_dict = await cache_get_metadata(url)
        return VideoMetadata(**meta_dict)

    except KeyError:
        logger.info(
            "No metadata in cache for URL. Fetching track metadata for URL: '%s'", url
        )

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
    }
    ydl_opts = add_auth_cookie(ydl_opts)

    def _extract_info(opts: dict[str, Any], target_url: str) -> dict[str, Any] | None:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(target_url, download=False)  # type: ignore[no-typing]

    info = await asyncio.to_thread(_extract_info, ydl_opts, url)

    if not info:
        msg = "Failed to get youtube metadata"
        raise ValueError(msg)

    title = cast("str", info.get("title")) or url  # type: ignore[no-typing]
    duration_s = cast("int", info.get("duration")) or 0  # type: ignore[no-typing]

    meta: VideoMetadata = VideoMetadata(
        url=url,
        title=title,
        runtime=duration_s,
        runtime_str=utils.sec_to_string(duration_s),
    )

    await cache_set_metadata(meta)
    logger.debug("Cached metadata for '%s'", url)

    return meta


async def get_playlist_video_urls(playlist_url: str) -> list[str]:
    """Return a list of all video URLs in the given playlist."""
    if not check_is_playlist(playlist_url):
        return []

    ydl_opts: dict[str, Any] = {
        "logger": logger,
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    ydl_opts = add_auth_cookie(ydl_opts)

    def _extract_playlist(opts: dict[str, Any], url: str) -> dict[str, Any] | None:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)  # type: ignore[no-typing]

    try:
        info = await asyncio.to_thread(_extract_playlist, ydl_opts, playlist_url)
    except DownloadError as e:
        logger.warning(
            "yt-dlp failed to extract playlist info for %s: %s", playlist_url, e
        )
        return []
    except Exception:
        logger.exception("Unexpected error fetching playlist info for %s", playlist_url)
        return []

    if info is None:
        msg = "Retrieved info from youtube was None"
        raise TypeError(msg)

    entries = cast("list[dict[str, Any]]", info.get("entries")) or []  # type: ignore[no-typing]

    video_urls: list[str] = []
    metadata_promises = []
    for entry in entries:
        vid_id = entry.get("id")
        if not vid_id:
            continue

        video_urls.append(f"https://www.youtube.com/watch?v={vid_id}")
        metadata_promises.append(extract_metadata(entry))

    # waiting for metadata to be cached
    await asyncio.gather(*metadata_promises)

    return video_urls


async def search_youtube(search: str, n: int = 5) -> list[tuple[str, str, float]]:
    """Return the top ``n`` search results for the given query."""
    ydl_opts: dict[str, Any] = {
        "logger": logger,
        "extract_flat": True,
        "forceid": True,
        "forcetitle": True,
        "noprogress": True,
        "quiet": True,
        "skip_download": True,
    }
    ydl_opts = add_auth_cookie(ydl_opts)

    search = f"ytsearch{n + 2}:{search}"

    def _run_extract(opts: dict[str, Any], query: str) -> dict[str, Any] | None:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(query, download=False)  # type: ignore[no-typing]

    try:
        info: dict[str, Any] | None = await asyncio.to_thread(
            _run_extract, ydl_opts, search
        )
    except DownloadError as e:
        logger.warning("yt-dlp failed to extract search for %s: %s", search, e)
        return []
    except Exception:
        logger.exception("Unexpected error fetching search for %s", search)
        return []

    if info is None:
        msg = "Retrieved info from youtube was None"
        raise TypeError(msg)

    entries = cast("list[dict[str, Any]]", info.get("entries")) or []  # type: ignore[no-typing]

    results: list[tuple[str, str, float]] = []
    for entry in entries:
        duration = entry.get("duration")
        video_id = entry.get("id")
        title = entry.get("title")

        if duration is None or not video_id or not title:
            continue

        url = f"https://www.youtube.com/watch?v={video_id}"
        results.append((url, title, duration))
        if len(results) == n:
            break

    logger.info('Search for "%s" return %d results', search, len(results))
    return results
