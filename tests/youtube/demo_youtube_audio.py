import asyncio
import logging
from pathlib import Path

from balaambot.youtube.download import fetch_audio_pcm
from balaambot.youtube.metadata import (
    get_playlist_video_urls,
    get_youtube_track_metadata,
    search_youtube,
)
from balaambot.youtube.utils import (
    get_audio_pcm,
    remove_audio_pcm,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


async def fetch_audio(
    test_url: str,
    cookiefile: Path | None = None,
) -> None:
    logger.info("Testing audio fetching for URL: %s", test_url)
    # Fetch and cache audio
    t0 = asyncio.get_event_loop().time()
    cache_path = await fetch_audio_pcm(test_url, cookiefile=cookiefile)
    t1 = asyncio.get_event_loop().time()
    logger.info("Fetched audio cache at: %s", cache_path)
    logger.info("Fetch took %.2f seconds", t1 - t0)

async def test_get_metadata(test_url: str) -> None:
    # Get track name
    t0 = asyncio.get_event_loop().time()
    track_metadata = await get_youtube_track_metadata(test_url)
    t1 = asyncio.get_event_loop().time()
    if track_metadata:
        logger.info("Track name: %s", track_metadata["title"])
        logger.info("Track runtime: %s seconds", track_metadata["runtime"])
        logger.info("Track name fetch took %.2f seconds", t1 - t0)
    else:
        logger.warning("Could not fetch track name for URL: %s", test_url)

async def load_cached_audio_pcm(test_url: str) -> None:
    # Read raw PCM bytes
    t0 = asyncio.get_event_loop().time()
    pcm_data = get_audio_pcm(test_url)
    t1 = asyncio.get_event_loop().time()
    if pcm_data:
        logger.info("Loaded %s bytes of PCM data", len(pcm_data))
        logger.info("PCM data read took %.2f seconds", t1 - t0)

    await asyncio.sleep(5)

    # Remove cached file
    removed = remove_audio_pcm(test_url)
    logger.info("Cache removed: %s", removed)

async def get_playlist(playlist_url: str) -> None:
    logger.info("Fetching a playlist of URLs: %s", playlist_url)
    urls = await get_playlist_video_urls(playlist_url)
    logger.info("URLs:")
    for url in urls:
        logger.info(" - %s", url)

async def search(search: str) -> None:
    # search for a video
    logger.info("Fetching search results for query '%s'", search)
    results = await search_youtube(search)
    logger.info("Results:")
    for result in results:
        logger.info(" - %s", result)


if __name__ == "__main__":

    async def main() -> None:
        """Test the audio fetching and caching functionality."""
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        test_playlist_url = "https://www.youtube.com/watch?v=Z0Uh3OJCx3o&list=PLJDafirWnxGR5H0rSeJKgxC6rIj78JKce"
        test_search_query = "Rick Astley Never Gonna Give You Up"

        # await fetch_audio(test_url)
        # await test_get_metadata(test_url)
        # await load_cached_audio_pcm(test_url)
        # await get_playlist(test_playlist_url)
        # await search(test_search_query)

        age_restricted_url = "https://www.youtube.com/watch?v=wiRRsHPTSC8"
        cookiefile = Path("persistent/cookies.txt")
        await fetch_audio(age_restricted_url, cookiefile=cookiefile)

    asyncio.run(main())
