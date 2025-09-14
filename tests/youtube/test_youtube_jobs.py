# type: ignore
import asyncio

import pytest
from pathlib import Path

import balaambot.youtube.jobs as ytj
from balaambot import discord_utils, utils


class DummyGuild:
    def __init__(self, id):
        self.id = id


class DummyVC:
    def __init__(self, guild_id):
        self.guild = DummyGuild(guild_id)
        self.loop = asyncio.get_event_loop()
        # override run_in_executor or create_task per‐test

    def play(self, *args, **kwargs):
        self.playing = True


class DummyLogger:
    def __init__(self):
        self.infos = []
        self.errors = []
        self.exceptions = []
        self.warnings = []

    def info(self, msg, *args, **kwargs):
        self.infos.append((msg, args))

    def error(self, msg, *args, **kwargs):
        self.errors.append((msg, args))

    def exception(self, msg, *args, **kwargs):
        self.exceptions.append((msg, args))

    def warning(self, msg, *args, **kwargs):
        self.warnings.append((msg, args))


@pytest.fixture(autouse=True)
def clear_queue():
    """Ensure queue is empty between tests."""
    ytj.youtube_queue.clear()
    yield
    ytj.youtube_queue.clear()


@pytest.fixture
def dummy_logger(monkeypatch):
    """Replace module logger with a dummy one."""
    log = DummyLogger()
    monkeypatch.setattr(ytj, "logger", log)
    return log


@pytest.mark.asyncio
async def test_add_to_queue_first_item_schedules_playback(monkeypatch, dummy_logger):
    vc = DummyVC(10)
    recorded = []

    # record create_task calls
    orig_create = vc.loop.create_task
    def fake_create_task(coro):
        recorded.append(coro)
        # Return a real Task so the loop remains healthy
        return orig_create(coro)
    monkeypatch.setattr(vc.loop, "create_task", fake_create_task, raising=False)

    # record prefetch metadata calls
    meta_called = []
    def fake_run(ex, func, logger, url):
        meta_called.append((ex, func, logger, url))
    monkeypatch.setattr(vc.loop, "run_in_executor", fake_run, raising=False)

    await ytj.add_to_queue(vc, ["yt://video1"])

    # queue updated
    assert ytj.youtube_queue[10] == ["yt://video1"]
    # play_next scheduled once
    assert len(recorded) == 1
    assert asyncio.iscoroutine(recorded[0])
    # metadata fetch scheduled
    assert len(meta_called) == 1
    ex, func, logger_arg, url = meta_called[0]
    assert ex is utils.FUTURES_EXECUTOR
    assert func is ytj.get_metadata
    assert logger_arg is dummy_logger
    assert url == "yt://video1"


@pytest.mark.asyncio
async def test_add_to_queue_subsequent_items_do_not_schedule(monkeypatch, dummy_logger):
    vc = DummyVC(11)
    # first enqueue: schedule
    orig_create = vc.loop.create_task
    monkeypatch.setattr(vc.loop, "create_task", lambda c: orig_create(c), raising=False)
    monkeypatch.setattr(vc.loop, "run_in_executor", lambda *args: None, raising=False)
    await ytj.add_to_queue(vc, ["first"])

    scheduled = False
    def fail_create_task(_):
        nonlocal scheduled
        scheduled = True
        return orig_create(asyncio.sleep(0))
    monkeypatch.setattr(vc.loop, "create_task", fail_create_task, raising=False)
    monkeypatch.setattr(vc.loop, "run_in_executor", lambda *args: None, raising=False)

    await ytj.add_to_queue(vc, ["second"])

    assert ytj.youtube_queue[11] == ["first", "second"]
    assert not scheduled


@pytest.mark.asyncio
async def test_add_to_queue_create_task_fails_clears_queue(monkeypatch):
    vc = DummyVC(12)
    def bad_create_task(_):
        raise RuntimeError("no loop")

    with pytest.raises(RuntimeError):
        vc.loop.create_task = bad_create_task
        vc.loop.run_in_executor = lambda *args: None

        await ytj.add_to_queue(vc, ["failyt"])
    assert 12 not in ytj.youtube_queue


@pytest.mark.asyncio
async def test_play_next_no_queue(dummy_logger):
    vc = DummyVC(20)
    # no queue at all
    await ytj._play_next(vc)
    assert 20 not in ytj.youtube_queue
    # should have logged "No more tracks"
    assert any("No more tracks" in msg for msg, _ in dummy_logger.infos)


