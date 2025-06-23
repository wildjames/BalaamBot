import logging
from collections.abc import Callable

from discord.channel import CategoryChannel, ForumChannel

from balaambot import discord_utils, utils
from balaambot.youtube.download import (
    download_and_convert,
    fetch_audio_pcm,
    get_metadata,
)
from balaambot.youtube.metadata import get_youtube_track_metadata
from balaambot.youtube.utils import get_cache_path, get_temp_paths

# TODO: This should maintain a "playing" state so we can pause and resume playback

# Mapping from voice client to its queue of YouTube URLs
# The key is the guild ID, and the value is a list of URLs.
youtube_queue: dict[int, list[str]] = {}
# Always check that this many tracks in the queue are cached
QUEUE_FORESIGHT = 3

logger = logging.getLogger(__name__)


async def add_to_queue(
    vc: discord_utils.DISCORD_VOICE_CLIENT, url: str, text_channel: int | None = None
) -> None:
    """Add a YouTube URL to the playback queue for the given voice client.

    If nothing is playing, start playback immediately.
    """
    queue = youtube_queue.setdefault(vc.guild.id, [])
    queue.append(url)
    logger.info(
        "Queued URL %s for guild_id=%s (queue length=%d)", url, vc.guild.id, len(queue)
    )

    # dispatch a subprocess that fetches the metadata
    vc.loop.run_in_executor(utils.FUTURES_EXECUTOR, get_metadata, logger, url)

    # If this is the only item, start playback
    if len(queue) == 1:
        logger.info("Queue created for guild_id=%s, starting playback", vc.guild.id)

        # Start playback immediately
        try:
            vc.loop.create_task(_play_next(vc, text_channel=text_channel))
        except Exception:
            logger.exception("Failed to start playback for guild_id=%s", vc.guild.id)
            # If we fail to start playback, we should clear the queue
            youtube_queue.pop(vc.guild.id, None)
            raise


async def _maybe_preload_next_tracks(
    vc: discord_utils.DISCORD_VOICE_CLIENT,
    queue: list[str],
    foresight: int = QUEUE_FORESIGHT,
) -> None:
    logger.info("Caching the next %d tracks in the queue...", foresight)

    upcoming = queue[1 : foresight + 1]  # skip the currently playing track

    for url in upcoming:
        logger.info("  - Caching %s", url)

    for url in upcoming:
        mixer = discord_utils.get_mixer_from_voice_client(vc)

        cache_path = get_cache_path(url, mixer.SAMPLE_RATE, mixer.CHANNELS)
        if cache_path.exists():
            continue  # already downloaded

        opus_tmp, pcm_tmp = get_temp_paths(url)
        try:
            await vc.loop.run_in_executor(
                utils.FUTURES_EXECUTOR,
                download_and_convert,
                logger,
                url,
                opus_tmp,
                pcm_tmp,
                cache_path,
                mixer.SAMPLE_RATE,
                mixer.CHANNELS,
            )
        except Exception:
            logger.exception("Failed to pre-download %s", url)
            try:
                queue.remove(url)
                logger.warning("Removed %s from queue due to pre-download failure", url)
                # Try fetching again, since the queue changed
                await _maybe_preload_next_tracks(vc, queue, foresight)
            except ValueError:
                pass  # failed to remove is the only error this can catch, I think?


