import array
import logging
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from discord import AudioSource, VoiceClient

from src.audio_handlers.youtube_audio import (
    fetch_audio_pcm,
    get_audio_pcm,
)

logger = logging.getLogger(__name__)

# Keep one mixer per guild
_mixers: dict[int, "MultiAudioSource"] = {}


async def ensure_mixer(vc: VoiceClient) -> "MultiAudioSource":
    """Get or create a MultiAudioSource mixer for the given VoiceClient.

    If a mixer does not already exist for the guild, a new one is instantiated,
    started on the VoiceClient, and stored. Otherwise, the existing mixer
    is returned.

    Args:
        vc: The Discord VoiceClient to attach the mixer to.

    Returns:
        The MultiAudioSource instance for the guild.

    """
    gid = vc.guild.id
    if gid not in _mixers:
        mixer = MultiAudioSource()
        vc.play(mixer, signal_type="music")  # start the background mixer thread
        _mixers[gid] = mixer
        logger.info("Created mixer for guild %s", gid)
    return _mixers[gid]


class Track(TypedDict):
    """A representation of an audio track in the mixer.

    Attributes:
        samples: An array of PCM samples (int16) for playback.
        pos: The current read position in the samples array.
        after_play: Optional callback invoked when playback completes.

    """

    samples: array.array[int]
    pos: int
    after_play: Callable[[], None] | None


