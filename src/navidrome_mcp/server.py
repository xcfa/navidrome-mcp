"""FastMCP server exposing Navidrome song search, play statistics and playlist editing."""

from collections.abc import AsyncIterator
from typing import Annotated, Literal

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.lifespan import lifespan
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from navidrome_mcp import __version__
from navidrome_mcp.auth import SharedSecretVerifier
from navidrome_mcp.client import NavidromeClient, Params
from navidrome_mcp.config import Settings
from navidrome_mcp.models import (
    Playlist,
    PlaylistChange,
    PlaylistDetails,
    Song,
    SongPage,
    TagValue,
)

WORK_TAG = "work"
GENRE_TAG = "genre"
# Upper bound on matched ids sent as a repeated filter, to keep query strings sane.
MAX_FILTER_IDS = 100
PLAYLIST_PAGE = 500

SortKey = Literal["play_count", "last_played", "date_added", "random", "title", "artist", "album"]
SORT_FIELDS: dict[str, str] = {
    "play_count": "play_count",
    "last_played": "play_date",
    "date_added": "recently_added",
    "random": "random",
    "title": "title",
    "artist": "artist",
    "album": "album",
}
DESC_BY_DEFAULT = {"play_count", "last_played", "date_added"}

INSTRUCTIONS = """\
Access to the user's Navidrome music library.
Use search_songs for both listing (sorted by play count, last played, date added, random)
and filtering (title, artist/band, work, genre, favourites); filters combine.
Use list_tag_values to discover exact work/genre names. Song ids from search results
are what playlist tools expect - never invent ids."""

SongIds = Annotated[
    list[str],
    Field(description="Song ids taken from search_songs / get_playlist results"),
]


def _client(ctx: Context) -> NavidromeClient:
    return ctx.lifespan_context["navidrome"]