def create_before_after_functions(
    url: str, vc: discord_utils.DISCORD_VOICE_CLIENT, text_channel: int | None = None
) -> tuple[Callable[[], None], Callable[[], None]]:
    """Create before and after play functions for a given YouTube URL.

    Returns:
        A tuple containing the before_play function and the after_play function.

    """

    def _before_play() -> None:
        logger.info("Starting playback for %s", url)
        if text_channel is not None:
            channel = vc.guild.get_channel(text_channel)
            logger.info(
                "Sending a playback started message to channel '%s' (instance of %s)",
                channel,
                type(channel),
            )
            if channel is not None and not isinstance(
                channel, (ForumChannel, CategoryChannel)
            ):
                # Schedule an async task to fetch metadata and send the message
                async def _send_now_playing() -> None:
                    try:
                        track = await get_youtube_track_metadata(url)
                        content = (
                            f"▶️    Now playing **[{track['title']}]({track['url']})**"
                        )
                        await channel.send(content=content)
                    except Exception:
                        logger.exception(
                            "Failed to send 'now playing' message for %s", url
                        )
                        content = "Now playing next track"
                        await channel.send(content=content)

                vc.loop.create_task(_send_now_playing())

    def _after_play() -> None:
        # Remove the URL from the queue after playback
        youtube_queue[vc.guild.id].pop(0)

        # If the queue is now empty, remove the guild entry
        if not youtube_queue[vc.guild.id]:
            youtube_queue.pop(vc.guild.id, None)
            logger.info("Queue empty for guild_id=%s, removed queue", vc.guild.id)
            if text_channel is not None:
                channel = vc.guild.get_channel(text_channel)
                if channel is not None and not isinstance(
                    channel, (ForumChannel, CategoryChannel)
                ):
                    job = channel.send(content="😮‍💨    Finished playing queue!")
                    vc.loop.create_task(job)

        # Schedule the next track when this one finishes
        logger.info("Finished playing %s for guild_id=%s", url, vc.guild.id)
        vc.loop.create_task(_play_next(vc, text_channel=text_channel))

    return _before_play, _after_play


async def _play_next(
    vc: discord_utils.DISCORD_VOICE_CLIENT, text_channel: int | None = None
) -> None:
    """Internal: play the next URL in the queue for vc, if any."""
    queue = youtube_queue.get(vc.guild.id)
    logger.info("Queue length for guild_id=%s: %d", vc.guild.id, len(queue or []))

    if not queue:
        logger.info("No more tracks in queue for guild_id=%s", vc.guild.id)
        youtube_queue.pop(vc.guild.id, None)
        return

    url = queue[0]
    logger.info("Starting playback of %s for guild_id=%s", url, vc.guild.id)

    await get_youtube_track_metadata(url)

    _before_play, _after_play = create_before_after_functions(url, vc, text_channel)

    try:
        mixer = discord_utils.get_mixer_from_voice_client(vc)
        cache_path = await fetch_audio_pcm(
            url,
            sample_rate=mixer.SAMPLE_RATE,
            channels=mixer.CHANNELS,
        )
        mixer.play_pcm(
            cache_path,
            before_play=_before_play,
            after_play=_after_play,
        )

        mixer.resume()
        vc.play(mixer)

        await _maybe_preload_next_tracks(vc, queue)
    except Exception:
        logger.exception("Error playing YouTube URL %s", url)
        # Clear the queue to avoid infinite retries
        youtube_queue.pop(vc.guild.id, None)


def get_current_track(vc: discord_utils.DISCORD_VOICE_CLIENT) -> str | None:
    """Get the currently playing YouTube URL for the voice client."""
    queue = youtube_queue.get(vc.guild.id)
    if queue and len(queue) > 0:
        return queue[0]
    return None


async def skip(vc: discord_utils.DISCORD_VOICE_CLIENT) -> None:
    """Skip the current track and play the next in queue."""
    mixer = discord_utils.get_mixer_from_voice_client(vc)
    try:
        # This triggers the after_play callback which will handle the next track
        logger.info("Skipping current track for guild_id=%s", vc.guild.id)
        mixer.skip_current_tracks()
    except Exception:
        logger.exception("Error stopping current track for guild_id=%s", vc.guild.id)


async def clear_queue(vc: discord_utils.DISCORD_VOICE_CLIENT) -> None:
    """Clear all queued tracks for the voice client. Excludes current playback."""
    if vc.guild.id in youtube_queue:
        # Remove all but the currently playing track
        youtube_queue[vc.guild.id] = youtube_queue[vc.guild.id][:1]
        logger.info("Cleared YouTube queue for guild_id=%s", vc.guild.id)


async def list_queue(vc: discord_utils.DISCORD_VOICE_CLIENT) -> list[str]:
    """Return the list of queued URLs for the voice client."""
    return list(youtube_queue.get(vc.guild.id, []))


async def stop(vc: discord_utils.DISCORD_VOICE_CLIENT) -> None:
    """Stop playback and clear the queue."""
    # Stop current playback
    mixer = discord_utils.get_mixer_from_voice_client(vc)
    try:
        mixer.clear_tracks()
        mixer.pause()
    except Exception:
        logger.exception("Error stopping playback for guild_id=%s", vc.guild.id)
    # Remove queue
    youtube_queue.pop(vc.guild.id, None)
