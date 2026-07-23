"""
Personal YouTube download API for a Telegram music bot.

Endpoints:
    GET /            - health check
    GET /download    - download audio or video from a YouTube URL/ID

Security:
    - Requires a matching `api_key` query param (checked against MY_API_KEY env var)
    - Rate limited to 10 requests/minute per client IP

NOTE: This is intended for personal, private use only. Downloading and
redistributing copyrighted content may violate YouTube's Terms of Service
and copyright law in your jurisdiction - use responsibly and only for
content you have the right to download.
"""

import os
import uuid
import shutil
import logging
from pathlib import Path

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import yt_dlp

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Secret key clients must supply to use the API. Set this in your environment:
#   export MY_API_KEY="some-long-random-string"
MY_API_KEY = os.environ.get("MY_API_KEY")

# Optional path to a Netscape-format cookies.txt file, used by yt-dlp so
# age-restricted / bot-check-gated videos can still be downloaded.
#   export COOKIES_FILE="/path/to/cookies.txt"
COOKIES_FILE = os.environ.get("COOKIES_FILE")

# Directory where temporary downloads are stored before being streamed back
# and then deleted.
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/tmp/ytdlp_downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("music-api")

# --------------------------------------------------------------------------
# App + rate limiter setup
# --------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Personal YouTube Download API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/")
def health_check():
    """Simple health check endpoint."""
    return {"status": "ok"}


def _build_ydl_options(media_type: str, output_template: str) -> dict:
    """
    Build the yt-dlp options dict for either an audio-only or video download.
    """
    common_opts = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # Fail fast instead of hanging on slow/broken streams
        "socket_timeout": 30,
    }

    # Attach cookies file if one was configured - lets yt-dlp get past
    # age gates / "confirm you're not a bot" checks using your own session.
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        common_opts["cookiefile"] = COOKIES_FILE

    if media_type == "audio":
        common_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:  # video
        common_opts.update({
            # Best mp4 video+audio, falling back to best overall and letting
            # yt-dlp/ffmpeg merge into an mp4 container.
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
        })

    return common_opts


@app.get("/download")
@limiter.limit("10/minute")
def download(
    request: Request,
    url: str = Query(..., description="YouTube video ID or full URL"),
    type: str = Query(..., description="Either 'audio' or 'video'"),
    api_key: str = Query(..., description="Secret API key"),
):
    """
    Download a YouTube video as audio (mp3) or video (mp4) and stream the
    resulting file back to the caller. The temp file is deleted immediately
    after the response has been sent.
    """
    # --- 1. Auth check -----------------------------------------------------
    if not MY_API_KEY:
        # Server misconfigured - fail safe rather than accepting any key.
        raise HTTPException(status_code=500, detail="Server API key is not configured")
    if api_key != MY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # --- 2. Validate `type` --------------------------------------------------
    if type not in ("audio", "video"):
        raise HTTPException(status_code=400, detail="`type` must be 'audio' or 'video'")

    # --- 3. Normalize a bare video ID into a full URL ----------------------
    # Accepts either a full URL or a raw 11-char YouTube video ID.
    video_url = url if url.startswith("http") else f"https://www.youtube.com/watch?v={url}"

    # --- 4. Download via yt-dlp --------------------------------------------
    job_id = uuid.uuid4().hex
    output_template = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")
    ydl_opts = _build_ydl_options(type, output_template)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            downloaded_path = Path(ydl.prepare_filename(info))

            # For audio, the postprocessor converts to mp3, so the final
            # extension differs from what prepare_filename predicted.
            if type == "audio":
                downloaded_path = downloaded_path.with_suffix(".mp3")

        if not downloaded_path.exists():
            raise FileNotFoundError(f"Expected output file not found: {downloaded_path}")

    except Exception as exc:
        logger.exception("yt-dlp failed for url=%s type=%s", video_url, type)
        # Clean up any partial files from this job before returning the error.
        _cleanup_job_files(job_id)
        return JSONResponse(
            status_code=500,
            content={"error": "download_failed", "detail": str(exc)},
        )

    # --- 5. Stream the file back, then delete it ---------------------------
    media_type = "audio/mpeg" if type == "audio" else "video/mp4"
    filename = downloaded_path.name

    def _cleanup_after_send():
        try:
            downloaded_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("Failed to delete temp file: %s", downloaded_path)

    response = FileResponse(
        path=downloaded_path,
        media_type=media_type,
        filename=filename,
        background=_make_background_cleanup(_cleanup_after_send),
    )
    return response


def _make_background_cleanup(cleanup_fn):
    """Wrap a plain function as a Starlette BackgroundTask for FileResponse."""
    from starlette.background import BackgroundTask
    return BackgroundTask(cleanup_fn)


def _cleanup_job_files(job_id: str):
    """Remove any leftover files matching a given job id (used on failure)."""
    for f in DOWNLOAD_DIR.glob(f"{job_id}.*"):
        try:
            f.unlink()
        except Exception:
            logger.exception("Failed to clean up leftover file: %s", f)
