import logging
import random
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from balaambot import discord_utils
from balaambot.sfx import audio_sfx_jobs

logger = logging.getLogger(__name__)


# TODOs:
# - too many sounds in the zip
#   - `/list_sfx`` gives an error because it hits the message size limit
# - check if sfx file exists before running it and joining channel
# - sanitize sfx file names and find files with similar names
# - add fuzzy search
# - add basic file browser with discord msg buttons
# - integrate with soundboard API?


class SFXCommands(commands.Cog):
    """Slash commands for scheduling and triggering SFX jobs."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the SFXCommands cog."""
        self.bot = bot

    @app_commands.command(name="add_sfx", description="Add a scheduled SFX job")
    @app_commands.describe(
        sound="Filename of the sound effect (including extension)",
        min_interval="Minimum seconds between plays",
        max_interval="Maximum seconds between plays",
    )
    async def add_sfx(
        self,
        interaction: discord.Interaction,
        sound: str,
        min_interval: float,
        max_interval: float,
    ) -> None:
        """Add a scheduled sound effect (SFX) job to the server."""
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return
        channel, _member = channel_member
        guild = channel.guild

        vc = await discord_utils.ensure_connected(guild, channel)
        try:
            job_id = await audio_sfx_jobs.add_job(vc, sound, min_interval, max_interval)
            message = (
                f"✅    Added job `{job_id}`: `{sound}` "
                f"every {min_interval:.1f}-{max_interval:.1f}s."
            )
            await interaction.response.send_message(message, ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(
                f"Failed to add job: {e}", ephemeral=True
            )

    @app_commands.command(name="remove_sfx", description="Remove a scheduled SFX job")
    @app_commands.describe(job_id="The ID of the job to remove")
    async def remove_sfx(self, interaction: discord.Interaction, job_id: str) -> None:
        """Remove a scheduled SFX job using its job identifier."""
        if await discord_utils.require_guild(interaction) is None:
            return

        try:
            await audio_sfx_jobs.remove_job(job_id)
            await interaction.response.send_message(
                f"🗑️    Removed job `{job_id}`.", ephemeral=True
            )
        except KeyError:
            await interaction.response.send_message(
                f"No job found with ID `{job_id}`.", ephemeral=True
            )

    @app_commands.command(name="list_sfx_jobs", description="List active SFX jobs")
    async def list_sfx_jobs(self, interaction: discord.Interaction) -> None:
        """Send a list of active jobs in the server."""
        guild = await discord_utils.require_guild(interaction)
        if guild is None:
            return

        jobs: list[str] = []
        for jid, (vc, _task, sound, mi, ma) in audio_sfx_jobs.loop_jobs.items():
            if vc.guild.id == guild.id:
                jobs.append(f"`{jid}`: `{sound}` every {mi:.1f}-{ma:.1f}s")

        if not jobs:
            await interaction.response.send_message(
                "No active jobs in this server.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "**Active jobs:**\n" + "\n".join(jobs), ephemeral=True
            )

    @app_commands.command(name="list_sfx", description="List available sound effects")
    async def list_sfx(self, interaction: discord.Interaction) -> None:
        """List all available sound effects."""
        await interaction.response.defer(thinking=True)
        if await discord_utils.require_guild(interaction) is None:
            return

        sound_files = audio_sfx_jobs.SOUND_FILES
        if not sound_files:
            await interaction.followup.send(
                "No sound effects available.", ephemeral=True
            )
            return

        # Format the list of sound files
        formatted_sounds = "\n".join(f"- {Path(sound).name}" for sound in sound_files)
        await interaction.followup.send(
            f"**Available sound effects:**\n{formatted_sounds}", ephemeral=True
        )

    @app_commands.command(
        name="trigger_sfx",
        description="Manually play a random sound effect",
    )
    async def trigger_sfx(self, interaction: discord.Interaction) -> None:
        """Play a random sound effect in the voice channel."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return
        channel, _member = channel_member
        guild = channel.guild

        # pick & fire off the effect
        sound = random.choice(audio_sfx_jobs.SOUND_FILES)  # noqa: S311

        vc = await discord_utils.ensure_connected(guild, channel)
        mixer = await discord_utils.get_mixer_from_interaction(interaction)

        await mixer.play_file(sound)
        if not vc.is_playing():
            vc.play(mixer)
        await interaction.followup.send(
            f"🔊    Playing **{Path(sound).name}**", ephemeral=False
        )

    @app_commands.command(name="stop_sfx", description="Stop all SFX playback")
    async def stop_sfx(self, interaction: discord.Interaction) -> None:
        """Stop all sound effect playback in the voice channel."""
        if await discord_utils.require_guild(interaction) is None:
            return

        mixer = await discord_utils.get_mixer_from_interaction(interaction)
        mixer.clear_sfx()
        mixer.pause()
        await interaction.response.send_message("⏹️    Stopped all SFX playback.")

    @app_commands.command(
        name="play_sfx", description="Play a sound effect immediately"
    )
    @app_commands.describe(sound="Filename of the sound effect (including extension)")
    async def play_sfx(self, interaction: discord.Interaction, sound: str) -> None:
        """Play a sound effect immediately in the voice channel."""
        channel_member = await discord_utils.require_voice_channel(interaction)
        if channel_member is None:
            return
        channel, _member = channel_member
        guild = channel.guild

        vc = await discord_utils.ensure_connected(guild, channel)
        mixer = await discord_utils.get_mixer_from_interaction(interaction)

        try:
            await mixer.play_file(sound)
            if not vc.is_playing():
                vc.play(mixer)

            await interaction.response.send_message(
                f"🔊    Playing sound effect: **{sound}**", ephemeral=False
            )
        except FileNotFoundError:
            await interaction.response.send_message(
                f"❌    Sound effect `{sound}` not found.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    """Add the SFXCommands cog to the bot."""
    logger.info("Loading SFXCommands cog")
    await bot.add_cog(SFXCommands(bot))
