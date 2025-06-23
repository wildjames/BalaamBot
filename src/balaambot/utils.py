import concurrent.futures
import json
import logging
from collections.abc import Awaitable
from typing import Any

import redis
import redis.exceptions

from balaambot.config import ADDRESS, DB, PASSWORD, PORT, REDIS_KEY, USE_REDIS, USERNAME

logger = logging.getLogger(__name__)

FUTURES_EXECUTOR = concurrent.futures.ProcessPoolExecutor()

memory_cache: dict[str, dict] = {}

redis_cache = None
if USE_REDIS:
    # Check that we can talk to redis
    try:
        logger.info(
            "Trying to use Redis cache with key '%s' - host '%s:%s'",
            REDIS_KEY,
            ADDRESS,
            PORT,
        )
        redis_cache = redis.Redis(
            host=ADDRESS,
            port=PORT,
            db=DB,
            username=USERNAME,
            password=PASSWORD,
            health_check_interval=20,
        )
        reply = str(redis_cache.echo("OK"))
        logger.info("Tried to talk to redis server - got reply '%s'", reply)

    except (redis.exceptions.AuthenticationError, redis.exceptions.ConnectionError):
        logger.exception("FAILED TO CONNECT TO REDIS - FALLING BACK ON IN-MEMORY CACHE")
        redis_cache = None
        USE_REDIS = False
else:
    logger.info("Using in-memory cache")


async def get_cache(key: str) -> dict[str, Any]:
    """Fetch a dict from the cache. Throw an error if the key is not found.

    Arguments:
        key: The key that the data is stored under

    """
    if redis_cache is not None and USE_REDIS:
        logger.debug("Fetching '%s' from Redis", key)
        serialised = redis_cache.hget(REDIS_KEY, key)

        if isinstance(serialised, Awaitable):
            serialised = await serialised

        if not serialised:
            raise KeyError(key)

        return json.loads(serialised)

    logger.debug("Fetching '%s' from memory", key)
    return memory_cache[key]


async def set_cache(key: str, obj: dict[str, Any]) -> None:
    """Store the given dictionary in the cache under the specified key.

    Arguments:
        key: The cache key under which to store the object.
        obj: The dictionary object to cache.

    """
    if redis_cache is not None and USE_REDIS:
        logger.debug("Caching '%s' to Redis", key)
        serialised = json.dumps(obj)
        redis_cache.hset(REDIS_KEY, key, serialised)
        return

    logger.debug("Caching '%s' to memory", key)
    memory_cache[key] = obj


def sec_to_string(val: float) -> str:
    """Convert a number of seconds to a human-readable string, (-)(HH:)MM:SS."""
    sec_in_hour = 60 * 60
    sign = "-" if val < 0 else ""
    val = abs(val)
    d = ""
    if val >= sec_in_hour:
        d += f"{int(val // sec_in_hour):02d}:"
        val = val % sec_in_hour
    d += f"{int(val // 60):02d}:{int(val % 60):02d}"
    return sign + d
