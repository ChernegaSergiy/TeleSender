#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web API route for uploading media files into the Gallery folder."""

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote

from aiohttp import web

from src.config.config import config
from src.services.gallery_service import GalleryService
from src.services.logger import log
from src.services.translator import lang


CHUNK_SIZE = 256 * 1024

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".heic", ".heif", ".avif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp"}
GIF_EXTENSIONS = {".gif"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav", ".amr"}
STICKER_EXTENSIONS = {".tgs"}


def _safe_filename(name: str) -> str:
    """Returns a single, filesystem-safe file name without any path parts."""
    name = unquote(str(name or ""))
    name = Path(name).name
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*%]+', "_", name)
    name = name.strip().strip(".")
    stem, suffix = os.path.splitext(name)
    stem = (stem or "upload")[:80]
    suffix = suffix[:16]
    return f"{stem}{suffix}"


def _unique_target(directory: Path, filename: str) -> Path:
    """Returns a path in ``directory`` that does not overwrite an existing file."""
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = os.path.splitext(filename)
    for index in range(1, 1000):
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{os.getpid()}{suffix}"


def _classify(filename: str, content_type: str = ""):
    """Maps an uploaded file to a Gallery category and a task media type."""
    suffix = Path(filename).suffix.lower()

    if suffix in GIF_EXTENSIONS:
        return "image", "gif"
    if suffix in PHOTO_EXTENSIONS:
        return "image", "photo"
    if suffix in VIDEO_EXTENSIONS:
        return "video", "video"
    if suffix in AUDIO_EXTENSIONS:
        return "other", "audio"
    if suffix in STICKER_EXTENSIONS:
        return "other", "sticker"

    content_type = (content_type or "").lower()
    if content_type.startswith("image/"):
        return "image", "photo"
    if content_type.startswith("video/"):
        return "video", "video"
    if content_type.startswith("audio/"):
        return "other", "audio"
    return "document", "document"


class MediaApiRoutes:
    """Handles media uploads for the task form."""

    def __init__(self, bot=None):
        self.bot = bot

    async def _save_part(self, part, max_bytes: int) -> dict:
        """Streams one multipart file into Gallery and returns its metadata."""
        category, media_type = _classify(
            part.filename, part.headers.get("Content-Type", "")
        )
        folder = GalleryService.ROOT / GalleryService.CATEGORIES.get(category, "other")
        folder.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(part.filename)

        descriptor, temp_name = tempfile.mkstemp(
            prefix=".upload-", suffix=".part", dir=str(folder)
        )
        temp = Path(temp_name)

        size = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while True:
                    chunk = await part.read_chunk(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise web.HTTPRequestEntityTooLarge(
                            max_size=max_bytes, actual_size=size
                        )
                    handle.write(chunk)
            target = _unique_target(folder, safe_name)
            os.replace(temp, target)
        except BaseException:
            try:
                temp.unlink()
            except OSError:
                pass
            raise

        log.info(f"Gallery upload saved: {target} ({size} bytes)")
        return {
            "path": str(target),
            "name": target.name,
            "size": size,
            "media_type": media_type,
            "category": category,
        }

    async def upload(self, request):
        """Accepts a multipart form with a ``file`` field and stores it in Gallery."""
        try:
            reader = await request.multipart()
        except Exception:
            return web.json_response(
                {"error": lang.get("web.errors.invalid_request", "Invalid request")},
                status=400,
            )

        max_bytes = max(1, config.web_max_upload_mb) * 1024 * 1024
        saved = None
        try:
            async for part in reader:
                if part.name != "file" or not getattr(part, "filename", None):
                    continue
                # Drain extra file parts, but store only the first one.
                if saved is not None:
                    continue
                saved = await self._save_part(part, max_bytes)
        except web.HTTPRequestEntityTooLarge:
            return web.json_response(
                {"error": lang.get("web.errors.file_too_large", "File is too large")},
                status=413,
            )
        except Exception:
            log.exception("Gallery upload failed")
            return web.json_response(
                {"error": lang.get("web.errors.upload_failed", "Upload failed")},
                status=500,
            )

        if saved is None:
            return web.json_response(
                {"error": lang.get("web.errors.no_file", "No file selected")},
                status=400,
            )

        return web.json_response({"success": True, **saved})

    def register(self, app: web.Application):
        """Registers media API routes."""
        app.router.add_post("/api/media/upload", self.upload)
