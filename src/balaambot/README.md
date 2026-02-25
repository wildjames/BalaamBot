# BalaamBot Architecture Guide

This README is a **fast re-orientation map** for `src/balaambot`.
If you forgot everything, read the sections in order and you should be productive again in a few minutes.

## 1) 60-second mental model

- `main.py` boots a `discord.py` bot, auto-loads all cogs in `bot_commands/`, and registers shared listeners.
- Cogs (`bot_commands/*.py`) are the app layer: slash commands, validation, and user-facing responses.
- Feature logic lives in subsystem modules:
  - `youtube/` for queueing, metadata, download, PCM caching.
  - `audio_handlers/multi_audio_source.py` for mixing music + SFX into one Discord audio stream.
  - `sfx/audio_sfx_jobs.py` for scheduled/random SFX loops.
  - `cats/cat_handler.py` for cat game state + persistence.
- Shared infra:
  - `discord_utils.py` handles voice connection checks and mixer access.
  - `utils.py` provides cache abstraction (memory/Redis), executors, and helper formatting.

## 2) Runtime startup flow

1. `start()` in `main.py` runs `asyncio.run(main())`.
2. `main()` validates `DISCORD_BOT_TOKEN`.
3. Bot context opens (`async with bot:`).
4. `load_extensions()` imports every file in `bot_commands/` (except `__init__.py`) as a cog extension.
5. `add_listeners()` registers `on_voice_state_update` from `discord_utils.py`.
6. `bot.start(token)` connects to Discord.
7. On ready, `bot.tree.sync()` publishes slash commands.

## 3) Module map

### Entrypoints and shared config

- `main.py`: process/bootstrap lifecycle.
- `config.py`: env-driven config (persistent directory, Redis options, optional YouTube cookie file, custom voice client class).
- `utils.py`: cache API (`get_cache`/`set_cache`), process pool executor, time formatting.
- `discord_utils.py`: command guardrails + connect/reconnect logic + mixer retrieval.

### Command cogs (app layer)

- `bot_commands/bot_commands.py`: `/ping`, `/stop` (stop all jobs + disconnect).
- `bot_commands/music_commands.py`: YouTube queue UX (`/play`, `/play_next`, `/play_list`, `/list_queue`, `/skip`, `/pause`, `/resume`, `/stop_music`, `/clear_queue`, `/prune_queue`) including interaction Views/buttons.
- `bot_commands/sfx_commands.py`: scheduled and manual SFX (`/add_sfx`, `/remove_sfx`, `/list_sfx_jobs`, `/list_sfx`, `/trigger_sfx`, `/play_sfx`, `/stop_sfx`).
- `bot_commands/cat_commands.py`: cat minigame commands + periodic hunger tasks.
- `bot_commands/joke_commands.py`: joke and meme commands.

### Audio pipeline

- `audio_handlers/multi_audio_source.py`:
  - Maintains one `MultiAudioSource` per guild (`ensure_mixer`).
  - Mixes many PCM streams each 20ms frame.
  - Separates long-form tracks (`_tracks`) from SFX (`_sfx`).
  - Supports callbacks before/after playback for queue progression.

### YouTube subsystem

- `youtube/jobs.py`:
  - Owns in-memory queue per guild (`youtube_queue`).
  - Orchestrates add/skip/stop/prune/list.
  - Triggers playback progression via callbacks and `_play_next()`.
  - Preloads upcoming tracks (`QUEUE_FORESIGHT`) in background.
- `youtube/download.py`:
  - Downloads with `yt-dlp`, converts with `ffmpeg`, writes PCM to cache.
  - Uses per-URL async locks to avoid duplicate concurrent downloads.
- `youtube/metadata.py`:
  - Fetches metadata through `yt-dlp` and caches it.
- `youtube/utils.py`:
  - URL validation/parsing, cache/temp path construction, metadata cache helpers.

### SFX subsystem

- `sfx/audio_sfx_jobs.py`:
  - Discovers files under `sounds/` at import time.
  - Maintains loop jobs (`loop_jobs`) keyed by UUID.
  - Each job sleeps random interval and queues one effect into the mixer.

### Cat subsystem

- `cats/cat_handler.py`:
  - In-memory DB shape: `guild_id -> cat_id -> Cat`.
  - Loads/saves JSON at `PERSISTENT_DATA_DIR/cats.json`.
  - Fuzzy name matching via `rapidfuzz`.

## 4) State and persistence

- **In-memory (per process):**
  - YouTube queues (`youtube.jobs.youtube_queue`).
  - Active SFX loop tasks (`sfx.audio_sfx_jobs.loop_jobs`).
  - Guild mixers (`audio_handlers.multi_audio_source._mixers`).
- **Persistent on disk:**
  - Cat DB: `persistent/cats.json`.
  - Audio cache + temp files: `persistent/audio_cache/`.
- **Optional external persistence:**
  - Metadata/cache in Redis if `USE_REDIS=true`; otherwise memory cache in `utils.py`.

## 5) End-to-end flows to remember

### `/play <query>`

1. `music_commands.py` validates voice context via `discord_utils`.
2. Query resolved to URL/search/playlist path.
3. URL(s) added to `youtube.jobs` queue.
4. `_play_next()` fetches metadata + PCM cache, enqueues PCM into mixer.
5. Mixer callback pops queue head and schedules next track.

### `/add_sfx` scheduled loop

1. `sfx_commands.py` ensures connection.
2. `audio_sfx_jobs.add_job()` creates background task.
3. Task sleeps random interval and calls `mixer.play_file(sound)`.
4. Track completes; loop continues until removed/cancelled/disconnect.

### Voice disconnect safety

- Shared listener `on_voice_state_update` disconnects bot if no human users remain in channel.

## 6) What to edit for common changes

- Add/modify slash command behavior: `bot_commands/<feature>_commands.py`.
- Change queue semantics or preloading policy: `youtube/jobs.py`.
- Change metadata/download/caching behavior: `youtube/metadata.py`, `youtube/download.py`, `youtube/utils.py`.
- Change mixing/volume/normalization behavior: `audio_handlers/multi_audio_source.py`.
- Change cat game rules or persistence schema: `cats/cat_handler.py` and `bot_commands/cat_commands.py`.
- Change voice-connection checks/policies: `discord_utils.py`.

## 7) Operational notes

- `ffmpeg` must be available in `PATH` for playback/SFX decode.
- Bot commands are auto-discovered by filename in `bot_commands/`.
- Most long-running work is task-based (`asyncio` tasks + executor workers), so race/ordering issues usually live around queue mutation and callback timing.

## 8) If you only have 2 minutes

Open these in order:

1. `main.py`
2. `bot_commands/music_commands.py`
3. `youtube/jobs.py`
4. `audio_handlers/multi_audio_source.py`
5. `discord_utils.py`

That path gives the highest signal for understanding how the bot actually behaves at runtime.
