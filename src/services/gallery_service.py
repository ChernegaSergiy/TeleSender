#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simple media gallery storage service."""

import os
import shutil
from pathlib import Path

from src.services.logger import log


class GalleryService:
    ROOT = Path("gallery")

    CATEGORIES = {
        "image": "images",
        "video": "videos",
        "document": "documents",
        "other": "other",
    }

    def __init__(self):
        for folder in self.CATEGORIES.values():
            (self.ROOT / folder).mkdir(parents=True, exist_ok=True)

    def save_file(self, src_path: str, category: str = "other") -> str:
        """Stores a file in the requested Gallery category.

        Files that are already inside the gallery are moved to the category folder
        (Telegram downloads land in ``gallery/other/`` first), so no duplicate
        is left behind. Files from outside the gallery are copied, keeping the
        original untouched. Saving a file onto itself is a no-op.
        """
        source = Path(src_path)
        folder = self.CATEGORIES.get(category, "other")
        target_dir = self.ROOT / folder
        target_dir.mkdir(parents=True, exist_ok=True)

        target = target_dir / source.name
        if source.resolve() == target.resolve():
            log.info(f"Gallery already in place: {target}")
            return str(target)

        target = self._unique_target(target_dir, source.name)
        if self._is_inside_gallery(source):
            shutil.move(str(source), str(target))
            log.info(f"Gallery moved: {source} -> {target}")
        else:
            shutil.copy2(source, target)
            log.info(f"Gallery saved: {target}")
        return str(target)

    def _is_inside_gallery(self, path: Path) -> bool:
        """Returns True when ``path`` already belongs to the Gallery folder."""
        try:
            path.resolve().relative_to(self.ROOT.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _unique_target(directory: Path, name: str) -> Path:
        """Returns a free path in ``directory`` without overwriting existing files."""
        target = directory / name
        if not target.exists():
            return target
        stem = Path(name).stem or "file"
        suffix = Path(name).suffix
        for index in range(1, 1000):
            candidate = directory / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                return candidate
        return directory / f"{stem}_{os.getpid()}{suffix}"

    def list_files(self, category: str = None):
        if category:
            d = self.ROOT / self.CATEGORIES.get(category, "other")
            return [str(p) for p in d.iterdir() if p.is_file()]
        result = []
        for folder in self.CATEGORIES.values():
            d = self.ROOT / folder
            if d.exists():
                result.extend(str(p) for p in d.iterdir() if p.is_file())
        return result


gallery_service = GalleryService()
