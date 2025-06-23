# type: ignore
import array
import asyncio
import shutil
from uuid import uuid4
import subprocess
from pathlib import Path

import pytest

import balaambot.audio_handlers.multi_audio_source as mas
from balaambot.audio_handlers.multi_audio_source import (
    MultiAudioSource,
    _mixers,
    ensure_mixer,
)


class Loop:
    def create_task(self, coro):
        pass

class MockVoiceChat:
    def __init__(self):
        self.loop = Loop()


def test_is_opus_returns_false():
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    assert not src.is_opus()


def test_mix_samples_combines_and_clears_tracks():
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)

    # Use small chunk size for testing (8 bytes -> 4 samples)
    src.CHUNK_SIZE = 8
    # Prepare tracks with after_play key for TypedDict
    track1 = {"name": "name1", "id": uuid4(), "samples": array.array("h", [1, -1, 1, -1]), "pos": 0, "after_play": None, "before_play": None}
    track2 = {"name": "name2", "id": uuid4(), "samples": array.array("h", [2, -2, 2, -2]), "pos": 0, "after_play": None, "before_play": None}
    # Assign tracks
    src._tracks = [track1, track2]
    src._sfx = []

    total = src._mix_samples()

    # The sum needs to be an int32 array
    assert isinstance(total, array.array)
    assert total.typecode == "i"
    assert list(total) == [3, -3, 3, -3]

    # Both tracks reached end, so internal _tracks and _sfx should be empty
    assert src._tracks == []
    assert src._sfx == []


def test_read_clips_and_respects_stopped(monkeypatch):
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    src.CHUNK_SIZE = 8

    # Stub mix to produce values outside clip range
    def fake_mix():
        return array.array("i", [100, -100, 0, 10])

    src._mix_samples = fake_mix
    src.MAX_VOLUME = 10
    src.MIN_VOLUME = -10
    src._stopped = False

    # Test normal read with clipping
    data = src.read()
    out = array.array("h")
    out.frombytes(data)
    assert list(out) == [10, -10, 0, 10]

    # Test stopped state returns silence
    src._stopped = True
    silence = src.read()
    assert silence == b""


def test_play_file_success(monkeypatch, tmp_path):
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    src.CHUNK_SIZE = 2

    # Create a dummy file
    dummy = tmp_path / "dummy.wav"
    dummy.write_bytes(b"")

    # Stub ffmpeg detection
    monkeypatch.setattr(shutil, "which", lambda name: "ffmpeg")

    # Stub subprocess.Popen
    class DummyPopen:
        def __init__(self, args, stdout, stderr):
            self.returncode = 0

        def communicate(self):
            return (b"\x01\x00\x02\x00", b"")

    monkeypatch.setattr(subprocess, "Popen", DummyPopen)

    # Patch create_task on vc.loop to record scheduling of functions
    scheduled = []
    monkeypatch.setattr(
        vc.loop, "create_task", lambda coro: scheduled.append(coro) or asyncio.sleep(0)
    )

    def before_play():
        pass

    def after_play():
        pass

    src.play_file(str(dummy), before_play=before_play, after_play=after_play)

    # One track enqueued
    assert len(src._sfx) == 1
    track = src._sfx[0]

    # Samples converted correctly
    assert track["samples"] == array.array("h", [1, 2])
    assert track["pos"] == 0

    # Callbacks not called yet
    assert scheduled == []

    # The before function is called after the first read
    src.read()
    assert len(scheduled) == 1

    # read until the track is done
    while track["pos"] < len(track["samples"]):
        src.read()

    # After reading all samples, callback should be called
    assert len(scheduled) == 2


def test_play_file_ffmpeg_not_found(monkeypatch, tmp_path):
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    dummy = tmp_path / "dummy.wav"
    dummy.write_bytes(b"")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError) as exc:
        src.play_file(str(dummy))
    assert "ffmpeg not found" in str(exc.value)


def test_play_file_file_not_found():
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    with pytest.raises(FileNotFoundError):
        src.play_file("nonexistent.wav")


@pytest.mark.asyncio
async def test_ensure_mixer_creates_and_reuses():
    # Clear existing mixers
    _mixers.clear()

    calls = []

    class DummyGuild:
        def __init__(self, id):
            self.id = id

    class DummyVC:
        def __init__(self, id):
            self.guild = DummyGuild(id)
            self.played = []

        def play(self, source, **kwargs):
            calls.append(source)
            self.played.append(source)

    vc1 = DummyVC(42)
    mixer1 = ensure_mixer(vc1)
    assert isinstance(mixer1, MultiAudioSource)
    assert mixer1 in calls

    # Calling again for same guild should not create a new mixer or call play
    vc2 = DummyVC(42)
    mixer2 = ensure_mixer(vc2)
    assert mixer2 is mixer1
    assert vc2.played == []


@pytest.fixture(autouse=True)
def clear_mixers_and_tracks():
    # Clear global mixer registry and ensure fresh instances
    _mixers.clear()
    yield
    _mixers.clear()


@pytest.mark.asyncio
async def test_ensure_mixer_multiple_guilds(monkeypatch):
    class DummyGuild:
        def __init__(self, id):
            self.id = id

    class DummyVC:
        def __init__(self, id):
            self.guild = DummyGuild(id)
            self.played = []

        def play(self, source, **kwargs):
            self.played.append(source)

    # First guild should create a new mixer and call play()
    vc1 = DummyVC(1)
    mixer1 = ensure_mixer(vc1)
    assert isinstance(mixer1, MultiAudioSource)
    assert _mixers[1] is mixer1
    assert vc1.played == [mixer1]

    # Second guild should get its own mixer
    vc2 = DummyVC(2)
    mixer2 = ensure_mixer(vc2)
    assert mixer2 is not mixer1
    assert _mixers[2] is mixer2
    assert vc2.played == [mixer2]

    # Calling again for guild 1 should reuse and not call play()
    vc1b = DummyVC(1)
    mixer1b = ensure_mixer(vc1b)
    assert mixer1b is mixer1
    assert vc1b.played == []


