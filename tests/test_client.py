import httpx
import pytest

from navidrome_mcp.client import NavidromeClient, NavidromeError

from .conftest import FakeNavidrome


def make_client(handler) -> NavidromeClient:
    return NavidromeClient(
        "http://navidrome:4533/", "u", "p", transport=httpx.MockTransport(handler)
    )


async def test_logs_in_lazily_and_sends_token(fake: FakeNavidrome):
    client = make_client(fake.handler)
    items, total = await client.get_list("/song", sort="title")

    assert (items, total) == ([], 0)
    assert fake.logins == 1
    req = fake.last("/api/song")
    assert req.headers["X-ND-Authorization"] == "Bearer jwt-1"
    assert req.headers["X-ND-Client-Unique-Id"]
    assert req.url.params["_sort"] == "title"
    assert req.url.params["_order"] == "ASC"


async def test_relogins_once_on_401(fake: FakeNavidrome):
    client = make_client(fake.handler)
    await client.get_list("/song")
    fake.reject_next = True

    await client.get_list("/song")

    assert fake.logins == 2
    assert fake.last("/api/song").headers["X-ND-Authorization"] == "Bearer jwt-2"


async def test_picks_up_refreshed_token_from_response_header():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"token": "first"})
        seen.append(request.headers["X-ND-Authorization"])
        return httpx.Response(200, json=[], headers={"X-ND-Authorization": "second"})

    client = make_client(handler)
    await client.get_list("/song")
    await client.get_list("/song")

    assert seen == ["Bearer first", "Bearer second"]


async def test_login_failure_is_reported():
    client = make_client(lambda r: httpx.Response(401, json={"error": "Invalid username or password"}))

    with pytest.raises(NavidromeError, match="Invalid username or password"):
        await client.get_list("/song")


async def test_tag_values_keeps_only_exact_tag(fake: FakeNavidrome):
    fake.tags = [
        {"id": "a", "tagName": "work", "tagValue": "NieR:Automata"},
        {"id": "b", "tagName": "workid", "tagValue": "nier-123"},
    ]
    client = make_client(fake.handler)

    tags = await client.tag_values("work", "nier")

    assert [t["id"] for t in tags] == ["a"]
