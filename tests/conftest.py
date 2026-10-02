import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastmcp import Client

from navidrome_mcp.config import Settings
from navidrome_mcp.server import create_server

TOKEN = "test-mcp-token"


def song(id: str, title: str, **extra: Any) -> dict[str, Any]:
    return {"id": id, "title": title, "artist": "Keiichi Okabe", "album": "NieR", **extra}


@dataclass
class FakeNavidrome:
    """In-process stand-in for the Navidrome native API."""

    songs: list[dict[str, Any]] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    artists: list[dict[str, Any]] = field(default_factory=list)
    playlists: dict[str, dict[str, Any]] = field(default_factory=dict)
    tracks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    logins: int = 0
    valid_token: str = "jwt-1"
    reject_next: bool = False

    def last(self, path: str) -> httpx.Request:
        return [r for r in self.requests if r.url.path == path][-1]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/auth/login":
            self.logins += 1
            self.valid_token = f"jwt-{self.logins}"
            return httpx.Response(200, json={"token": self.valid_token, "username": "u"})

        auth = request.headers.get("X-ND-Authorization", "")
        if self.reject_next or auth != f"Bearer {self.valid_token}":
            self.reject_next = False
            return httpx.Response(401, json={"error": "Not authenticated"})
        return self.route(request, path.removeprefix("/api"))

    def route(self, request: httpx.Request, path: str) -> httpx.Response:
        q = request.url.params
        method = request.method
        if path == "/song":
            return _page(self.songs, q)
        if path == "/tag":
            name = (q.get("name") or "").lower()
            items = [
                t for t in self.tags
                if t["tagName"].startswith(q["tag_name"]) and name in t["tagValue"].lower()
            ]
            return _page(items, q)
        if path == "/artist":
            return _page(self.artists, q)
        if path == "/playlist" and method == "GET":
            return _page(list(self.playlists.values()), q)
        if path == "/playlist" and method == "POST":
            assert request.headers["content-type"] == "application/json"
            body = json.loads(request.content)
            pid = f"pl{len(self.playlists) + 1}"
            self.playlists[pid] = {"id": pid, "songCount": 0, **body}
            self.tracks[pid] = []
            return httpx.Response(200, json={"id": pid})

        parts = path.strip("/").split("/")
        pid = parts[1]
        if pid not in self.playlists:
            return httpx.Response(404, json={"error": "not found"})
        if len(parts) == 2 and method == "GET":
            return httpx.Response(200, json=self.playlists[pid])
        if len(parts) == 2 and method == "DELETE":
            del self.playlists[pid]
            return httpx.Response(200, json={})
        tracks = self.tracks[pid]
        if method == "GET":
            return _page(tracks, q)
        if method == "POST":
            ids = json.loads(request.content)["ids"]
            for sid in ids:
                row = str(len(tracks) + 1)
                tracks.append({**song(sid, f"t-{sid}"), "id": row, "mediaFileId": sid})
            self.playlists[pid]["songCount"] = len(tracks)
            return httpx.Response(200, json={"added": len(ids)})
        if method == "DELETE":
            rows = set(q.get_list("id"))
            self.tracks[pid] = [t for t in tracks if t["id"] not in rows]
            self.playlists[pid]["songCount"] = len(self.tracks[pid])
            return httpx.Response(200, json={"ids": list(rows)})
        return httpx.Response(405)


def _page(items: list[dict[str, Any]], q: httpx.QueryParams) -> httpx.Response:
    start, end = int(q.get("_start", 0)), int(q.get("_end", len(items)))
    return httpx.Response(
        200, json=items[start:end], headers={"X-Total-Count": str(len(items))}
    )


@pytest.fixture
def fake() -> FakeNavidrome:
    return FakeNavidrome()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        navidrome_url="http://navidrome:4533",
        navidrome_username="u",
        navidrome_password="p",
        mcp_auth_token=TOKEN,
    )


@pytest.fixture
def server(fake: FakeNavidrome, settings: Settings):
    return create_server(settings, transport=httpx.MockTransport(fake.handler))


@pytest.fixture
async def mcp_client(server):
    async with Client(server) as client:
        yield client
