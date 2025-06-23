# type: ignore
import asyncio
import random
import uuid

import pytest

import balaambot.sfx.audio_sfx_jobs as sfx
from balaambot import discord_utils


@pytest.fixture(autouse=True)
def clear_jobs(monkeypatch):
    # Clear loop_jobs before each test
    sfx.loop_jobs.clear()
    yield
    sfx.loop_jobs.clear()


class DummyGuild:
    def __init__(self, id, vc=None):
        self.id = id
        self.voice_client = vc


class DummyVC:
    def __init__(self, guild_id, connected=True):
        self.guild = DummyGuild(guild_id)
        self._connected = connected
        self.disconnect_called = False
        self.loop = asyncio.get_event_loop()

    def is_connected(self):
        return self._connected

    async def disconnect(self, force=False):
        self.disconnect_called = True
        self._connected = False

    async def connect(self):
        self._connected = True
        return self


class DummyChannel:
    def __init__(self, vc):
        self._vc = vc

    async def connect(self, cls=DummyVC):
        return self._vc


class DummyLogger:
    def __init__(self):
        self.last_msg = None
        self.last_args = None

    def info(self, msg, *args, **kwargs):
        self.last_msg = msg
        self.last_args = args


@pytest.mark.asyncio
async def test_add_and_remove_job_disconnect(monkeypatch):
    # Setup dummy voice client
    vc = DummyVC(guild_id=1, connected=True)

    # add_job
    job_id = await sfx.add_job(vc, sound="sfx.wav", min_interval=0.1, max_interval=0.2)
    assert job_id in sfx.loop_jobs
    stored_vc, task, sound, mi, ma = sfx.loop_jobs[job_id]
    assert stored_vc is vc
    assert sound == "sfx.wav"
    assert mi == 0.1
    assert ma == 0.2

    # simulate no other jobs, remove
    await sfx.remove_job(job_id)
    assert job_id not in sfx.loop_jobs


@pytest.mark.asyncio
async def test_remove_job_not_found():
    with pytest.raises(KeyError):
        await sfx.remove_job("nonexistent")


@pytest.mark.asyncio
async def test_ensure_connected_existing(monkeypatch):
    # Case 1: existing connected vc
    vc = DummyVC(guild_id=2, connected=True)
    guild = DummyGuild(id=2, vc=vc)
    channel = DummyChannel(vc)
    out = await discord_utils.ensure_connected(guild, channel)
    assert out is vc

    # Case 2: no vc
    guild2 = DummyGuild(id=3, vc=None)
    new_vc = DummyVC(guild_id=3, connected=False)
    channel2 = DummyChannel(new_vc)
    out2 = await discord_utils.ensure_connected(guild2, channel2)
    assert out2 is new_vc


@pytest.mark.asyncio
async def test_play_sfx_loop_client_not_connected(monkeypatch):
    # Setup dummy VC that's not connected
    vc = DummyVC(guild_id=4, connected=False)
    job_id = uuid.uuid4().hex

    # Insert a real dummy task so remove_job can cancel it cleanly
    dummy_task = asyncio.get_event_loop().create_task(asyncio.sleep(0))
    sfx.loop_jobs[job_id] = (vc, dummy_task, "sound.wav", 0.0, 0.0)

    # Run loop
    # Should remove job due to not connected
    await sfx._play_sfx_loop(vc, job_id)
    assert job_id not in sfx.loop_jobs


@pytest.mark.asyncio
async def test_play_sfx_loop_play_error(monkeypatch):
    # Setup dummy VC always connected
    vc = DummyVC(guild_id=5, connected=True)
    job_id = uuid.uuid4().hex

    # Insert a real dummy task
    dummy_task = asyncio.get_event_loop().create_task(asyncio.sleep(0))
    sfx.loop_jobs[job_id] = (vc, dummy_task, "sound.wav", 0.0, 0.0)

    # Patch random.uniform to zero wait
    monkeypatch.setattr(random, "uniform", lambda a, b: 0.0)

    # Patch asyncio.sleep to no-op
    orig_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda x, _orig=orig_sleep: _orig(0))

    # Patch utils.get_mixer_from_voice_client to throw
    async def fake_get_mixer(vc_in):
        raise RuntimeError("fail")

    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", fake_get_mixer)

    await sfx._play_sfx_loop(vc, job_id)

    # Should remove job due to error
    assert job_id not in sfx.loop_jobs


