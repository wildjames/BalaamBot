import array
import asyncio
import logging
import shutil
import threading
import uuid
from collections.abc import Callable
from math import sqrt
from pathlib import Path
from typing import TypedDict

from discord import AudioSource

from balaambot.config import DISCORD_VOICE_CLIENT

logger = logging.getLogger(__name__)

# Keep one mixer per guild
_mixers: dict[int, "MultiAudioSource"] = {}


def ensure_mixer(vc: DISCORD_VOICE_CLIENT) -> "MultiAudioSource":
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
        mixer = MultiAudioSource(vc=vc)
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

    id: uuid.UUID
    name: str
    samples: array.array[int]
    pos: int
    before_play: Callable[[], None] | None
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

    # Audio normalisation
    # The intent here is to make loud and quiet tracks somewhat more consistent,
    # so that users don't either miss a track or get jumped by a loud one. This is
    # not intended to be any kind of improvement on perceived quality!
    TARGET_VOLUME: float = 0.997
    # "max" or "std_dev"
    NORMALISATION_APPROACH = "std_dev"

    def __init__(
        self, vc: DISCORD_VOICE_CLIENT, *, normalise_audio: bool = False
    ) -> None:
        """Initialize the mixer, setting up track storage and synchronization."""
        self.vc = vc

        self._lock = threading.Lock()

        self._tracks: list[Track] = []
        self._sfx: list[Track] = []

        self._stopped = True

        # use track id as the hash key
        self._track_norm_factors: dict[uuid.UUID, float] = {}
        self.normalise_audio = normalise_audio

    def is_opus(self) -> bool:
        """Indicate that output data is raw PCM, not Opus-encoded.

        TODO: We should probably have an option to return Opus.

        Returns:
            False always, since this source provides raw PCM bytes.

        """
        return False

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

    def _compute_normalisation_factor(self, track: Track) -> None:
        """Compute and store the normalisation factor for a track.

        The normalisation factor is the multiplication factor required to bring it
        to the desired average volume. Stores the factor in the internal
        _track_norm_factors dictionary

        There are some options for normalisation type:
        - "max" will scale the track so that the max volume is the target value.
        - "std_dev" will scale it so that the 3 sigma amplitude is the target volume.

        Arguments:
                   track: The track to compute

        """
        factor = 1.0
        if self.NORMALISATION_APPROACH == "max":
            # Values can be positive or negative, so look at the abs
            factor = max([abs(s) for s in track["samples"]])

        if self.NORMALISATION_APPROACH == "std_dev":
            mean_sample = sum(track["samples"]) / len(track["samples"])
            mu = sum([(s - mean_sample) ** 2 for s in track["samples"]])
            std_dev = sqrt(mu / len(track["samples"]))
            factor = 3 * std_dev

        factor = self.TARGET_VOLUME * self.MAX_VOLUME / factor

        self._track_norm_factors[track["id"]] = factor
        logger.info(
            (
                "pre-computed a volume normalisation factor for track '%s': %.3f."
                " Max amplitude was %d."
                " Max amplitude will now be %d."
            ),
            track["name"],
            factor,
            max(track["samples"]),
            max([int(s * factor) for s in track["samples"]]),
        )

    def handle_callback(self, track: Track, which: str) -> None:
        """Execute the before or after callback for a track.

        Arguments:
            track: The track to process
            which: either "before_play" or "after_play"

        """
        if which == "before_play":
            callback = track["before_play"]
        elif which == "after_play":
            callback = track["after_play"]
        else:
            msg = "which must be either before_play or after_play"
            raise ValueError(msg)

        if callback:
            # schedule the coroutine on the bot's loop without blocking this thread
            try:
                job = asyncio.to_thread(callback)
                self.vc.loop.create_task(job)
            except Exception:
                logger.exception(
                    "Failed to schedule %s callback for track %s", which, track["name"]
                )

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

            if pos == 0:
                self.handle_callback(track, "before_play")

            if end > len(samples):
                pad = array.array("h", [0] * (end - len(samples)))
                samples.extend(pad)

            norm_factor = 1
            if self.normalise_audio and track["id"] in self._track_norm_factors:
                norm_factor = self._track_norm_factors[track["id"]]

            chunk = samples[pos:end]
            for i, s in enumerate(chunk):
                total[i] += (
                    # Clamp to within MIN and MAX
                    max(self.MIN_VOLUME, min(int(s * norm_factor), self.MAX_VOLUME))
                )
            track["pos"] = end

            if end >= len(samples):
                # Finished with playback on this track.
                self.handle_callback(track, "after_play")
                # Clean up the normalisation factors dictionary.
                self._track_norm_factors.pop(track["id"], None)
            else:
                # Continue playing
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

    def play_pcm(
        self,
        file_path: Path,
        before_play: Callable[[], None] | None = None,
        after_play: Callable[[], None] | None = None,
    ) -> None:
        """Queue a pre-converted PCM file for playback as a music track.

        No checks are done on file format - it's assumed to be the correct format.

        Arguments:
            file_path: The path to the file to be played
            before_play: A callback to be triggered when playback starts
            after_play: A callback to be triggered when playback ends

        """
        logger.info("Queueing PCM %s", file_path)

        if not file_path.is_file():
            msg = f"{file_path!r} does not exist"
            raise FileNotFoundError(msg)

        pcm = file_path.read_bytes()
        samples = array.array("h")
        samples.frombytes(pcm)

        with self._lock:
            track = Track(
                id=uuid.uuid4(),
                name=str(file_path),
                samples=samples,
                pos=0,
                before_play=before_play,
                after_play=after_play,
            )
            self._tracks.append(track)

        logger.info("Loaded data from %s", file_path)
        logger.info("Now %d tracks in mixer", len(self._tracks))
        self.resume()

    async def play_file(
        self,
        filename: str,
        before_play: Callable[[], None] | None = None,
        after_play: Callable[[], None] | None = None,
    ) -> None:
        """Decode an audio file via ffmpeg and enqueue it for mixing.

        Uses ffmpeg to convert the specified file into 16-bit 48kHz stereo PCM,
        reads the output, and adds the samples to the mixer queue.

        Args:
            filename: Path to the audio file to play.
            before_play: Optional callback invoked when the file starts playing.
            after_play: Optional callback invoked when the file finishes playing.

        Raises:
            FileNotFoundError: If the file does not exist.
            RuntimeError: If ffmpeg is not installed or decoding fails.

        """
        logger.info("Playing file %s", filename)

        file_path = Path(filename)
        if not await asyncio.to_thread(file_path.is_file):
            msg = f"{filename!r} does not exist"
            raise FileNotFoundError(msg)

        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            msg = "ffmpeg not found in PATH"
            raise RuntimeError(msg)

        proc = await asyncio.create_subprocess_exec(
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
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        pcm_data, err = await proc.communicate()
        if proc.returncode != 0:
            msg = f"ffmpeg failed: {err.decode(errors='ignore')}"
            raise RuntimeError(msg)

        samples = array.array("h")
        samples.frombytes(pcm_data)

        with self._lock:
            track = Track(
                id=uuid.uuid4(),
                name=filename,
                samples=samples,
                pos=0,
                before_play=before_play,
                after_play=after_play,
            )
            self._sfx.append(track)

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
