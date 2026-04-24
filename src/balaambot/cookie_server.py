"""FastAPI server that accepts a cookies.txt upload and saves it to a configured path.

The server is optional.  If ``COOKIE_SERVER_API_KEY`` or
``BALAAMBOT_COOKIE_UPLOAD_PATH`` are not set the module imports cleanly and
:func:`start_server` returns ``None`` after logging a warning, so the rest of
the bot continues unaffected.

Authentication
--------------
Every request must include an ``Authorization: Bearer <token>`` header.
The expected token is read from the COOKIE_SERVER_API_KEY environment
variable.  The comparison uses :func:`secrets.compare_digest` to avoid
timing-based side-channel attacks.

Environment variables
---------------------
COOKIE_SERVER_API_KEY
    Required to enable the server.  The secret bearer token clients must supply.
BALAAMBOT_COOKIE_UPLOAD_PATH
    Required to enable the server.  Absolute path where ``cookies.txt`` will be
    written.  The parent directory must already exist.
COOKIE_SERVER_HOST
    Optional.  Host to bind on (default: ``0.0.0.0``).
COOKIE_SERVER_PORT
    Optional.  Port to listen on (default: ``8080``).
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Security, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def _build_app(api_key: str, upload_path: Path) -> FastAPI:
    """Construct and return the FastAPI application."""
    app = FastAPI(
        title="Cookie Upload Server",
        description="Upload a Netscape-format cookies.txt file.",
        version="1.0.0",
        # Disable the interactive docs in production to reduce attack surface.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    bearer_scheme = HTTPBearer()

    def _require_auth(
        credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),  # noqa: B008
    ) -> None:
        """Dependency that enforces Bearer token authentication."""
        # compare_digest prevents timing-based attacks.
        if not secrets.compare_digest(
            credentials.credentials.encode(), api_key.encode()
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get(
        "/health",
        summary="Health check endpoint",
        description="Returns 200 OK if the server is running.",
    )
    async def health_check() -> dict[str, str]:
        """Simple health check endpoint."""
        return {"status": "ok"}

    @app.post(
        "/cookies",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(_require_auth)],
        summary="Upload cookies.txt",
        description=(
            "Accepts a ``cookies.txt`` file in Netscape format and writes it to the "
            "server-configured path.  Requires a valid Bearer token."
        ),
    )
    async def upload_cookies(file: UploadFile) -> None:
        """Receive the uploaded file and atomically write it to upload_path."""
        if file.content_type not in ("text/plain", "application/octet-stream"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=(
                    f"Expected content type text/plain, got {file.content_type!r}. "
                    "Please upload a plain-text cookies.txt file."
                ),
            )

        # Read at most 10 MiB to guard against unreasonably large uploads.
        max_bytes = 10 * 1024 * 1024
        content = await file.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Uploaded file exceeds the 10 MiB limit.",
            )

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="File could not be decoded as UTF-8.",
            ) from exc

        if not text.lstrip().startswith("# Netscape HTTP Cookie File"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "File does not appear to be a Netscape HTTP Cookie File. "
                    "Ensure the first line is '# Netscape HTTP Cookie File'."
                ),
            )

        # Write atomically: write to a temp file next to the destination, then
        # rename so the target is never partially written.
        tmp_path = upload_path.with_suffix(".tmp")
        try:
            tmp_path.write_bytes(content)
            tmp_path.replace(upload_path)
        except OSError as exc:
            logger.exception("Failed to write cookie file")
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save the cookie file on the server.",
            ) from exc

        logger.info("cookies.txt updated at %s", upload_path)

    return app


# ---------------------------------------------------------------------------
# Public async entry point (used by main.py)
# ---------------------------------------------------------------------------


def _resolve_config() -> tuple[str, Path, str, int] | None:
    """Read and validate server config from environment variables.

    Returns a (api_key, upload_path, host, port) tuple, or None if the server
    should not start.
    """
    api_key = os.getenv("COOKIE_SERVER_API_KEY")
    upload_path_raw = os.getenv("BALAAMBOT_COOKIE_UPLOAD_PATH")

    if not api_key or not upload_path_raw:
        logger.warning(
            "Cookie upload server is disabled. "
            "Set COOKIE_SERVER_API_KEY and BALAAMBOT_COOKIE_UPLOAD_PATH to enable it."
        )
        return None

    upload_path = Path(upload_path_raw).resolve()
    if not upload_path.parent.is_dir():
        logger.error(
            "Cookie upload server disabled: parent directory of "
            "BALAAMBOT_COOKIE_UPLOAD_PATH does not exist: %s",
            upload_path.parent,
        )
        return None

    host = os.getenv("COOKIE_SERVER_HOST", "0.0.0.0")  # noqa: S104
    port = int(os.getenv("COOKIE_SERVER_PORT", "8080"))
    return api_key, upload_path, host, port


async def start_server() -> None:
    """Start the uvicorn server as a coroutine.

    Returns immediately without starting anything if the required environment
    variables are not set, so callers do not need to guard the call.
    """
    cfg = _resolve_config()
    if cfg is None:
        return

    api_key, upload_path, host, port = cfg
    logger.info("Cookie upload server will write to: %s", upload_path)

    app = _build_app(api_key, upload_path)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info("Starting cookie upload server on %s:%s", host, port)
    await server.serve()


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    asyncio.run(start_server())
