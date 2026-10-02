"""Compact output models. Navidrome responses are large; tools return only what an LLM needs."""

from typing import Any

from pydantic import BaseModel, Field


def _genres(raw: dict[str, Any]) -> list[str]:
    genres = raw.get("genres") or []
    names = [g["name"] for g in genres if isinstance(g, dict) and g.get("name")]
    if not names and raw.get("genre"):
        names = [raw["genre"]]
    return names


class Song(BaseModel):
    id: str
    title: str
    artist: str | None = None
    album: str | None = None
    year: int | None = None
    genres: list[str] = Field(default_factory=list)
    work: list[str] = Field(default_factory=list)
    duration_sec: int = 0
    play_count: int = 0
    last_played: str | None = None
    starred: bool = False
    added_at: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Song":
        # Playlist track rows carry the song id in `mediaFileId`; `id` is the row id there.
        tags = raw.get("tags") or {}
        return cls(
            id=raw.get("mediaFileId") or raw["id"],
            title=raw.get("title", ""),
            artist=raw.get("artist"),
            album=raw.get("album"),
            year=raw.get("year") or None,
            genres=_genres(raw),
            work=tags.get("work") or [],
            duration_sec=round(raw.get("duration") or 0),
            play_count=raw.get("playCount") or 0,
            last_played=raw.get("playDate"),
            starred=bool(raw.get("starred")),
            added_at=raw.get("createdAt"),
        )


class SongPage(BaseModel):
    total: int
    offset: int
    songs: list[Song]
    resolved: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Library values the artist/work/genre filters matched",
    )
    note: str | None = None


class TagValue(BaseModel):
    value: str
    # Navidrome only keeps counts for some tags (e.g. genre); None means unknown.
    song_count: int | None = None
    album_count: int | None = None


class Playlist(BaseModel):
    id: str
    name: str
    comment: str | None = None
    song_count: int = 0
    duration_sec: int = 0
    public: bool = False
    smart: bool = False
    owner: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Playlist":
        return cls(
            id=raw["id"],
            name=raw.get("name", ""),
            comment=raw.get("comment") or None,
            song_count=raw.get("songCount") or 0,
            duration_sec=round(raw.get("duration") or 0),
            public=bool(raw.get("public")),
            smart=bool(raw.get("rules")),
            owner=raw.get("ownerName"),
        )


class PlaylistDetails(Playlist):
    offset: int = 0
    songs: list[Song] = Field(default_factory=list)


class PlaylistChange(BaseModel):
    playlist: Playlist
    added: int = 0
    removed: int = 0
    not_found: list[str] = Field(default_factory=list)
