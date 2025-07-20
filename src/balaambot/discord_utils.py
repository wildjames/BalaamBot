import logging
from typing import cast

import discord

from balaambot.audio_handlers.multi_audio_source import MultiAudioSource, ensure_mixer
from balaambot.config import DISCORD_VOICE_CLIENT

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000


async def _send_interaction_message(
    interaction: discord.Interaction, message: str, *, ephemeral: bool = True
) -> None:
    """Send a message using the correct interaction method."""
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(message, ephemeral=ephemeral)


async def require_guild(
    interaction: discord.Interaction,
) -> discord.Guild | None:
    """Ensure the interaction was triggered inside a guild."""
    if interaction.guild is None:
        await _send_interaction_message(
            interaction, "This command only works in a server.", ephemeral=True
        )
        return None
    return interaction.guild


async def require_voice_channel(
    interaction: discord.Interaction,
) -> tuple[discord.VoiceChannel, discord.Member] | None:
    """Ensure the user is in a voice channel and return it."""
    guild = await require_guild(interaction)
    if guild is None:
        return None

    member = guild.get_member(interaction.user.id)
    if (
        not member
        or not member.voice
        or not member.voice.channel
        or not isinstance(member.voice.channel, discord.VoiceChannel)
    ):
        await _send_interaction_message(
            interaction,
            "You need to be in a standard voice channel to use this command.",
            ephemeral=True,
        )
        return None

    return member.voice.channel, member


async def ensure_connected(
    guild: discord.Guild, channel: discord.VoiceChannel
) -> DISCORD_VOICE_CLIENT:
    """Connect to voice or reuse existing connection."""
    vc = guild.voice_client

    if not vc or not isinstance(vc, DISCORD_VOICE_CLIENT) or not vc.is_connected():
        vc = await channel.connect(cls=DISCORD_VOICE_CLIENT)

    elif vc.channel != channel:
        # If the voice client is connected to a different channel,
        # disconnect and reconnect
        await vc.disconnect()
        vc = await channel.connect(cls=DISCORD_VOICE_CLIENT)

    return vc


async def check_voice_channel_populated(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
) -> bool:
    """Check if the voice channel has any connected users."""
    if not channel.members:
        await guild.text_channels[0].send(
            "The voice channel is empty. Please add some users to it."
        )
        return False
    return True


async def get_mixer_from_interaction(
    interaction: discord.Interaction,
) -> MultiAudioSource:
    """Get the mixer for the current interaction's guild.

    If the mixer is not already connected, it will attempt to connect to the
    voice channel of the user who triggered the interaction.
    """
    if interaction.guild is None:
        msg = "This command only works in a server."
        raise ValueError(msg)

    vc = interaction.guild.voice_client
    if not vc:
        member = interaction.guild.get_member(interaction.user.id)
        if member and member.voice and member.voice.channel:
            vc = await member.voice.channel.connect(cls=DISCORD_VOICE_CLIENT)
        else:
            await interaction.followup.send(
                "You need to be in a voice channel (or have me already in one)"
                " to trigger a sound.",
                ephemeral=True,
            )
            msg = "You need to be in a voice channel to trigger a sound."
            raise ValueError(msg)

    vc = cast("DISCORD_VOICE_CLIENT", vc)
    mixer = ensure_mixer(vc)

    if not mixer:
        await interaction.followup.send(
            "Failed to connect to the voice channel.", ephemeral=True
        )
        msg = "Failed to connect to the voice channel."
        raise ValueError(msg)

    return mixer


def get_mixer_from_voice_client(
    vc: DISCORD_VOICE_CLIENT,
) -> MultiAudioSource:
    """Get the mixer for the given voice client."""
    mixer = ensure_mixer(vc)

    if not mixer:
        msg = "Failed to connect to the voice channel."
        raise ValueError(msg)

    return mixer


async def get_voice_channel_mixer(
    interaction: discord.Interaction,
) -> tuple[DISCORD_VOICE_CLIENT, MultiAudioSource] | None:
    """Ensure the user is in a voice channel and returns the channel and mixer."""
    if interaction.guild is None:
        await interaction.followup.send(
            "This command can only be used in a server.", ephemeral=True
        )
        return None

    member = interaction.guild.get_member(interaction.user.id)
    if (
        not member
        or not member.voice
        or not isinstance(member.voice.channel, discord.VoiceChannel)
    ):
        await interaction.followup.send("Join a voice channel first.", ephemeral=True)
        return None

    vc = await ensure_connected(
        interaction.guild,
        member.voice.channel,
    )

    mixer = get_mixer_from_voice_client(vc)
    return vc, mixer


async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    """Alert when a voice state is updated."""
    # Detect when a user leaves a voice channel
    if after.channel is None and before.channel is not None:
        logger.info("'%s' left a voice channel '%s'.", member.name, before.channel)

        # Check if there are any human members left in the channel
        non_bot_members = (
            [True for m in before.channel.members if not m.bot]
            if before.channel
            else []
        )

        # If no non-bot members are left, disconnect the bot
        if not any(non_bot_members):
            vc = before.channel.guild.voice_client
            if vc:
                await vc.disconnect(force=True)
                logger.info(
                    "Disconnected from %s as no users are left.", before.channel.name
                )
