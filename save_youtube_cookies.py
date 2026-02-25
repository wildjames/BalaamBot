"""Log into YouTube with Playwright and persist cookies to a file.

Environment variables:
- YOUTUBE_USERNAME: Google account email/username.
- YOUTUBE_PASSWORD: Google account password.
- YOUTUBE_COOKIES_PATH: Output path for cookies. Can be a file path or directory.
  If a directory is provided, cookies are written to <dir>/cookies.txt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

LOGIN_TIMEOUT_MS = 60_000
MIN_ACTION_DELAY_SECONDS = 0.35
MAX_ACTION_DELAY_SECONDS = 1.4
LOGGER = logging.getLogger(__name__)
SYSTEM_RANDOM = random.SystemRandom()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        msg = f"Missing required environment variable: {name}"
        raise ValueError(msg)
    return value


def _resolve_output_path(raw_path: str) -> Path:
    output_path = Path(raw_path).expanduser()
    if output_path.exists() and output_path.is_dir():
        return output_path / "cookies.txt"
    if output_path.suffix == "":
        return output_path / "cookies.txt"
    return output_path


async def _human_delay(
    min_delay: float = MIN_ACTION_DELAY_SECONDS,
    max_delay: float = MAX_ACTION_DELAY_SECONDS,
) -> None:
    """Sleep for a random short interval to mimic less robotic interaction."""
    await asyncio.sleep(SYSTEM_RANDOM.uniform(min_delay, max_delay))


async def save_youtube_cookies() -> None:
    """Authenticate to YouTube and write browser cookies to disk as JSON text."""
    username = _require_env("YOUTUBE_USERNAME")
    password = _require_env("YOUTUBE_PASSWORD")
    cookies_output = _resolve_output_path(_require_env("YOUTUBE_COOKIES_PATH"))

    cookies_output.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto("https://www.youtube.com/", wait_until="domcontentloaded")
            await _human_delay()

            sign_in = page.get_by_role("link", name="Sign in")
            if await sign_in.count() == 0:
                await _human_delay()
                await page.goto(
                    "https://accounts.google.com/ServiceLogin?service=youtube",
                    wait_until="domcontentloaded",
                )
            else:
                await _human_delay()
                await sign_in.first.click()

            await _human_delay()
            await page.get_by_label("Email or phone").fill(username)
            await _human_delay()
            await page.get_by_role("button", name="Next").click()

            await _human_delay()
            await page.get_by_label("Enter your password").fill(password)
            await _human_delay()
            await page.get_by_role("button", name="Next").click()
            await _human_delay()

            await page.wait_for_url(
                "https://www.youtube.com/**", timeout=LOGIN_TIMEOUT_MS
            )
            await page.wait_for_selector("button#avatar-btn", timeout=LOGIN_TIMEOUT_MS)

            cookies = await context.cookies()
            cookies_output.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            LOGGER.info("Saved %s cookies to %s", len(cookies), cookies_output)
        except PlaywrightTimeoutError as exc:
            msg = (
                "Timed out waiting for YouTube login to complete. "
                "Your account may require CAPTCHA, 2FA, or manual verification."
            )
            raise RuntimeError(msg) from exc
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(save_youtube_cookies())
