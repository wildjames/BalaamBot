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


class SearchView(View):
    """A view containing buttons for selecting search results."""

    def __init__(
        self, parent: "MusicCommands", results_list: list[tuple[str, str, float]]
    ) -> None:
        """Set up the internal structures."""
        super().__init__(timeout=None)  # no timeout so buttons remain valid
        self.results = results_list
        self.parent = parent

        for idx, (url, title, _) in enumerate(self.results):
            button = Button(  # type: ignore  This type error is daft and I hate it so fuck that
                label=f"{idx + 1}",
                style=discord.ButtonStyle.primary,
                custom_id=f"search_select_{idx}",
            )

            # Bind a callback that knows which index was clicked
            button.callback = self.make_callback(idx, url, title)  # type: ignore The button is well defined
            self.add_item(button)  # type: ignore The button is well defined

    def make_callback(
        self, idx: int, url: str, title: str
    ) -> Callable[
        [discord.Interaction], Awaitable[InteractionCallbackResponse[Client] | None]
    ]:
        """Handle the clicking of the buttons."""

        async def callback(
            inner_interaction: discord.Interaction,
        ) -> InteractionCallbackResponse[Client] | None:
            # Log which result the user picked
            logger.info(
                'User %s selected search result #%d: %s ("%s")',
                inner_interaction.user.name,
                idx + 1,
                url,
                title,
            )

            await inner_interaction.response.edit_message(
                content=f"Playing {title}", view=None, delete_after=5
            )
            await self.parent.do_play(inner_interaction, url)

        return callback


class MusicCommands(commands.Cog):
    """Slash commands for YouTube queue and playback."""

    MAX_QUEUE_REPORT_LENGTH = 10

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the MusicCommands cog."""
        self.bot = bot

    @app_commands.command(
        name="play", description="Enqueue and play a YouTube video audio"
    )
    @app_commands.describe(query="YouTube video URL, playlist URL, or search term")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        """Enqueue a YouTube URL; starts playback if idle."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        query = query.strip()

        # Handle playlist URLs
        if yt_utils.is_valid_youtube_playlist(query):
            logger.info("Received play command for playlist URL: '%s'", query)
            self.bot.loop.create_task(self.do_play_playlist(interaction, query))

        # Handle youtube videos
        elif yt_utils.is_valid_youtube_url(query):
            logger.info("Received play command for URL: '%s'", query)
            self.bot.loop.create_task(self.do_play(interaction, query))

        # Fall back to searching youtube and asking the user to select a search result
        elif query:
            logger.info("Received a string. Searching youtube for videos. '%s'", query)
            self.bot.loop.create_task(self.do_search_youtube(interaction, query))

        else:
            # Failed to do anything. I think this is only reached if the query is empty?
            await interaction.followup.send(
                content=(
                    "Invalid play command. Please provide a valid youtube video "
                    "or playlist link, or a searchable string."
                ),
                ephemeral=True,
            )
            logger.warning(
                "Received an empty query for play command in guild_id=%s",
                interaction.guild_id,
            )

    async def do_search_youtube(
        self, interaction: discord.Interaction, query: str
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

        # Build a text block describing each result line by line
        lines: list[str] = []
        for idx, (_, title, duration_secs) in enumerate(results):
            duration_str = sec_to_string(duration_secs)
            lines.append(f"**{idx + 1}.** {title} ({duration_str})")

        description = (
            "Select a track by clicking the corresponding button:\n\n"
            + "\n".join(lines)
        )

        # Send the reply with the View
        await interaction.followup.send(
            content=description,
            view=SearchView(self, results),
            ephemeral=True,
        )

    async def do_play_playlist(
        self, interaction: discord.Interaction, playlist_url: str
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
        for track_url in track_urls:
            # These have to be awaited, to preserve order.
            await yt_jobs.add_to_queue(
                vc, track_url, text_channel=interaction.channel_id
            )

        # Confirmation message
        await interaction.followup.send(
            f"🎵    Queued {len(track_urls)} tracks from playlist.", ephemeral=False
        )

        return None

    async def do_play(self, interaction: discord.Interaction, url: str) -> None:
        """Play a YouTube video by fetching and streaming the audio from the URL."""
        # Check if the user is in a voice channel
        vc_mixer = await discord_utils.get_voice_channel_mixer(interaction)
        if vc_mixer is None:
            return
        vc, mixer = vc_mixer

        # Add to queue. Playback (in mixer) will await cache when it's time
        await yt_jobs.add_to_queue(vc, url, text_channel=interaction.channel_id)

        track_meta = await yt_audio.get_youtube_track_metadata(url)
        if track_meta is None:
            await interaction.followup.send(
                f"Failed to fetch track metadata. Please check the URL. [{url}]",
                ephemeral=True,
            )
            return

        queue = await yt_jobs.list_queue(vc)
        pos = len(queue)

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
        upcoming = await yt_jobs.list_queue(vc)

        if not upcoming:
            msg = "The queue is empty."
            return await interaction.followup.send(msg, ephemeral=True)

        lines: list[str] = []
        total_runtime = 0

        for i, url in enumerate(upcoming):
            if len(lines) >= self.MAX_QUEUE_REPORT_LENGTH:
                pass

            new_line = f"{i + 1}. [Invalid track URL]({url})"

            track_meta = await yt_audio.get_youtube_track_metadata(url)

            if i == 0:
                # Only the first track gets a URL link and a now playing tag
                new_line = (
                    f"# Now playing: [{track_meta['title']}]"
                    f"({track_meta['url']})"
                    f" ({track_meta['runtime_str']})\n"
                )
            else:
                new_line = (
                    f"{i + 1}. *{track_meta['title']}* ({track_meta['runtime_str']})"
                )
            total_runtime += track_meta["runtime"]
            lines.append(new_line)

        msg = (
            f"\n\n**Upcoming tracks ({len(lines)} of {len(upcoming)} shown):**\n"
            + "\n".join(lines)
        )

        # format runtime as H:MM:SS or M:SS
        total_runtime_str = sec_to_string(total_runtime)
        msg += f"\n\n###    Total runtime: {total_runtime_str}"

        return await interaction.followup.send(msg, ephemeral=True)

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
