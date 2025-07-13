import logging
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

from balaambot.cats.cat_handler import MAX_CAT_HUNGER, CatHandler

ENABLE_CAT_HUNGRY_MESSAGE = False
MSG_NO_CAT = (
    "You don't have any cats yet! :crying_cat_face: Try adopting one with `/adopt`!"
)
CAT_FEEDS_PER_DAY = 1
# Calculate how often to decrease hunger based on feeds per day
HUNGER_LOOP_TIME = (CAT_FEEDS_PER_DAY * 24 * 60 * 60) / MAX_CAT_HUNGER
# Hunger level at which to notify users
NOTIFICATION_THRESHOLD = 10
# Maximum allowed length for cat names
MAX_CAT_NAME_LENGTH = 32

logger = logging.getLogger(__name__)


class CatCommands(commands.Cog):
    """Slash commands for cat interactions."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the CatCommands cog."""
        self.bot = bot
        self.cat_handler = CatHandler()
        self.hunger_task.start()
        self.feed_notify_task.start()

    async def cog_unload(self) -> None:
        """Stop the hunger task when the cog is unloaded."""
        self.hunger_task.cancel()
        self.feed_notify_task.cancel()

    @tasks.loop(seconds=HUNGER_LOOP_TIME)
    async def hunger_task(self) -> None:
        """Task to decrease the hunger of all cats every minute."""
        self.cat_handler.decrease_hunger()

    @tasks.loop(hours=24)
    async def feed_notify_task(self) -> None:
        """Task to notify users to feed their cats."""
        if not ENABLE_CAT_HUNGRY_MESSAGE:
            logger.info("Daily reminder to feed cats is disabled. Skipping.")
            return
        hungry_cats = self.cat_handler.get_hungry_cats(threshold=NOTIFICATION_THRESHOLD)
        if hungry_cats:
            # Message each user that their cat is hungry
            for user_id in hungry_cats:
                user = await self.bot.fetch_user(user_id)
                if user:
                    logger.debug("Notifying user %d that their cat is hungry", user_id)
                    await user.send(
                        "Bruh, one of your cats is starving! Go feed it! :pouting_cat:"
                    )
                else:
                    logger.warning(
                        "Could not find user with ID %d to notify about hungry cat",
                        user_id,
                    )

    @app_commands.command(name="adopt", description="Adopt a new cat for the server!")
    @app_commands.describe(cat="The name of the cat to adopt")
    async def adopt_cat(self, interaction: discord.Interaction, cat: str) -> None:
        """Creates and saves a new pet cat."""
        logger.info(
            "Received adopt_cat command: %s (cat: %s, guild_id: %d)",
            interaction.user,
            cat,
            interaction.guild_id,
        )

        # cat name length cant be too ridiculous
        if len(cat) > MAX_CAT_NAME_LENGTH:
            await interaction.response.send_message(
                f"Cat names can be at most {MAX_CAT_NAME_LENGTH} characters long. "
                "Please choose a shorter name.",
                ephemeral=True,
            )
            return

        guild_id = 0 if interaction.guild_id is None else interaction.guild_id

        if self.cat_handler.get_cat(cat, guild_id):
            await interaction.response.send_message(
                f"We already have a cat named {cat}!", ephemeral=True
            )
            return

        self.cat_handler.add_cat(cat, guild_id, interaction.user.id)
        await interaction.response.send_message(
            f"<@{interaction.user.id}> adopted a new cat called {cat}! :smile_cat:"
        )

    @app_commands.command(name="feed", description="Feed one of our cats!")
    @app_commands.describe(cat="The name of the cat you want to feed")
    async def feed_cat(self, interaction: discord.Interaction, cat: str) -> None:
        """Feed a cat to increase its hunger level."""
        logger.info(
            "Received feed_cat command from: %s (cat: %s, guild_id: %d)",
            interaction.user,
            cat,
            interaction.guild_id,
        )
        guild_id = 0 if interaction.guild_id is None else interaction.guild_id
        if self.cat_handler.get_num_cats(guild_id) == 0:
            await interaction.response.send_message(MSG_NO_CAT)
            return

        target_cat = self.cat_handler.get_cat(cat, guild_id)
        if target_cat is None:
            await interaction.response.send_message(
                f"We don't have any cats named {cat}. "
                f"We have these:\n{self.cat_handler.get_cat_names(guild_id)}.",
                ephemeral=True,
            )
            return
        msg = self.cat_handler.feed_cat(target_cat, guild_id, interaction.user.id)
        await interaction.response.send_message(msg)

    @app_commands.command(name="pet", description="Try to pet one of our cats!")
    @app_commands.describe(cat="The name of the cat you want to pet")
    async def pet_cat(self, interaction: discord.Interaction, cat: str) -> None:
        """Try to pet a cat with a chance to fail."""
        logger.info(
            "Received pet_cat command from: %s (cat: %s, guild_id: %d)",
            interaction.user,
            cat,
            interaction.guild_id,
        )
        guild_id = 0 if interaction.guild_id is None else interaction.guild_id
        if self.cat_handler.get_num_cats(guild_id) == 0:
            await interaction.response.send_message(MSG_NO_CAT)
            return

        target_cat = self.cat_handler.get_cat(cat, guild_id)
        if target_cat is None:
            await interaction.response.send_message(
                f"We don't have any cats named {cat}. "
                f"We have these:\n{self.cat_handler.get_cat_names(guild_id)}.",
                ephemeral=True,
            )
            return

        success = random.choices([True, False], [3, 1])  # noqa: S311
        if success[0]:
            msg = (
                f"<@{interaction.user.id}> successfully petted {target_cat}! "
                "They love it! :heart_eyes_cat:"
            )
        else:
            msg = (
                f"{target_cat} ran away before <@{interaction.user.id}> could pet them!"
            )
        await interaction.response.send_message(msg)

    @app_commands.command(name="list_cats", description="See all of our cats!")
    async def list_cats(self, interaction: discord.Interaction) -> None:
        """List all of the server's cats."""
        logger.info("Received list_cats command from: %s", interaction.user)
        guild_id = 0 if interaction.guild_id is None else interaction.guild_id
        if self.cat_handler.get_num_cats(guild_id) == 0:
            await interaction.response.send_message(MSG_NO_CAT, ephemeral=True)
            return

        cat_list = self.cat_handler.get_cat_names(guild_id)
        await interaction.response.send_message(
            f"We currently have these cats:\n{cat_list}"
        )

    @app_commands.command(
        name="remove_cat", description="Remove a cat you own from the server."
    )
    @app_commands.describe(cat="The name of the cat to remove")
    async def remove_cat(self, interaction: discord.Interaction, cat: str) -> None:
        """Remove a cat, only if the user is the owner."""
        logger.info(
            "Received remove_cat command: %s (cat: %s, guild_id: %d)",
            interaction.user,
            cat,
            interaction.guild_id,
        )
        guild_id = 0 if interaction.guild_id is None else interaction.guild_id
        success, message = self.cat_handler.remove_cat(
            cat, guild_id, interaction.user.id
        )
        await interaction.response.send_message(message)


async def setup(bot: commands.Bot) -> None:
    """Load the CatCommands cog."""
    logger.info("Loading CatCommands cog")
    await bot.add_cog(CatCommands(bot))
