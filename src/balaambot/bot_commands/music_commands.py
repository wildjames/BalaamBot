import logging
from collections.abc import Awaitable, Callable

import discord
from discord import Client, InteractionCallbackResponse, app_commands
from discord.ext import commands
from discord.ui import Button, View

from balaambot import discord_utils
from balaambot.utils import sec_to_string
from balaambot.youtube import jobs as yt_jobs
from balaambot.youtube import metadata as yt_audio
from balaambot.youtube import utils as yt_utils

logger = logging.getLogger(__name__)


def truncate_label(text: str, suffix: str = "", max_length: int = 80) -> str:
    """Utility to truncate button labels to Discord's limit."""
    total_len = len(text) + len(suffix)
    if total_len <= max_length:
        return text
    # leave room for ellipsis
    return text[: max_length - len(suffix) - 3] + "..."


class SearchView(View):
    """A view containing buttons for selecting search results."""

    def __init__(
        self,
        parent: "MusicCommands",
        results: list[tuple[str, str, float]],
        *,
        queue_to_top: bool = False,
    ):
        """Set up the internal structures."""
        super().__init__(timeout=None)  # no timeout so buttons remain valid
        self.parent = parent

        for idx, (url, title, runtime) in enumerate(results):
            label_text = truncate_label(title, suffix=f" ({sec_to_string(runtime)})")
            btn = Button(
                label=f"{label_text} ({sec_to_string(runtime)})",
                style=discord.ButtonStyle.primary,
                custom_id=f"search_{idx}",
                row=idx,
            )
            btn.callback = self._make_callback(
                idx, url, title, queue_to_top=queue_to_top
            )  # type: ignore The button is well defined
            self.add_item(btn)

    def _make_callback(
        self, idx: int, url: str, title: str, *, queue_to_top: bool
    ) -> Callable[
        [discord.Interaction], Awaitable[InteractionCallbackResponse[Client] | None]
    ]:
        async def callback(
            interaction: discord.Interaction,
        ) -> InteractionCallbackResponse[Client] | None:
            logger.info(
                'User %s selected search result #%d: %s ("%s")',
                interaction.user.name,
                idx + 1,
                url,
                title,
            )

            await interaction.response.edit_message(
                content=f"Playing {title}", view=None, delete_after=5
            )
            await self.parent.do_play(interaction, url, queue_to_top=queue_to_top)

        return callback


class PruneView(View):
    """A view for pruning tracks from the queue."""

    def __init__(
        self,
        parent: "MusicCommands",
        vc: discord_utils.DISCORD_VOICE_CLIENT,
        items: list[tuple[str, str]],
    ) -> None:
        """Initialize the prune view."""
        super().__init__(timeout=None)
        self.parent = parent
        self.vc = vc
        for idx, (url, title) in enumerate(items):
            label = truncate_label(title, max_length=80)
            btn = Button(
                label=label,
                style=discord.ButtonStyle.danger,
                custom_id=f"prune_{idx}",
                row=idx,
            )
            btn.callback = self._make_callback(url)  # type: ignore The button is well defined
            self.add_item(btn)

    def _make_callback(
        self, url: str
    ) -> Callable[
        [discord.Interaction], Awaitable[InteractionCallbackResponse[Client] | None]
    ]:
        async def callback(
            interaction: discord.Interaction,
        ) -> InteractionCallbackResponse[Client] | None:
            logger.info(
                "User '%s' in channel '%s' selected to prune track URL: '%s'",
                interaction.user.name,
                interaction.channel_id,
                url,
            )

            success = await yt_jobs.prune_queue(self.vc, url=url)
            if not success:
                await interaction.response.send_message(
                    "Failed to remove track. Please check the position and try again.",
                    ephemeral=True,
                )
                return None

            track_meta = await yt_audio.get_youtube_track_metadata(url)

            await interaction.response.edit_message(
                content=f"Removed track {track_meta['title']} from the queue.",
                view=None,
                delete_after=20,
            )
            return None

        return callback


