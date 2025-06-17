# Balaam Bot
Another Discord bot of biblical proportions.

[![Coverage Status](https://coveralls.io/repos/github/wildjames/BalaamBot/badge.svg?branch=main)](https://coveralls.io/github/wildjames/BalaamBot?branch=main)

Balaam Bot mixes YouTube audio, sound effects and other fun features into a single Discord bot. It uses slash commands provided by `discord.py` and includes a small cat adoption mini‑game, jokes and meme generation.

## Features
- **Music playback** – queue YouTube URLs or search terms with `/play` and manage the queue with `/list_queue`, `/skip`, and `/clear_queue`.
- **Sound effects** – schedule random effects, trigger them manually or list the available files with `/list_sfx`.
- **Cat commands** – adopt a cat, pet it and keep track of the guild’s collection.
- **Jokes and memes** – `/joke` tells a joke and `/meme` fetches a random meme image.

## Requirements
- Python 3.10+
- `ffmpeg` installed and available in the `PATH`.

## Quick start
1. Clone the repository and install dependencies:
   ```bash
   uv sync && uv run pre-commit install --install-hooks
   ```
2. Copy `env.template` to `.env` and set `DISCORD_BOT_TOKEN` to your bot’s token.
3. Unpack the bundled sound effects (optional):
   ```bash
   make unpack
   ```
4. Start the bot locally:
   ```bash
   make run
   ```

### Docker
Alternatively build and run using Docker:
```bash
docker build -t balaambot:latest .
docker run --rm -it --env-file .env -v $(pwd)/persistent:/app/persistent balaambot:latest
```

## Bot setup
Follow the [discord.py guide](https://discordpy.readthedocs.io/en/stable/discord.html) when creating your application. When inviting the bot, grant it the following scopes and permissions:

Scopes:
- `applications.commands`
- `bot`

Permissions:
- `Connect`
- `Send Messages`
- `Speak`
- `Use Voice Activity`
- `View Channels`

Privileged intents:
- `Message Content Intent`

## Tests
Run the unit tests with:
```bash
make test
```
Use `make test-integration` to run the slower integration tests.

For planned work see [TODO.md](TODO.md).

