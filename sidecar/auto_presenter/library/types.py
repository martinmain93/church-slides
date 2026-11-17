"""Canonical data model for songs, slides, sections, and setlists.

This is the source of truth — Pydantic types here are mirrored into
TypeScript via `shared/codegen.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SectionType = Literal[
    "verse",
    "chorus",
    "bridge",
    "tag",
    "intro",
    "outro",
    "pre-chorus",
    "instrumental",
    "other",
]


class Section(BaseModel):
    type: SectionType
    number: int = 1


class Slide(BaseModel):
    id: str
    song_id: str
    section: Section
    order_in_song: int
    order_in_section: int
    lyrics: str
    words: list[str]


class Song(BaseModel):
    id: str
    title: str
    body_text: str
    slides: list[Slide]
    section_order: list[Section]
    ccli: str | None = None
    author: str | None = None
    copyright: str | None = None
    default_tempo_wps: float | None = None
    created_at: datetime
    updated_at: datetime


class SongSetlistItem(BaseModel):
    id: str
    kind: Literal["song"] = "song"
    order: int
    song_id: str


class Setlist(BaseModel):
    id: str
    name: str
    items: list[SongSetlistItem]
    created_at: datetime
    last_used_at: datetime | None = None


class Library(BaseModel):
    songs: dict[str, Song]
