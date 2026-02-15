import asyncio
import logging
import os
import pathlib

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Discord provides a nice default coloured log format
discord.utils.setup_logging(level=logging.DEBUG)
# Discord gets very spammy on DEBUG
logging.getLogger("discord").setLevel(logging.INFO)
logger = logging.getLogger(__name__)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("!"),
    case_insensitive=True,
    intents=intents,
    description="A bot for the NaughtyBoys",
)


@bot.event
async def on_ready() -> None:
    """Call when the bot is ready; synchronizes slash commands with Discord."""
    await bot.tree.sync()
    logger.info("Logged in as %s", bot.user)


async def load_extensions() -> None:
    """Load all the available bot extensions."""
    commands_path = pathlib.Path(__file__).parent / "bot_commands"
    extension_files = sorted(commands_path.glob("*.py"), key=lambda f: f.name)
    if len(extension_files) == 0:
        logger.fatal(
            "No extensions found to load. Please double check the paths are correct."
        )

    for ext_file in extension_files:
        if ext_file.name != "__init__.py":
            ext_path = f"balaambot.bot_commands.{ext_file.stem}"
            logger.info("Loading extension: %s", ext_path)
            await bot.load_extension(ext_path)


def add_listeners() -> None:
    """Add listeners to the bot."""
    # This needs to be imported here, since it needs the logging and bot to be set up
    # before we can import and set up balaambot code.
    from balaambot.discord_utils import on_voice_state_update  # noqa: PLC0415 RUF100

    bot.add_listener(on_voice_state_update, "on_voice_state_update")

    logger.info("Added listeners to the bot.")


async def main() -> None:
    """Main async process that runs the bot."""
    # Check the token is valid
    if DISCORD_BOT_TOKEN is None or DISCORD_BOT_TOKEN == "":
        msg = "DISCORD_BOT_TOKEN environment variable is not set."
        raise ValueError(msg)
    if '"' in DISCORD_BOT_TOKEN:
        msg = (
            "DISCORD_BOT_TOKEN contains invalid characters. "
            'Do not wrap the token in "..." inside the .env file!'
        )
        raise ValueError(msg)
    logger.info("Starting bot...")
    async with bot:
        # Loads all files in bot_commands
        await load_extensions()
        add_listeners()
        # Start the bot
        await bot.start(DISCORD_BOT_TOKEN)


def start() -> None:
    """Entrypoint for the bot. Starts the main async process."""
    # Bot must be run this way so all tasks run in the same process
    asyncio.run(main())