def create_server(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastMCP:
    @lifespan
    async def navidrome_lifespan(server: FastMCP) -> AsyncIterator[dict]:
        client = NavidromeClient(
            settings.navidrome_url,
            settings.navidrome_username,
            settings.navidrome_password.get_secret_value(),
            timeout=settings.navidrome_timeout,
            transport=transport,
        )
        try:
            yield {"navidrome": client}
        finally:
            await client.aclose()

    mcp = FastMCP(
        "navidrome",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=SharedSecretVerifier(settings.mcp_auth_token.get_secret_value()),
        lifespan=navidrome_lifespan,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    # --- songs ------------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    async def search_songs(
        ctx: Context,
        title: Annotated[
            str | None, Field(description="Full-text search over title, album and artist")
        ] = None,
        artist: Annotated[
            str | None, Field(description="Artist or band name (partial match)")
        ] = None,
        work: Annotated[
            str | None,
            Field(description="WORK tag value, partial and case-insensitive, e.g. 'nier'"),
        ] = None,
        genre: Annotated[str | None, Field(description="Genre name (partial match)")] = None,
        starred_only: Annotated[bool, Field(description="Only favourite (starred) songs")] = False,
        sort: Annotated[
            SortKey,
            Field(
                description="play_count = most played, last_played = most recently played, "
                "date_added = newest in library, random = shuffle"
            ),
        ] = "title",
        order: Annotated[
            Literal["asc", "desc"] | None,
            Field(description="Default: desc for play_count/last_played/date_added, else asc"),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 25,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> SongPage:
        """List or search songs. All filters are optional and combine with AND."""
        client = _client(ctx)
        filters: Params = []
        resolved: dict[str, list[str]] = {}

        if title:
            filters.append(("title", title))
        if starred_only:
            filters.append(("starred", "true"))

        for label, tag, value in (("work", WORK_TAG, work), ("genre", GENRE_TAG, genre)):
            if not value:
                continue
            tags = (await client.tag_values(tag, value))[:MAX_FILTER_IDS]
            resolved[label] = [t["tagValue"] for t in tags]
            if not tags:
                return _no_match(offset, resolved, label, value)
            filters += [(tag, t["id"]) for t in tags]

        if artist:
            found = await client.artists(artist, limit=MAX_FILTER_IDS)
            # The server does word-based matching; prefer names that contain the query.
            close = [a for a in found if artist.casefold() in a.get("name", "").casefold()]
            found = close or found
            resolved["artist"] = [a["name"] for a in found]
            if not found:
                return _no_match(offset, resolved, "artist", artist)
            filters += [("artists_id", a["id"]) for a in found]

        if order is None:
            order = "desc" if sort in DESC_BY_DEFAULT else "asc"
        items, total = await client.songs(
            filters, sort=SORT_FIELDS[sort], order=order, offset=offset, limit=limit
        )
        return SongPage(
            total=total,
            offset=offset,
            songs=[Song.from_api(s) for s in items],
            resolved=resolved,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_tag_values(
        ctx: Context,
        tag: Annotated[
            str, Field(description="Tag name: work, genre, mood, grouping, ...")
        ] = WORK_TAG,
        query: Annotated[str | None, Field(description="Partial value filter")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> list[TagValue]:
        """List values of a tag in the library (e.g. all WORKs or genres).

        Song/album counts are only available for some tags (genre); otherwise null."""
        tags = await _client(ctx).tag_values(tag.lower(), query, limit=limit)
        return [
            TagValue(
                value=t["tagValue"],
                song_count=t.get("songCount") or None,
                album_count=t.get("albumCount") or None,
            )
            for t in tags
        ]

    # --- playlists --------------------------------------------------------

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_playlists(
        ctx: Context,
        query: Annotated[str | None, Field(description="Partial playlist name")] = None,
    ) -> list[Playlist]:
        """List playlists visible to the configured Navidrome user."""
        return [Playlist.from_api(p) for p in await _client(ctx).playlists(query)]

    @mcp.tool(annotations={"readOnlyHint": True})
    async def get_playlist(
        ctx: Context,
        playlist_id: str,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> PlaylistDetails:
        """Playlist metadata plus a page of its songs in playlist order."""
        client = _client(ctx)
        meta = await client.playlist(playlist_id)
        tracks, _ = await client.playlist_tracks(playlist_id, offset=offset, limit=limit)
        return PlaylistDetails(
            **Playlist.from_api(meta).model_dump(),
            offset=offset,
            songs=[Song.from_api(t) for t in tracks],
        )

    @mcp.tool(annotations={"destructiveHint": False})
    async def create_playlist(
        ctx: Context,
        name: Annotated[str, Field(min_length=1)],
        song_ids: SongIds,
        comment: str | None = None,
        public: bool = False,
    ) -> PlaylistChange:
        """Create a new playlist and fill it with the given songs (in order, duplicates dropped)."""
        client = _client(ctx)
        playlist_id = await client.create_playlist(name, comment, public)
        ids = _dedupe(song_ids)
        added = await client.add_tracks(playlist_id, ids) if ids else 0
        return PlaylistChange(
            playlist=Playlist.from_api(await client.playlist(playlist_id)), added=added
        )

    @mcp.tool(annotations={"destructiveHint": False})
    async def add_to_playlist(
        ctx: Context,
        playlist_id: str,
        song_ids: SongIds,
        skip_existing: Annotated[
            bool, Field(description="Do not add songs that are already in the playlist")
        ] = True,
    ) -> PlaylistChange:
        """Append songs to the end of an existing (non-smart) playlist."""
        client = _client(ctx)
        await _editable(client, playlist_id)
        ids = _dedupe(song_ids)
        if skip_existing and ids:
            present = {Song.from_api(t).id for t in await _all_tracks(client, playlist_id)}
            ids = [i for i in ids if i not in present]
        added = await client.add_tracks(playlist_id, ids) if ids else 0
        return PlaylistChange(
            playlist=Playlist.from_api(await client.playlist(playlist_id)), added=added
        )

    @mcp.tool(annotations={"destructiveHint": True})
    async def remove_from_playlist(
        ctx: Context,
        playlist_id: str,
        song_ids: SongIds,
    ) -> PlaylistChange:
        """Remove every occurrence of the given songs from a (non-smart) playlist."""
        client = _client(ctx)
        await _editable(client, playlist_id)
        wanted = set(song_ids)
        # DELETE takes playlist row ids (`id`), not song ids (`mediaFileId`).
        rows: list[str] = []
        found: set[str] = set()
        for track in await _all_tracks(client, playlist_id):
            song_id = Song.from_api(track).id
            if song_id in wanted:
                rows.append(str(track["id"]))
                found.add(song_id)
        if rows:
            await client.remove_tracks(playlist_id, rows)
        return PlaylistChange(
            playlist=Playlist.from_api(await client.playlist(playlist_id)),
            removed=len(rows),
            not_found=[i for i in _dedupe(song_ids) if i not in found],
        )

    @mcp.tool(annotations={"destructiveHint": True})
    async def delete_playlist(ctx: Context, playlist_id: str) -> str:
        """Permanently delete a playlist. Ask the user for confirmation first."""
        client = _client(ctx)
        name = (await client.playlist(playlist_id)).get("name", playlist_id)
        await client.delete_playlist(playlist_id)
        return f"Deleted playlist '{name}'"

    return mcp


def _no_match(offset: int, resolved: dict[str, list[str]], label: str, value: str) -> SongPage:
    hint = "list_tag_values" if label in ("work", "genre") else "a shorter name"
    return SongPage(
        total=0,
        offset=offset,
        songs=[],
        resolved=resolved,
        note=f"No {label} matches '{value}'. Try {hint}.",
    )


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


async def _editable(client: NavidromeClient, playlist_id: str) -> None:
    if (await client.playlist(playlist_id)).get("rules"):
        raise ToolError("This is a smart playlist; its songs are defined by rules and can't be edited.")


async def _all_tracks(client: NavidromeClient, playlist_id: str) -> list[dict]:
    tracks: list[dict] = []
    while True:
        page, total = await client.playlist_tracks(
            playlist_id, offset=len(tracks), limit=PLAYLIST_PAGE
        )
        tracks += page
        if not page or len(tracks) >= total:
            return tracks


def main() -> None:
    settings = Settings()
    create_server(settings).run(
        transport="http", host=settings.mcp_host, port=settings.mcp_port
    )


if __name__ == "__main__":
    main()