def test_play_pcm_success(tmp_path):
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    pcm = tmp_path / "track.pcm"
    pcm.write_bytes(b"\x03\x00\x04\x00")

    src.play_pcm(pcm)

    assert len(src._tracks) == 1
    track = src._tracks[0]
    assert track["samples"].tolist() == [3, 4]
    assert track["pos"] == 0
    assert src._stopped is False


def test_play_pcm_file_not_found():
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    with pytest.raises(FileNotFoundError):
        src.play_pcm(Path("/does/not/exist.pcm"))


def test_mix_samples_with_callback_and_padding(monkeypatch):
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)

    # Make CHUNK_SIZE small: 4 bytes => 2 samples
    src.CHUNK_SIZE = 4
    called = []

    # samples length 1, so needs padding for second sample
    samples = array.array("h", [5])

    def cb():
        called.append(True)

    track = {"name": "name", "id": uuid4(), "samples": samples, "pos": 0, "before_play": cb, "after_play": cb}
    src._tracks = [track]
    src._sfx = []

    # Patch create_task on vc.loop to record scheduling of functions
    scheduled = []
    monkeypatch.setattr(
        vc.loop, "create_task", lambda coro: scheduled.append(coro) or asyncio.sleep(0)
    )

    total = src._mix_samples()
    # Should have [5, 0] as int32 array
    assert isinstance(total, array.array) and total.typecode == "i"
    assert total.tolist() == [5, 0]

    # callbacks should have been called once each
    assert len(scheduled) == 2

    # track lists should now be empty
    assert src._tracks == []
    assert src._sfx == []


def test_skip_current_tracks_invokes_callbacks_and_clears():
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    src._stopped = False

    called = []
    # Two tracks with after_play handlers
    t1 = {
        "samples": array.array("h", [1, 2]),
        "pos": 1,
        "after_play": lambda: called.append("a"),
    }
    t2 = {
        "samples": array.array("h", [3, 4, 5]),
        "pos": 2,
        "after_play": lambda: called.append("b"),
    }
    src._tracks = [t1, t2]

    src.skip_current_tracks()
    # All tracks should be removed
    assert src._tracks == []
    # Both callbacks called
    assert set(called) == {"a", "b"}


def test_skip_current_tracks_callback_exceptions_are_swallowed():
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    src._tracks = [
        {
            "samples": array.array("h", [1]),
            "pos": 0,
            "after_play": lambda: (_ for _ in ()).throw(ValueError()),
        }
    ]
    # Should not raise
    src.skip_current_tracks()
    assert src._tracks == []  # track removed despite exception


def test_stop_sfx_and_stop_tracks_and_stop():
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    src._sfx = [1, 2, 3]
    src._tracks = [4, 5, 6]
    src._stopped = False

    # stop_sfx clears only sfx
    src.clear_sfx()
    assert src._sfx == []
    assert src._tracks == [4, 5, 6]

    # stop_tracks clears tracks and sets stopped
    src.clear_tracks()
    assert src._tracks == []
    assert src._stopped is True

    # stop() should invoke both in sequence
    calls = []

    # monkeypatch instance methods
    mas.MultiAudioSource.clear_sfx = lambda self: calls.append("sfx")
    mas.MultiAudioSource.clear_tracks = lambda self: calls.append("tracks")

    vc2 = MockVoiceChat()
    src2 = MultiAudioSource(vc2)
    src2.clear_queue()
    assert calls == ["sfx", "tracks"]


def test_compute_normalisation_factor_max(monkeypatch):
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    src.NORMALISATION_APPROACH = "max"
    src.TARGET_VOLUME = 1.0
    src.MAX_VOLUME = 1.0
    samples = [1, -2, 3, -4]
    track = {"id": "tid", "samples": samples, "name": "t"}
    src._track_norm_factors = {}
    src._compute_normalisation_factor(track)
    # Should use max(abs(samples))
    assert "tid" in src._track_norm_factors


def test_compute_normalisation_factor_std_dev(monkeypatch):
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    src.NORMALISATION_APPROACH = "std_dev"
    src.TARGET_VOLUME = 1.0
    src.MAX_VOLUME = 1.0
    samples = [1, 2, 3, 4]
    track = {"id": "tid2", "samples": samples, "name": "t2"}
    src._track_norm_factors = {}
    src._compute_normalisation_factor(track)
    assert "tid2" in src._track_norm_factors


def test_handle_callback_before_and_after(monkeypatch):
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    called = {}
    def before(): called["before"] = True
    def after(): called["after"] = True
    track = {"before_play": before, "after_play": after, "name": "t"}
    # Patch asyncio.to_thread to call the function immediately
    monkeypatch.setattr("asyncio.to_thread", lambda func: func())
    monkeypatch.setattr(vc.loop, "create_task", lambda coro: coro)
    src.handle_callback(track, "before_play")
    src.handle_callback(track, "after_play")
    assert "before" in called and "after" in called

def test_handle_callback_invalid_which():
    vc = MockVoiceChat()
    src = MultiAudioSource(vc)
    track = {"before_play": None, "after_play": None, "name": "t"}
    with pytest.raises(ValueError):
        src.handle_callback(track, "not_a_valid_type")