@pytest.mark.asyncio
async def test_play_sfx_loop_success_one_iteration(monkeypatch):
    # Setup dummy VC always connected
    vc = DummyVC(guild_id=6, connected=True)
    job_id = uuid.uuid4().hex

    # Create simple mixer that plays immediately
    class DummyMixer:
        def __init__(self):
            self.played = []

        async def play_file(self, sound, after_play=None):
            # simulate immediate playback
            if after_play:
                after_play()

    dummy_mixer = DummyMixer()

    # Insert a real dummy task so remove_job can cancel it
    dummy_task = asyncio.get_event_loop().create_task(asyncio.sleep(0))
    sfx.loop_jobs[job_id] = (vc, dummy_task, "sound.wav", 0.0, 0.0)

    # Patch random.uniform and sleep
    monkeypatch.setattr(random, "uniform", lambda a, b: 0.0)
    orig_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda x, _orig=orig_sleep: _orig(0))

    # Patch utils.get_mixer_from_voice_client
    async def fake_get_mixer(vc_in):
        return dummy_mixer

    monkeypatch.setattr(discord_utils, "get_mixer_from_voice_client", fake_get_mixer)

    # To exit after one iteration, remove job within after_play
    original_after = dummy_mixer.play_file

    async def wrapped_play_file(sound, after_play=None):
        # call original then schedule removal
        await original_after(sound, after_play)
        # remove job to break loop
        asyncio.get_event_loop().create_task(sfx.remove_job(job_id))

    dummy_mixer.play_file = wrapped_play_file

    # Run loop
    task = asyncio.create_task(sfx._play_sfx_loop(vc, job_id))
    await asyncio.wait_for(task, timeout=1)
    assert job_id not in sfx.loop_jobs


@pytest.fixture(autouse=True)
def clear_loop_jobs():
    """Ensure loop_jobs is empty before/after each test."""
    sfx.loop_jobs.clear()
    yield
    sfx.loop_jobs.clear()


@pytest.mark.asyncio
async def test_play_sfx_loop_job_not_found(monkeypatch):
    """If the job_id isn't in loop_jobs at the start, we should log and exit cleanly."""
    vc = DummyVC(guild_id=42, connected=True)
    dummy_logger = DummyLogger()
    monkeypatch.setattr(sfx, "logger", dummy_logger)

    # Ensure loop_jobs is empty and call the loop
    await sfx._play_sfx_loop(vc, "no-such-job")

    assert dummy_logger.last_msg == "SFX job %s not found in guild_id=%s"
    assert dummy_logger.last_args == ("no-such-job", vc.guild.id)


@pytest.mark.asyncio
async def test_play_sfx_loop_cancelled(monkeypatch):
    """If asyncio.sleep raises CancelledError, the loop should log cancellation and re-raise."""
    vc = DummyVC(guild_id=99, connected=True)
    job_id = uuid.uuid4().hex

    # insert a dummy task so remove_job can clean up if needed
    dummy_task = asyncio.get_event_loop().create_task(asyncio.sleep(0))
    sfx.loop_jobs[job_id] = (vc, dummy_task, "dummy.wav", 0.0, 0.0)

    dummy_logger = DummyLogger()
    monkeypatch.setattr(sfx, "logger", dummy_logger)

    # Force sleep to immediately raise CancelledError
    def fake_sleep(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await sfx._play_sfx_loop(vc, job_id)

    assert dummy_logger.last_msg == "SFX job %s cancelled in guild_id=%s"
    assert dummy_logger.last_args == (job_id, vc.guild.id)


@pytest.mark.asyncio
async def test_stop_all_jobs_calls_remove_only_for_target_vc(monkeypatch):
    """stop_all_jobs should invoke remove_job() exactly for those jobs whose vc matches."""
    vc1 = DummyVC(guild_id=1)
    vc2 = DummyVC(guild_id=2)

    # create three fake jobs
    t1 = asyncio.get_event_loop().create_task(asyncio.sleep(0))
    t2 = asyncio.get_event_loop().create_task(asyncio.sleep(0))
    t3 = asyncio.get_event_loop().create_task(asyncio.sleep(0))

    sfx.loop_jobs["job1"] = (vc1, t1, "a.wav", 0.1, 0.2)
    sfx.loop_jobs["job2"] = (vc1, t2, "b.wav", 0.1, 0.2)
    sfx.loop_jobs["job3"] = (vc2, t3, "c.wav", 0.1, 0.2)

    called = []

    async def fake_remove_job(jid):
        called.append(jid)

    monkeypatch.setattr(sfx, "remove_job", fake_remove_job)

    await sfx.stop_all_jobs(vc1)

    # Only the two jobs for vc1 should have been passed to remove_job
    assert set(called) == {"job1", "job2"}
    # And job3 should remain untouched in loop_jobs
    assert "job3" in sfx.loop_jobs
