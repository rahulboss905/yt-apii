# Personal YouTube Download API

A small self-hosted FastAPI server that downloads audio/video from YouTube
via `yt-dlp`, for use as a backend to a personal Telegram music bot.

⚠️ **Personal use only.** This is meant to run privately behind your own
API key, for content you have the right to download. Don't expose it
publicly or use it to redistribute copyrighted material.

## Requirements

- Python 3.9+
- `ffmpeg` installed on the host (required by yt-dlp for audio extraction /
  merging video+audio). On Debian/Ubuntu: `sudo apt install ffmpeg`.

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. Set environment variables

| Variable       | Required | Description                                                                 |
|----------------|----------|-------------------------------------------------------------------------------|
| `MY_API_KEY`   | Yes      | Secret key clients must pass as `?api_key=...`                              |
| `COOKIES_FILE` | No       | Path to a Netscape-format `cookies.txt` for age/bot-gated videos             |
| `DOWNLOAD_DIR` | No       | Where temp files are written before streaming/cleanup (default `/tmp/ytdlp_downloads`) |

Locally (Linux/macOS):

```bash
export MY_API_KEY="pick-a-long-random-string"
export COOKIES_FILE="/home/you/cookies.txt"   # optional
```

On Windows (PowerShell):

```powershell
$env:MY_API_KEY = "pick-a-long-random-string"
$env:COOKIES_FILE = "C:\path\to\cookies.txt"
```

### Getting a cookies.txt file

Export cookies from a logged-in browser session using an extension like
"Get cookies.txt LOCALLY" (Chrome/Firefox). This lets yt-dlp download
age-restricted or bot-check-gated videos using your own YouTube session.
Keep this file private — it contains your login session.

## 3. Run locally

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Test it:

```bash
curl "http://localhost:8000/"
# {"status":"ok"}

curl -OJ "http://localhost:8000/download?url=dQw4w9WgXcQ&type=audio&api_key=pick-a-long-random-string"
```

## API

### `GET /`
Health check. Returns `{"status": "ok"}`.

### `GET /download`

| Param     | Required | Values                          |
|-----------|----------|----------------------------------|
| `url`     | Yes      | Full YouTube URL or bare video ID |
| `type`    | Yes      | `audio` or `video`                |
| `api_key` | Yes      | Must match `MY_API_KEY`           |

- Returns the file as a binary stream (`audio/mpeg` or `video/mp4`).
- Returns `401` if the API key is wrong.
- Returns `400` if `type` is invalid.
- Returns `500` with a JSON error body if the download fails.
- Rate limited to **10 requests/minute per IP** (returns `429` if exceeded).

The temp file on disk is deleted right after it's streamed to the client,
so storage doesn't fill up over time.

## 4. Deploying

### Render

1. Push this folder to a GitHub repo (keep it **private**, since it contains
   your bot's download logic).
2. On Render: New → Web Service → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables `MY_API_KEY` (and `COOKIES_FILE` if used) under
   the service's **Environment** tab.
6. If using cookies, you'll need to get the `cookies.txt` file onto the
   instance — e.g. commit it to a private repo path referenced by
   `COOKIES_FILE`, or use Render's "Secret Files" feature to mount it and
   point `COOKIES_FILE` at that path.
7. Make sure `ffmpeg` is available. Render's default Python environment may
   not include it — add an `apt.txt` file containing `ffmpeg` (Render
   installs packages listed there), or use a Docker deploy with an image
   that includes ffmpeg.

### Railway

1. Push the folder to a GitHub repo and create a new Railway project from it.
2. Railway auto-detects Python; ensure the start command is set to:
   `uvicorn main:app --host 0.0.0.0 --port $PORT`
3. Add `MY_API_KEY` / `COOKIES_FILE` under **Variables**.
4. For `ffmpeg`, add a `nixpacks.toml` with:
   ```toml
   [phases.setup]
   nixPkgs = ["ffmpeg"]
   ```
5. For cookies, use Railway's file/volume support, or base64-encode the
   cookies file into a variable and decode it to disk on startup.

## Notes / limitations

- This server is single-instance and not designed for concurrent heavy
  traffic — it's built for a personal bot with light usage, hence the
  10 req/min rate limit.
- Downloaded files are temporary and deleted immediately after being sent;
  there's no caching layer. If your bot repeatedly requests the same video,
  consider adding a cache in front of this if that becomes an issue.
- Respect YouTube's Terms of Service for your own use case.
