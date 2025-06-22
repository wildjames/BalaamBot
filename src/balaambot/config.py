# This file holds all global config variables
# Import the whole file if you need to access any of these
import os
from logging import getLogger
from pathlib import Path

from discord.ext import voice_recv

logger = getLogger(__name__)

DISCORD_VOICE_CLIENT = voice_recv.VoiceRecvClient

# Docker volume to hold all persistent data
PERSISTENT_DATA_DIR = os.getenv("PERSISTENT_DATA_DIR", default="persistent")
logger.info("Using persistent data directory: %s", PERSISTENT_DATA_DIR)

# For youtube authorisation
# This file should be created by the user and contain the cookies for the account
# that will be used to download videos.
COOKIE_FILE = os.getenv("BALAAMBOT_COOKIE_FILE", default=None)
if COOKIE_FILE is None:
    msg = "No cookie file specified. Not using authentication for YouTube."
    logger.warning(msg)
else:
    COOKIE_FILE = Path(COOKIE_FILE)
    if not COOKIE_FILE.is_absolute():
        logger.info(
            "COOKIE_FILE is not absolute, resolving relative to working directory."
        )
        COOKIE_FILE = Path.cwd() / COOKIE_FILE
    if not COOKIE_FILE.exists():
        msg = (
            f"Cookie file '{COOKIE_FILE}' does not exist. Please create it or set"
            " the BALAAMBOT_COOKIE_FILE environment variable."
        )
        raise FileNotFoundError(msg)
    logger.info("Using cookie file: %s", COOKIE_FILE)

USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"
REDIS_KEY = os.getenv("BALAAMBOT_REDIS_HASH_KEY", "balaambot")
ADDRESS = os.getenv("REDIS_ADDRESS", "localhost")
PORT = int(os.getenv("REDIS_PORT", "6379"))
DB = int(os.getenv("REDIS_DB", "0"))
USERNAME = os.getenv("REDIS_USERNAME", None)
PASSWORD = os.getenv("REDIS_PASSWORD", None)