@pytest.mark.asyncio
async def test_play_next_success(monkeypatch, dummy_logger):
    vc = DummyVC(21)
    url = "yt://abc"
    ytj.youtube_queue[21] = [url]

    # stub out metadata fetch
    monkeypatch.setattr(ytj, "get_youtube_track_metadata", lambda u: asyncio.sleep(0))
    # dummy mixer
    class DummyMixer:
        def __init__(self):
            self.played = []

        def play_pcm(self, play_file, *, before_play=None, after_play=None):
            self.played.append(play_file)
            assert play_file == "/tmp/test.pcm"
            if after_play:
                after_play()

    mixer = DummyMixer()
    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: mixer)
    monkeypatch.setattr(ytj, "fetch_audio_pcm", lambda *a, **k: Path("/tmp/test.pcm"))

    await ytj._play_next(vc)
    # queue emptied
    assert 21 not in ytj.youtube_queue


@pytest.mark.asyncio
async def test_play_next_mixer_failure(dummy_logger, monkeypatch):
    vc = DummyVC(22)
    ytj.youtube_queue[22] = ["badurl"]
    monkeypatch.setattr(ytj, "get_youtube_track_metadata", lambda u: asyncio.sleep(0))
    # mixer factory raises
    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: (_ for _ in ()).throw(RuntimeError("mixer bad")))

    await ytj._play_next(vc)
    assert 22 not in ytj.youtube_queue
    assert dummy_logger.exceptions, "Expected exception log for mixer failure"


@pytest.mark.asyncio
async def test_play_next_play_pcm_raises(dummy_logger, monkeypatch):
    vc = DummyVC(23)
    ytj.youtube_queue[23] = ["url23"]
    monkeypatch.setattr(ytj, "get_youtube_track_metadata", lambda u: asyncio.sleep(0))

    class Mixer:
        def play_pcm(self, *_args, **_kwargs):
            raise RuntimeError("play error")
    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: Mixer())

    await ytj._play_next(vc)
    assert 23 not in ytj.youtube_queue
    assert dummy_logger.exceptions


@pytest.mark.asyncio
async def test_get_current_track_empty():
    vc = DummyVC(30)
    assert ytj.get_current_track(vc) is None


@pytest.mark.asyncio
async def test_get_current_track_non_empty():
    vc = DummyVC(31)
    ytj.youtube_queue[31] = ["first", "second"]
    assert ytj.get_current_track(vc) == "first"


@pytest.mark.asyncio
async def test_skip_success(monkeypatch, dummy_logger):
    vc = DummyVC(40)
    class Mixer:
        def __init__(self):
            self.skipped = False
        def skip_current_tracks(self):
            self.skipped = True
    mixer = Mixer()
    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: mixer)
    await ytj.skip(vc)
    assert mixer.skipped


@pytest.mark.asyncio
async def test_skip_raises(monkeypatch, dummy_logger):
    vc = DummyVC(41)
    class Mixer:
        def skip_current_tracks(self):
            raise RuntimeError("skip fail")
    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: Mixer())
    await ytj.skip(vc)
    assert dummy_logger.exceptions


@pytest.mark.asyncio
async def test_clear_and_list_queue():
    vc = DummyVC(50)
    ytj.youtube_queue[50] = ["a", "b", "c"]
    await ytj.clear_queue(vc)
    # only first remains
    assert ytj.youtube_queue[50] == ["a"]
    result = await ytj.list_queue(vc)
    assert result == ["a"]


@pytest.mark.asyncio
async def test_list_queue_with_items():
    vc = DummyVC(51)
    ytj.youtube_queue[51] = ["x", "y"]
    result = await ytj.list_queue(vc)
    assert result == ["x", "y"]


@pytest.mark.asyncio
async def test_stop_success(monkeypatch, dummy_logger):
    vc = DummyVC(60)
    ytj.youtube_queue[60] = ["one", "two"]
    class Mixer:
        def __init__(self):
            self.stopped = False
        def clear_tracks(self):
            self.stopped = True
        def pause(self):
            raise RuntimeError("pause fail")
    mixer = Mixer()
    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: mixer)
    await ytj.stop(vc)
    assert mixer.stopped
    assert 60 not in ytj.youtube_queue
    assert dummy_logger.exceptions


@pytest.mark.asyncio
async def test_stop_clear_fails_but_queue_removed(monkeypatch, dummy_logger):
    vc = DummyVC(61)
    ytj.youtube_queue[61] = ["one"]
    class Mixer:
        def clear_tracks(self):
            raise RuntimeError("clear fail")
        def pause(self):
            pass
    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: Mixer())
    await ytj.stop(vc)
    assert 61 not in ytj.youtube_queue
    assert dummy_logger.exceptions


@pytest.mark.asyncio
async def test_maybe_preload_skips_cached(monkeypatch, dummy_logger):
    vc = DummyVC(70)
    queue = ["url1", "url2", "url3"]
    class Mixer:
        SAMPLE_RATE = 48000
        CHANNELS = 2
    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: Mixer())
    # url2 & url3 are already cached
    class Path:
        def __init__(self, exists): self._exists = exists
        def exists(self): return self._exists
    cache_map = {"url2": Path(True), "url3": Path(True)}
    monkeypatch.setattr(ytj, "get_cache_path", lambda url, sr, ch: cache_map.get(url, Path(False)))
    called = []
    async def fake_run(ex, func, url, opus_tmp, pcm_tmp, cache_path, sr, ch):
        called.append(url)
    vc.loop.run_in_executor = fake_run
    await ytj._maybe_preload_next_tracks(vc, queue)
    assert called == []