class MusicCommands(commands.Cog):
    """Slash commands for YouTube queue and playback."""

    MAX_QUEUE_REPORT_LENGTH = 10
    PRUNE_REPORT_LENGTH = 5

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the MusicCommands cog."""
        self.bot = bot

    async def _enqueue(
        self,
        interaction: discord.Interaction,
        query: str,
        command_name: str,
        *,
        queue_to_top: bool,
    ) -> None:
        query = query.strip()
        if not query:
            await interaction.followup.send(
                f"Invalid {command_name} command. Provide a valid URL or search term.",
                ephemeral=True,
            )
            logger.warning(
                "Empty query for %s in guild %s", command_name, interaction.guild_id
            )
            return

        if yt_utils.is_valid_youtube_playlist(query):
            logger.info("%s playlist URL: %s", command_name, query)
            task = self.do_play_playlist(interaction, query, queue_to_top=queue_to_top)
        elif yt_utils.is_valid_youtube_url(query):
            logger.info("%s video URL: %s", command_name, query)
            task = self.do_play(interaction, query, queue_to_top=queue_to_top)
        else:
            logger.info("%s search query: %s", command_name, query)
            task = self.do_search_youtube(interaction, query, queue_to_top=queue_to_top)

        task = self.bot.loop.create_task(task)
        try:
            await task
        except Exception as e:
            logger.exception(
                "Error while processing %s command for query '%s'",
                command_name,
                query,
            )
            await interaction.followup.send(
                f"An error occurred while processing your {command_name} request: {e}",
                ephemeral=True,
            )
            return

    @app_commands.command(
        name="play", description="Enqueue and play a YouTube video audio"
    )
    @app_commands.describe(query="YouTube URL, playlist URL, or search term")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        """Enqueue and play a YouTube video audio.

        Parameters
        ----------
        interaction : discord.Interaction
            The interaction from Discord.
        query : str
            The YouTube URL, playlist URL, or search term to search and play.

        """
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self._enqueue(interaction, query, queue_to_top=False, command_name="play")

    @app_commands.command(
        name="play_next", description="Queue a track to the top of the queue"
    )
    @app_commands.describe(query="YouTube URL, playlist URL, or search term")
    async def play_next(self, interaction: discord.Interaction, query: str) -> None:
        """Queue a track to the top of the queue.

        Parameters
        ----------
        interaction : discord.Interaction
            The Discord interaction triggering the command.
        query : str
            The YouTube URL, playlist URL, or search term.

        """
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self._enqueue(
            interaction, query, queue_to_top=True, command_name="play_next"
        )

    async def do_search_youtube(
        self,
        interaction: discord.Interaction,
        query: str,
        *,
        queue_to_top: bool = False,
    ) -> None:
        """Search for videos based on the query and display selection buttons."""
        # Check if the user is in a voice channel
        vc_mixer = await discord_utils.get_voice_channel_mixer(interaction)
        if vc_mixer is None:
            return
        vc, mixer = vc_mixer

        results = await yt_audio.search_youtube(query)
        # Each result is a tuple: (url, title, duration_in_seconds)

        if not results:
            await interaction.followup.send(
                "No results found for your query.", ephemeral=True
            )
            return

        description = "Select a track by clicking the corresponding button:\n\n"

        # Send the reply with the View
        await interaction.followup.send(
            content=description,
            view=SearchView(self, results, queue_to_top=queue_to_top),
            ephemeral=True,
        )

    async def do_play_playlist(
        self,
        interaction: discord.Interaction,
        playlist_url: str,
        *,
        queue_to_top: bool = False,
    ) -> None:
        """Handle enqueuing all videos from a YouTube playlist."""
        # Check if the user is in a voice channel
        vc_mixer = await discord_utils.get_voice_channel_mixer(interaction)
        if vc_mixer is None:
            return None
        vc, mixer = vc_mixer

        # Fetch playlist video URLs
        track_urls = await yt_audio.get_playlist_video_urls(playlist_url)
        if not track_urls:
            return await interaction.followup.send(
                "Failed to fetch playlist or playlist is empty.", ephemeral=True
            )

        # Enqueue all tracks and start background fetches
        await yt_jobs.add_to_queue(
            vc,
            track_urls,
            text_channel=interaction.channel_id,
            queue_to_top=queue_to_top,
        )

        # Confirmation message
        await interaction.followup.send(
            f"🎵    Queued {len(track_urls)} tracks from playlist.", ephemeral=False
        )

        return None

    async def do_play(
        self, interaction: discord.Interaction, url: str, *, queue_to_top: bool = False
    ) -> None:
        """Play a YouTube video by fetching and streaming the audio from the URL."""
        # Check if the user is in a voice channel
        vc_mixer = await discord_utils.get_voice_channel_mixer(interaction)
        if vc_mixer is None:
            return
        vc, mixer = vc_mixer

        # Add to queue. Playback (in mixer) will await cache when it's time
        await yt_jobs.add_to_queue(
            vc, [url], text_channel=interaction.channel_id, queue_to_top=queue_to_top
        )

        track_meta = await yt_audio.get_youtube_track_metadata(url)
        if track_meta is None:
            await interaction.followup.send(
                f"Failed to fetch track metadata. Please check the URL. [{url}]",
                ephemeral=True,
            )
            return

        queue = await yt_jobs.list_queue(vc)
        pos = queue.index(url) + 1

        runtime = track_meta["runtime_str"]

        msg = (
            f"🎵    Queued **[{track_meta['title']}]({track_meta['url']})"
            f" ({runtime})** at position {pos}."
        )
        await interaction.followup.send(msg, ephemeral=False)

    @app_commands.command(name="list_queue", description="List upcoming YouTube tracks")
    async def list_queue(self, interaction: discord.Interaction) -> None:
        """Show the current YouTube queue for this server."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return None
        channel, _member = channel_member
        guild = channel.guild
        vc = await discord_utils.ensure_connected(guild, channel)

        msg = await yt_jobs.create_queue_message(
            vc, guild, self.MAX_QUEUE_REPORT_LENGTH
        )

        return await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(
        name="prune_queue", description="Remove a track from the YouTube queue"
    )
    async def prune_queue(self, interaction: discord.Interaction) -> None:
        """Remove a track from the YouTube queue by its position."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return await interaction.followup.send(
                "You must be in a voice channel to prune the queue.", ephemeral=True
            )
        channel, _member = channel_member
        guild = channel.guild
        vc = await discord_utils.ensure_connected(guild, channel)

        queued_urls = await yt_jobs.list_queue(vc)
        if not queued_urls:
            return await interaction.followup.send(
                "The queue is empty. Nothing to prune.", ephemeral=True
            )

        if len(queued_urls) == 1:
            return await interaction.followup.send(
                "There is only one track in the queue. "
                "Use `/stop_music` to clear the queue.",
                ephemeral=True,
            )

        # build a list of (url, title) tuples for the head of the prunable tracks list
        prunable_urls = queued_urls[1 : self.PRUNE_REPORT_LENGTH + 1]
        prunable_items: list[tuple[str, str]] = []
        for url in prunable_urls:
            meta = await yt_audio.get_youtube_track_metadata(url)
            prunable_items.append((url, meta["title"]))

        # Build the "now playing" portion of the queue message
        msg = await yt_jobs.create_queue_message(vc, guild, 1)
        msg += "\n\nSelect a track to prune from the queue:"

        return await interaction.followup.send(
            msg,
            view=PruneView(self, vc, prunable_items),
            ephemeral=True,
            suppress_embeds=True,
        )

    @app_commands.command(name="skip", description="Skip the current YouTube track")
    async def skip(self, interaction: discord.Interaction) -> None:
        """Stop current track and play next in queue."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return
        channel, _member = channel_member
        guild = channel.guild
        vc = await discord_utils.ensure_connected(guild, channel)
        await yt_jobs.skip(vc)
        logger.info("Skipped track for guild_id=%s", guild.id)

        track_url = yt_jobs.get_current_track(vc)
        if not track_url:
            await interaction.followup.send(
                "No track is currently playing.", ephemeral=True
            )
            return

        track_meta = await yt_audio.get_youtube_track_metadata(track_url)
        if track_meta is None:
            await interaction.followup.send(
                f"Failed to fetch track metadata. Please check the URL. [{track_url}]",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "⏭️    Skipped to next track",
            ephemeral=False,
        )

    @app_commands.command(
        name="pause",
        description="Pause the current YouTube track",
    )
    async def pause(self, interaction: discord.Interaction) -> None:
        """Pause the current YouTube track."""
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return
        channel, _member = channel_member
        guild = channel.guild
        vc = await discord_utils.ensure_connected(guild, channel)
        mixer = discord_utils.get_mixer_from_voice_client(vc)
        if mixer is None:
            await interaction.response.send_message(
                "Failed to pause playback. Not connected to a voice channel.",
                ephemeral=True,
            )
            return
        if not mixer.is_playing:
            await interaction.response.send_message(
                "No track is currently playing.", ephemeral=True
            )
            return

        # Pause the mixer
        logger.info("Pausing track for guild_id=%s", guild.id)
        mixer.pause()
        await interaction.response.send_message(
            "⏸️    Paused the current YouTube track.", ephemeral=False
        )

    @app_commands.command(
        name="resume",
        description="Resume playback of the current YouTube track",
    )
    async def resume(self, interaction: discord.Interaction) -> None:
        """Resume playback of the current YouTube track."""
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return

        channel, _member = channel_member
        guild = channel.guild

        vc = await discord_utils.ensure_connected(guild, channel)
        mixer = discord_utils.get_mixer_from_voice_client(vc)

        if mixer is None:
            await interaction.response.send_message(
                "Failed to resume playback. Not connected to a voice channel.",
                ephemeral=True,
            )
            return
        if mixer.is_playing:
            await interaction.response.send_message(
                "Track is already playing.", ephemeral=True
            )
            return
        if mixer.num_tracks == 0:
            await interaction.response.send_message(
                "No track is currently queued to resume.", ephemeral=True
            )
            return

        # Resume the mixer
        logger.info(
            "Resuming track for guild_id=%s. %d Track(s) in mixer",
            guild.id,
            mixer.num_tracks,
        )
        mixer.resume()
        if not vc.is_playing():
            vc.play(mixer)
        await interaction.response.send_message(
            "▶️    Resumed the current YouTube track.", ephemeral=False
        )

    @app_commands.command(
        name="stop_music",
        description="Stop playback and clear YouTube queue",
    )
    async def stop_music(self, interaction: discord.Interaction) -> None:
        """Stop the current YouTube track and clear all queued tracks."""
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return
        channel, _member = channel_member
        guild = channel.guild
        vc = await discord_utils.ensure_connected(guild, channel)
        await yt_jobs.stop(vc)
        await interaction.response.send_message(
            "⏹️    Stopped and cleared YouTube queue.", ephemeral=False
        )

    @app_commands.command(name="clear_queue", description="Clear the YouTube queue")
    async def clear_queue(self, interaction: discord.Interaction) -> None:
        """Remove all queued YouTube tracks."""
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return
        channel, _member = channel_member
        guild = channel.guild
        vc = await discord_utils.ensure_connected(guild, channel)
        logger.info("Clearing YouTube queue for guild_id=%s", guild.id)

        # Clear the queue
        await yt_jobs.clear_queue(vc)
        current_queue = await yt_jobs.list_queue(vc)
        logger.info("queue after clearing: %s", current_queue)

        await interaction.response.send_message(
            "🗑️    Cleared the YouTube queue.", ephemeral=False
        )


async def setup(bot: commands.Bot) -> None:
    """Load the MusicCommands cog."""
    logger.info("Loading MusicCommands cog")
    await bot.add_cog(MusicCommands(bot))