class MultiAudioSource(AudioSource):
    """An audio source that mixes multiple PCM tracks and sound effects for Discord.

    This class decodes and buffers audio from files or YouTube URLs, mixes
    them in real time, and provides fixed-size PCM chunks for Discord voice.
    """

    SAMPLE_RATE = 48000  # Sample rate in Hz
    CHANNELS = 2  # Number of audio channels (stereo)
    CHUNK_DURATION = 0.02  # Duration of each chunk in seconds
    BYTE_SIZE = 2  # Bytes per sample (16-bit PCM)
    CHUNK_SIZE = int(SAMPLE_RATE * CHANNELS * BYTE_SIZE * CHUNK_DURATION)
    MIN_VOLUME = -32768
    MAX_VOLUME = 32767

    def __init__(self) -> None:
        """Initialize the mixer, setting up track storage and synchronization."""
        self._lock = threading.Lock()
        self._tracks: list[Track] = []
        self._sfx: list[Track] = []
        self._stopped = False

    def is_opus(self) -> bool:
        """Indicate that output data is raw PCM, not Opus-encoded.

        Returns:
            False always, since this source provides raw PCM bytes.

        """
        return False

    def cleanup(self) -> None:
        """Perform cleanup by clearing all queued tracks and pausing playback."""
        self.clear_queue()

    @property
    def is_stopped(self) -> bool:
        """Query whether the mixer is currently paused or stopped.

        Returns:
            True if playback is paused or no tracks are active; False otherwise.

        """
        return self._stopped

    @property
    def is_playing(self) -> bool:
        """Check if the mixer has any active tracks or sound effects.

        Returns:
            True if there are tracks or sound effects queued; False otherwise.

        """
        return not self._stopped and (self.num_tracks > 0 or self.num_sfx > 0)

    @property
    def num_tracks(self) -> int:
        """Get the number of music tracks currently queued in the mixer.

        Returns:
            The count of active music tracks.

        """
        return len(self._tracks)

    @property
    def num_sfx(self) -> int:
        """Get the number of sound effects currently queued in the mixer.

        Returns:
            The count of active sound effects.

        """
        return len(self._sfx)

    @property
    def num_playback_streams(self) -> int:
        """Get the total number of active tracks and sound effects.

        Returns:
            The sum of music tracks and sound effects currently queued.

        """
        return len(self._tracks) + len(self._sfx)

    def resume(self) -> None:
        """Resume playback if the mixer was paused."""
        logger.info("Resuming MultiAudioSource")
        with self._lock:
            self._stopped = False

    def pause(self) -> None:
        """Pause playback, halting output until resumed."""
        logger.info("Pausing MultiAudioSource")
        with self._lock:
            self._stopped = True

    def _mix_samples(self) -> array.array[int]:
        """Combine PCM data from all active tracks and sound effects.

        Iterates through each track, extracts the next chunk of samples,
        sums them into an accumulator buffer (int32 to prevent overflow),
        advances track positions, and invokes any completion callbacks.

        Returns:
            An array of mixed 32-bit sample sums for the next output chunk.

        """
        total = array.array("i", [0] * (self.CHUNK_SIZE // 2))
        new_tracks: list[Track] = []
        new_sfx: list[Track] = []

        for track in self._tracks + self._sfx:
            samples = track["samples"]
            pos = track["pos"]
            end = pos + (self.CHUNK_SIZE // 2)

            if end > len(samples):
                pad = array.array("h", [0] * (end - len(samples)))
                samples.extend(pad)

            chunk = samples[pos:end]
            for i, s in enumerate(chunk):
                total[i] += s
            track["pos"] = end

            if end >= len(samples):
                callback = track.get("after_play")
                if callback:
                    try:
                        logger.info("Calling after_play callback for track")
                        callback()
                    except Exception:
                        logger.exception("Error in after_play callback")
            else:
                if track in self._tracks:
                    new_tracks.append(track)
                if track in self._sfx:
                    new_sfx.append(track)

        self._tracks = new_tracks
        self._sfx = new_sfx
        return total

    def read(self) -> bytes:
        """Provide the next PCM audio chunk for Discord to send.

        This is invoked periodically by discord.py (approximately every
        20ms). If playback is paused, returns an empty byte string.

        Returns:
            A bytes object of length CHUNK_SIZE containing 16-bit PCM data.

        """
        if self.is_stopped:
            logger.info("STOPPED")
            return b""

        with self._lock:
            mixed = self._mix_samples()

        out = array.array("h", [0] * (self.CHUNK_SIZE // 2))
        for i, val in enumerate(mixed):
            if val > self.MAX_VOLUME:
                out[i] = self.MAX_VOLUME
            elif val < self.MIN_VOLUME:
                out[i] = self.MIN_VOLUME
            else:
                out[i] = val

        return out.tobytes()

    def clear_queue(self) -> None:
        """Stop all playback and clear both music tracks and sound effects."""
        logger.info("Stopping MultiAudioSource")
        self.clear_sfx()
        self.clear_tracks()

    def clear_sfx(self) -> None:
        """Remove all queued sound effects immediately."""
        logger.info("Stopping all sound effects")
        with self._lock:
            self._sfx.clear()
            logger.info("All sound effects stopped")

    def clear_tracks(self) -> None:
        """Remove all queued music tracks and pause playback."""
        logger.info("Stopping all tracks")
        with self._lock:
            self._tracks.clear()
            logger.info("All tracks stopped")
        self.pause()

    async def play_youtube(
        self,
        url: str,
        username: str | None = None,
        password: str | None = None,
        after_play: Callable[[], None] | None = None,
    ) -> None:
        """Decode and queue audio from a YouTube URL for playback.

        Downloads or retrieves cached PCM data for the given URL, converts
        it into a sample array, and appends it to the mixer queue.

        Args:
            url: The YouTube video URL to play.
            username: Optional YouTube account username for private content.
            password: Optional YouTube account password.
            after_play: Optional callback to invoke when playback ends.

        Raises:
            RuntimeError: If the PCM cache is missing after decoding.

        """
        logger.info("Queueing YouTube %s", url)

        await fetch_audio_pcm(
            url,
            sample_rate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            username=username,
            password=password,
        )
        pcm = get_audio_pcm(url)
        if pcm is None:
            msg = f"Cached file for {url} missing"
            raise RuntimeError(msg)

        samples = array.array("h")
        samples.frombytes(pcm)

        with self._lock:
            self._tracks.append(
                {"samples": samples, "pos": 0, "after_play": after_play}
            )

        logger.info("Loaded data for URL: %s", url)
        logger.info("Now %d tracks in mixer", len(self._tracks))
        self.resume()

    def play_file(
        self, filename: str, after_play: Callable[[], None] | None = None
    ) -> None:
        """Decode an audio file via ffmpeg and enqueue it for mixing.

        Uses ffmpeg to convert the specified file into 16-bit 48kHz stereo PCM,
        reads the output, and adds the samples to the mixer queue.

        Args:
            filename: Path to the audio file to play.
            after_play: Optional callback invoked when the file finishes playing.

        Raises:
            FileNotFoundError: If the file does not exist.
            RuntimeError: If ffmpeg is not installed or decoding fails.

        """
        logger.info("Playing file %s", filename)

        if not Path(filename).is_file():
            msg = f"{filename!r} does not exist"
            raise FileNotFoundError(msg)

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            msg = "ffmpeg not found in PATH"
            raise RuntimeError(msg)

        proc = subprocess.Popen(  # noqa: S603 We're safe here
            [
                ffmpeg_path,
                "-v",
                "quiet",
                "-i",
                filename,
                "-f",
                "s16le",
                "-ar",
                str(self.SAMPLE_RATE),
                "-ac",
                str(self.CHANNELS),
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        pcm_data, err = proc.communicate()
        if proc.returncode != 0:
            msg = f"ffmpeg failed: {err.decode(errors='ignore')}"
            raise RuntimeError(msg)

        samples = array.array("h")
        samples.frombytes(pcm_data)

        with self._lock:
            self._sfx.append({"samples": samples, "pos": 0, "after_play": after_play})

        self.resume()
        logger.info("There are now %d tracks in the mixer", len(self._sfx))

    def skip_current_tracks(self) -> None:
        """Immediately end playback of all current tracks and trigger callbacks.

        Removes all queued music tracks, sets their positions to the end to
        ensure any after_play callbacks are invoked, then calls each callback.
        """
        with self._lock:
            logger.info("Skipping current tracks")
            while self._tracks:
                track = self._tracks.pop(0)
                track["pos"] = len(track["samples"])
                callback = track.get("after_play")
                if callback:
                    try:
                        logger.info("Calling after_play callback for skipped track")
                        callback()
                    except Exception:
                        logger.exception(
                            "Error in after_play callback for skipped track"
                        )
