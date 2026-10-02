import pytest
from fastmcp.exceptions import ToolError

from .conftest import FakeNavidrome, song


async def test_lists_expected_tools(mcp_client):
    names = {t.name for t in await mcp_client.list_tools()}
    assert names == {
        "search_songs",
        "list_tag_values",
        "list_playlists",
        "get_playlist",
        "create_playlist",
        "add_to_playlist",
        "remove_from_playlist",
        "delete_playlist",
    }


@pytest.mark.parametrize(
    ("sort", "api_sort", "api_order"),
    [
        ("play_count", "play_count", "DESC"),
        ("last_played", "play_date", "DESC"),
        ("date_added", "recently_added", "DESC"),
        ("random", "random", "ASC"),
        ("title", "title", "ASC"),
    ],
)
async def test_search_songs_sort_mapping(mcp_client, fake, sort, api_sort, api_order):
    await mcp_client.call_tool("search_songs", {"sort": sort, "limit": 10, "offset": 5})

    params = fake.last("/api/song").url.params
    assert params["_sort"] == api_sort
    assert params["_order"] == api_order
    assert (params["_start"], params["_end"]) == ("5", "15")


async def test_search_songs_maps_song_fields(mcp_client, fake: FakeNavidrome):
    fake.songs = [
        song(
            "s1",
            "Weight of the World",
            year=2017,
            duration=332.4,
            playCount=42,
            playDate="2026-09-30T10:00:00Z",
            starred=True,
            createdAt="2026-01-01T00:00:00Z",
            genres=[{"id": "g", "name": "Soundtrack"}],
            tags={"work": ["NieR:Automata"]},
        ),
        song("s2", "Never played"),
    ]

    page = (await mcp_client.call_tool("search_songs", {"sort": "play_count"})).data

    assert page.total == 2
    first, second = page.songs
    assert first.play_count == 42 and first.starred and first.duration_sec == 332
    assert first.genres == ["Soundtrack"] and first.work == ["NieR:Automata"]
    assert second.play_count == 0 and second.last_played is None and not second.starred


async def test_search_songs_combines_filters(mcp_client, fake: FakeNavidrome):
    fake.tags = [
        {"id": "w1", "tagName": "work", "tagValue": "NieR:Automata"},
        {"id": "w2", "tagName": "work", "tagValue": "NieR Replicant"},
        {"id": "w3", "tagName": "work", "tagValue": "Drakengard"},
        {"id": "g1", "tagName": "genre", "tagValue": "Soundtrack"},
    ]
    fake.artists = [
        {"id": "a1", "name": "Keiichi Okabe"},
        {"id": "a2", "name": "Okabe Ensemble"},
        {"id": "a3", "name": "Someone Else"},
    ]

    page = (
        await mcp_client.call_tool(
            "search_songs",
            {
                "title": "weight",
                "work": "nier",
                "genre": "sound",
                "artist": "okabe",
                "starred_only": True,
            },
        )
    ).data

    params = fake.last("/api/song").url.params
    assert params["title"] == "weight"
    assert params["starred"] == "true"
    assert params.get_list("work") == ["w1", "w2"]
    assert params.get_list("genre") == ["g1"]
    assert params.get_list("artists_id") == ["a1", "a2"]
    assert page.resolved == {
        "work": ["NieR:Automata", "NieR Replicant"],
        "genre": ["Soundtrack"],
        "artist": ["Keiichi Okabe", "Okabe Ensemble"],
    }


async def test_unknown_work_returns_empty_without_querying_songs(mcp_client, fake):
    fake.songs = [song("s1", "x")]

    page = (await mcp_client.call_tool("search_songs", {"work": "zelda"})).data

    assert page.total == 0 and page.songs == []
    assert "list_tag_values" in page.note
    assert not [r for r in fake.requests if r.url.path == "/api/song"]


async def test_list_tag_values(mcp_client, fake: FakeNavidrome):
    fake.tags = [
        {"id": "w1", "tagName": "work", "tagValue": "NieR:Automata", "songCount": 120, "albumCount": 3},
        {"id": "g1", "tagName": "genre", "tagValue": "Rock"},
    ]

    result = (await mcp_client.call_tool("list_tag_values", {"tag": "work"})).structured_content

    assert result["result"] == [{"value": "NieR:Automata", "song_count": 120, "album_count": 3}]


async def test_playlist_lifecycle(mcp_client, fake: FakeNavidrome):
    created = (
        await mcp_client.call_tool(
            "create_playlist", {"name": "NieR top", "song_ids": ["s1", "s2", "s1"]}
        )
    ).data
    pid = created.playlist.id
    assert created.added == 2 and created.playlist.song_count == 2

    added = (
        await mcp_client.call_tool("add_to_playlist", {"playlist_id": pid, "song_ids": ["s2", "s3"]})
    ).data
    assert added.added == 1

    details = (await mcp_client.call_tool("get_playlist", {"playlist_id": pid})).data
    assert [s.id for s in details.songs] == ["s1", "s2", "s3"]

    removed = (
        await mcp_client.call_tool(
            "remove_from_playlist", {"playlist_id": pid, "song_ids": ["s2", "nope"]}
        )
    ).data
    # Row id "2" is deleted, not the song id.
    assert fake.last(f"/api/playlist/{pid}/tracks").url.params.get_list("id") == ["2"]
    assert removed.removed == 1 and removed.not_found == ["nope"]

    listed = (await mcp_client.call_tool("list_playlists", {})).structured_content["result"]
    assert [p["name"] for p in listed] == ["NieR top"]

    await mcp_client.call_tool("delete_playlist", {"playlist_id": pid})
    assert pid not in fake.playlists


async def test_smart_playlist_is_not_editable(mcp_client, fake: FakeNavidrome):
    fake.playlists["smart"] = {"id": "smart", "name": "Auto", "rules": {"all": []}}
    fake.tracks["smart"] = []

    with pytest.raises(ToolError, match="smart playlist"):
        await mcp_client.call_tool("add_to_playlist", {"playlist_id": "smart", "song_ids": ["s1"]})


async def test_navidrome_errors_surface_as_tool_errors(mcp_client):
    with pytest.raises(ToolError, match="404"):
        await mcp_client.call_tool("get_playlist", {"playlist_id": "missing"})