@pytest.mark.asyncio
async def test_maybe_preload_download_and_remove_on_failure(monkeypatch, dummy_logger):
    vc = DummyVC(71)
    queue = ["url1", "url2", "url3"]

    # Mixer stub
    class Mixer:
        SAMPLE_RATE = 44100
        CHANNELS = 1

    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", lambda vc: Mixer())
    # Never cached
    monkeypatch.setattr(
        ytj, "get_cache_path",
        lambda url, sr, ch: type("P", (), {"exists": lambda self: False})()
    )
    monkeypatch.setattr(ytj, "get_temp_paths", lambda url: ("opus_tmp", "pcm_tmp"))

    recorded = []

    async def fake_run(executor, func, *args):
        # args == (logger, url, opus_tmp, pcm_tmp, cache_path, sr, ch)
        _, url_arg, *_ = args
        if url_arg == "url2":
            raise Exception("fail")
        recorded.append(url_arg)

    vc.loop.run_in_executor = fake_run

    await ytj._maybe_preload_next_tracks(vc, queue)

    # url2 should get removed on failure
    assert "url2" not in queue
    # url3 should still have been attempted
    assert "url3" in recorded


@pytest.mark.asyncio
async def test_create_before_after_functions_with_metadata(monkeypatch, dummy_logger):
    vc = DummyVC(80)
    url1, url2 = "yt://1", "yt://2"
    ytj.youtube_queue[80] = [url1, url2]
    # stub out metadata retrieval
    async def fake_get_meta(u):
        return {"title": "T1", "url": "u1"}

    monkeypatch.setattr(ytj, "get_youtube_track_metadata", fake_get_meta)
    # fake text channel
    class TextChannel:
        def __init__(self): self.sent = []
        def send(self, content):
            self.sent.append(content)
            return asyncio.sleep(0)
    channel = TextChannel()
    vc.guild.get_channel = lambda id: channel

    calls: list[asyncio.Task] = []
    orig_create = vc.loop.create_task

    def run_task(coro, *args, **kwargs):
        task = orig_create(coro, *args, **kwargs)
        calls.append(task)
        return task

    vc.loop.create_task = run_task

    before, after = ytj.create_before_after_functions(url1, vc, text_channel=100)
    before()
    await asyncio.gather(*calls)
    # metadata‐based message sent
    assert channel.sent and "Now playing" in channel.sent[0]

    before_calls = len(calls)
    after()
    # queue shifted
    assert ytj.youtube_queue[80] == [url2]
    # next scheduled
    assert len(calls) > before_calls


@pytest.mark.asyncio
async def test_create_before_after_functions_without_metadata(monkeypatch, dummy_logger):
    vc = DummyVC(82)
    url = "yt://noinfo"
    ytj.youtube_queue[82] = [url]
    # fetch returns None
    async def fake_get_meta(u):
        return None

    monkeypatch.setattr(ytj, "get_youtube_track_metadata", fake_get_meta)
    class TextChannel:
        def __init__(self): self.sent = []
        def send(self, content):
            self.sent.append(content)
            return asyncio.sleep(0)
    channel = TextChannel()
    vc.guild.get_channel = lambda id: channel
    before, _ = ytj.create_before_after_functions(url, vc, text_channel=123)
    before()
    await asyncio.sleep(0)
    # fallback message sent
    assert channel.sent and "Now playing next track" in channel.sent[0]


@pytest.mark.asyncio
async def test_after_play_finishes_queue(monkeypatch, dummy_logger):
    vc = DummyVC(81)
    url = "yt://finish"
    ytj.youtube_queue[81] = [url]
    class TextChannel:
        def __init__(self): self.sent = []
        def send(self, content):
            self.sent.append(content)
            return asyncio.sleep(0)
    channel = TextChannel()
    vc.guild.get_channel = lambda id: channel
    calls: list[asyncio.Task] = []
    orig_create = vc.loop.create_task

    def run_task(coro, *args, **kwargs):
        task = orig_create(coro, *args, **kwargs)
        calls.append(task)
        return task

    vc.loop.create_task = run_task
    _, after = ytj.create_before_after_functions(url, vc, text_channel=200)
    after()
    await asyncio.sleep(0)
    assert 81 not in ytj.youtube_queue
    assert channel.sent and "Finished playing queue!" in channel.sent[0]


@pytest.mark.asyncio
async def test_before_after_no_text_channel(dummy_logger):
    vc = DummyVC(90)
    ytj.youtube_queue[90] = ["u"]
    before, after = ytj.create_before_after_functions("u", vc, text_channel=None)
    before()
    after()
