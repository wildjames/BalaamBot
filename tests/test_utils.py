import json
import pytest

import balaambot.config as config
import balaambot.utils as utils
from balaambot.utils import sec_to_string, get_cache, set_cache, memory_cache


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "00:00"),
        (5, "00:05"),
        (65, "01:05"),
        (3600, "01:00:00"),
        (3665, "01:01:05"),
        (100000, "27:46:40"),
        (-5, "-00:05"),
    ],
)
def test_sec_to_string(seconds, expected):
    assert sec_to_string(seconds) == expected


@pytest.fixture(autouse=True)
def force_memory_cache(monkeypatch):
    """
    Ensure USE_REDIS is False so we use the in-process memory_cache
    and start each test with a clean slate.
    """
    monkeypatch.setattr(utils, "USE_REDIS", False)
    memory_cache.clear()
    yield
    memory_cache.clear()


@pytest.mark.asyncio
async def test_set_and_get_cache_memory():
    config.USE_REDIS = False
    data = {"foo": "bar", "num": 123}

    await set_cache("mykey", data)
    assert "mykey" in memory_cache
    assert memory_cache["mykey"] == data

    result = await get_cache("mykey")
    assert result == data


@pytest.mark.asyncio
async def test_set_overwrites_existing():
    await set_cache("dupkey", {"a": 1})
    new_obj = {"a": 2, "b": 3}
    await set_cache("dupkey", new_obj)
    result = await get_cache("dupkey")
    assert result == new_obj


@pytest.mark.asyncio
async def test_get_cache_missing_key_raises_key_error():
    with pytest.raises(KeyError) as excinfo:
        await get_cache("no_such_key")
    assert "no_such_key" in str(excinfo.value)


# --- Redis-backed cache tests ---

class FakeRedisSync:
    def __init__(self):
        self.store: dict[str, str] = {}
    def hset(self, name, key, val):
        self.store[key] = val
    def hget(self, name, key):
        return self.store.get(key)


class FakeRedisAsync(FakeRedisSync):
    async def hget(self, name, key):
        return super().hget(name, key)


@pytest.mark.asyncio
async def test_redis_set_and_get(monkeypatch):
    # turn on Redis mode and inject fake client
    monkeypatch.setattr(utils, "USE_REDIS", True)
    fake = FakeRedisSync()
    monkeypatch.setattr(utils, "redis_cache", fake)

    data = {"x": 1, "y": "z"}
    await set_cache("rkey", data)

    # underlying store got the JSON
    assert fake.store["rkey"] == json.dumps(data)

    result = await get_cache("rkey")
    assert result == data


@pytest.mark.asyncio
async def test_redis_hget_awaitable(monkeypatch):
    # simulate an awaitable hget()
    monkeypatch.setattr(config, "USE_REDIS", True)
    fake = FakeRedisAsync()
    monkeypatch.setattr(utils, "redis_cache", fake)

    payload = {"a": [1, 2, 3]}
    await set_cache("akey", payload)
    result = await get_cache("akey")

    # Assert round-trip works even when hget is async
    assert result == payload


@pytest.mark.asyncio
async def test_redis_missing_key_raises_value_error(monkeypatch):
    # empty store
    monkeypatch.setattr(config, "USE_REDIS", True)
    fake = FakeRedisSync()
    monkeypatch.setattr(utils, "redis_cache", fake)

    with pytest.raises(KeyError) as excinfo:
        await get_cache("does_not_exist")
    assert "does_not_exist" in str(excinfo.value)
