"""Thin async client for the Navidrome native REST API (`/auth/login`, `/api/*`)."""

import asyncio
import uuid
from typing import Any

import httpx

AUTH_HEADER = "X-ND-Authorization"
CLIENT_ID_HEADER = "X-ND-Client-Unique-Id"

Params = list[tuple[str, str | int]]


class NavidromeError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NavidromeClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._username = username
        self._password = password
        self._token: str | None = None
        self._login_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={CLIENT_ID_HEADER: str(uuid.uuid4())},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- auth -------------------------------------------------------------

    async def login(self) -> None:
        resp = await self._call(
            "POST", "/auth/login", json={"username": self._username, "password": self._password}
        )
        if resp.status_code != 200:
            raise NavidromeError(
                f"Navidrome login failed (HTTP {resp.status_code}): {_error_text(resp)}",
                resp.status_code,
            )
        self._token = resp.json()["token"]

    async def _ensure_token(self, stale: str | None = None) -> str:
        async with self._login_lock:
            # Another task may have refreshed the token while we waited for the lock.
            if self._token is None or self._token == stale:
                await self.login()
            assert self._token is not None
            return self._token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Params | None = None,
        json: Any = None,
    ) -> httpx.Response:
        token = self._token or await self._ensure_token()
        resp = await self._send(method, path, token, params, json)
        if resp.status_code == 401:
            token = await self._ensure_token(stale=token)
            resp = await self._send(method, path, token, params, json)
        if resp.status_code >= 400:
            raise NavidromeError(
                f"{method} {path} failed (HTTP {resp.status_code}): {_error_text(resp)}",
                resp.status_code,
            )
        return resp

    async def _send(
        self, method: str, path: str, token: str, params: Params | None, json: Any
    ) -> httpx.Response:
        resp = await self._call(
            method,
            f"/api{path}",
            params=params,
            json=json,
            headers={AUTH_HEADER: f"Bearer {token}"},
        )
        # Navidrome slides the session: every authenticated response carries a fresh JWT.
        if refreshed := resp.headers.get(AUTH_HEADER):
            self._token = refreshed.removeprefix("Bearer ").strip()
        return resp

    async def _call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise NavidromeError(
                f"Navidrome is unreachable at {self._http.base_url}: {exc or type(exc).__name__}"
            ) from exc

    async def get_list(
        self,
        path: str,
        params: Params | None = None,
        *,
        offset: int = 0,
        limit: int = 50,
        sort: str | None = None,
        order: str = "ASC",
    ) -> tuple[list[dict[str, Any]], int]:
        query: Params = list(params or [])
        query += [("_start", offset), ("_end", offset + limit)]
        if sort:
            query += [("_sort", sort), ("_order", order.upper())]
        resp = await self.request("GET", path, params=query)
        items = resp.json() or []
        total = int(resp.headers.get("X-Total-Count", len(items)))
        return items, total

    # --- library ----------------------------------------------------------

    async def songs(
        self,
        filters: Params,
        *,
        sort: str,
        order: str,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self.get_list(
            "/song", filters, offset=offset, limit=limit, sort=sort, order=order
        )

    async def tag_values(
        self, tag_name: str, query: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        params: Params = [("tag_name", tag_name)]
        if query:
            params.append(("name", query))
        items, _ = await self.get_list("/tag", params, limit=limit, sort="name")
        # `tag_name` is a prefix match on the server side; keep only the exact tag.
        return [t for t in items if t.get("tagName") == tag_name]

    async def artists(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        items, _ = await self.get_list("/artist", [("name", query)], limit=limit, sort="name")
        return items

    # --- playlists --------------------------------------------------------

    async def playlists(self, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        params: Params = [("q", query)] if query else []
        items, _ = await self.get_list("/playlist", params, limit=limit, sort="name")
        return items

    async def playlist(self, playlist_id: str) -> dict[str, Any]:
        resp = await self.request("GET", f"/playlist/{playlist_id}")
        return resp.json()

    async def playlist_tracks(
        self, playlist_id: str, *, offset: int = 0, limit: int = 100
    ) -> tuple[list[dict[str, Any]], int]:
        return await self.get_list(
            f"/playlist/{playlist_id}/tracks", offset=offset, limit=limit, sort="id"
        )

    async def create_playlist(self, name: str, comment: str | None, public: bool) -> str:
        body = {"name": name, "comment": comment or "", "public": public}
        resp = await self.request("POST", "/playlist", json=body)
        return resp.json()["id"]

    async def add_tracks(self, playlist_id: str, song_ids: list[str]) -> int:
        resp = await self.request(
            "POST", f"/playlist/{playlist_id}/tracks", json={"ids": song_ids}
        )
        return int((resp.json() or {}).get("added", len(song_ids)))

    async def remove_tracks(self, playlist_id: str, track_row_ids: list[str]) -> None:
        await self.request(
            "DELETE",
            f"/playlist/{playlist_id}/tracks",
            params=[("id", i) for i in track_row_ids],
        )

    async def delete_playlist(self, playlist_id: str) -> None:
        await self.request("DELETE", f"/playlist/{playlist_id}")


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    if isinstance(data, dict):
        return str(data.get("error") or data.get("message") or data)[:300]
    return str(data)[:300]
