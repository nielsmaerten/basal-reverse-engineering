"""Persistent state manager for the slidedeck."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


DECK_DIR = Path(".slidedeck")
ASSETS_DIR = DECK_DIR / "assets"
DECK_JSON = DECK_DIR / "deck.json"

SLIDE_TYPES = {"html", "markdown", "image", "plotly", "stats"}


@dataclass
class Slide:
    id: str
    type: str  # html | markdown | image | plotly | stats
    content: str
    title: str = ""
    position: int = 0


@dataclass
class DeckState:
    title: str = "Slidedeck"
    current_slide_id: Optional[str] = None
    slides: list[Slide] = field(default_factory=list)

    # ── Persistence ──

    @classmethod
    def load(cls) -> DeckState:
        """Load state from disk, or return empty state."""
        _ensure_dirs()
        if DECK_JSON.exists():
            data = json.loads(DECK_JSON.read_text())
            slides = [Slide(**s) for s in data.get("slides", [])]
            return cls(
                title=data.get("title", "Slidedeck"),
                current_slide_id=data.get("current_slide_id"),
                slides=slides,
            )
        return cls()

    def save(self) -> None:
        _ensure_dirs()
        DECK_JSON.write_text(json.dumps(self.to_dict(), indent=2))

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "current_slide_id": self.current_slide_id,
            "slides": [asdict(s) for s in self.slides],
        }

    # ── Slide CRUD ──

    def get_slide(self, slide_id: str) -> Optional[Slide]:
        for s in self.slides:
            if s.id == slide_id:
                return s
        return None

    def add_slide(self, slide: Slide) -> Slide:
        if self.get_slide(slide.id):
            raise ValueError(f"Slide '{slide.id}' already exists")
        if slide.type not in SLIDE_TYPES:
            raise ValueError(f"Invalid type '{slide.type}', must be one of {SLIDE_TYPES}")
        # Position: if not set or beyond end, append
        if slide.position <= 0 or slide.position > len(self.slides):
            slide.position = len(self.slides) + 1
        else:
            # Shift existing slides at or after this position
            for s in self.slides:
                if s.position >= slide.position:
                    s.position += 1
        self.slides.append(slide)
        self.slides.sort(key=lambda s: s.position)
        # Auto-navigate to first slide
        if len(self.slides) == 1:
            self.current_slide_id = slide.id
        self.save()
        return slide

    def update_slide(self, slide_id: str, content: str | None = None,
                     title: str | None = None, type_: str | None = None) -> Slide:
        slide = self.get_slide(slide_id)
        if not slide:
            raise ValueError(f"Slide '{slide_id}' not found")
        if content is not None:
            slide.content = content
        if title is not None:
            slide.title = title
        if type_ is not None:
            if type_ not in SLIDE_TYPES:
                raise ValueError(f"Invalid type '{type_}', must be one of {SLIDE_TYPES}")
            slide.type = type_
        self.save()
        return slide

    def remove_slide(self, slide_id: str) -> None:
        slide = self.get_slide(slide_id)
        if not slide:
            raise ValueError(f"Slide '{slide_id}' not found")
        removed_pos = slide.position
        self.slides.remove(slide)
        # Repack positions
        for s in self.slides:
            if s.position > removed_pos:
                s.position -= 1
        # Fix current_slide_id if removed
        if self.current_slide_id == slide_id:
            self.current_slide_id = self.slides[0].id if self.slides else None
        self.save()

    def clear_slides(self) -> None:
        self.slides.clear()
        self.current_slide_id = None
        self.save()

    def reset(self) -> None:
        """Full reset: clear slides, reset title."""
        self.slides.clear()
        self.current_slide_id = None
        self.title = "Slidedeck"
        self.save()

    # ── Image support ──

    def import_image(self, source_path: str) -> str:
        """Copy an image into .slidedeck/assets/ and return the filename."""
        _ensure_dirs()
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(f"Image not found: {source_path}")
        dest = ASSETS_DIR / src.name
        # Handle name collisions
        counter = 1
        while dest.exists() and dest.read_bytes() != src.read_bytes():
            dest = ASSETS_DIR / f"{src.stem}_{counter}{src.suffix}"
            counter += 1
        if not dest.exists():
            shutil.copy2(src, dest)
        return dest.name


def _ensure_dirs() -> None:
    DECK_DIR.mkdir(exist_ok=True)
    ASSETS_DIR.mkdir(exist_ok=True)
