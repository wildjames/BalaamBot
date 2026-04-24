# Cookie Exporter – Chrome Extension

A Manifest V3 Chrome extension that collects all browser cookies, formats them as a [Netscape HTTP Cookie File](https://curl.haxx.se/rfc/cookie_spec.html), and either downloads the file locally or uploads it directly to the [BalaamBot cookie upload server](../src/balaambot/cookie_server.py).

## Files

| File | Purpose |
|---|---|
| `manifest.json` | Extension manifest (permissions, entry points) |
| `popup.html` / `popup.js` | Toolbar popup – download and upload buttons |
| `options.html` / `options.js` | Settings page – server URL and API key |

## Installation

1. Open **chrome://extensions** in Chrome.
2. Enable **Developer mode** (toggle, top-right).
3. Click **Load unpacked** and select this `chrome-extension/` folder.

## Usage

### First-time configuration

Before uploading you must configure the server connection:

1. Click the extension icon in the toolbar then click **⚙ Settings**, or right-click the icon and choose **Options**.
2. Fill in the two fields:
   - **Server URL** – full URL of the `POST /cookies` endpoint, e.g. `http://localhost:8080/cookies`.
   - **API Key** – the secret token set as `COOKIE_SERVER_API_KEY` on the server.
3. Click **Save**. Settings are stored in `chrome.storage.sync` and sync across Chrome profiles.

### Downloading cookies locally

Click **Download cookies.txt** in the popup. The file is saved to your default downloads folder.

### Uploading cookies to the server

Click **Upload to server** in the popup. The extension will:

1. Collect all cookies visible to the extension.
2. Build a Netscape-format file in memory.
3. `POST` the file to the configured URL with an `Authorization: Bearer <key>` header.
4. Display a success or error message in the popup.

## Cookie file format

```
# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
.example.com	TRUE	/	FALSE	1745000000	session_id	abc123
```

Columns (tab-separated): `domain`, `include_subdomains`, `path`, `secure`, `expires (unix timestamp)`, `name`, `value`.

This format is compatible with `curl --cookie cookies.txt`, `yt-dlp --cookies cookies.txt`, and similar tools.

## Required permissions

| Permission | Reason |
|---|---|
| `cookies` | Read all browser cookies |
| `downloads` | Trigger the local file download |
| `storage` | Persist server URL and API key settings |
| `<all_urls>` (host permission) | Access cookies set on any domain |

## Server setup

See [`src/balaambot/cookie_server.py`](../src/balaambot/cookie_server.py) and [`env.template`](../env.template) for server configuration. The minimum required environment variables are:

```bash
COOKIE_SERVER_API_KEY=your-secret-key
BALAAMBOT_COOKIE_UPLOAD_PATH=/path/to/cookies.txt
```

Start the server with:

```bash
uv run python -m balaambot.cookie_server
```
