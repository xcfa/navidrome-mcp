---
name: navidrome
description: Work with the user's Navidrome music library through the navidrome MCP server - find songs, most played / recently played / recently added / random tracks, filter by favourites, WORK tag (game/anime/franchise, e.g. NieR), artist/band, genre or title, and build or edit playlists. Use for any request about their music, listening stats or playlists (музыка, треки, прослушивания, избранное, плейлист, жанр, группа, WORK).
compatibility: Requires the navidrome MCP server (remote HTTP, bearer token) to be connected.
---

# Navidrome music library

The `navidrome` MCP server talks to the user's Navidrome instance. In opencode its tools show up
with the server prefix (`navidrome_search_songs`, ...); below they are named without it.

## Tools

| Tool | Use it for |
|---|---|
| `search_songs` | Any song listing or search. Filters and sorting combine in one call. |
| `list_tag_values` | Discover existing WORK / genre / mood values (song counts only for genres). |
| `list_playlists` | Find a playlist id by name. |
| `get_playlist` | Playlist metadata + songs (paged with `limit`/`offset`). |
| `create_playlist` | New playlist filled with song ids in the given order. |
| `add_to_playlist` | Append songs; skips songs already present by default. |
| `remove_from_playlist` | Remove songs (all occurrences) by song id. |
| `delete_playlist` | Delete a whole playlist. |

### `search_songs` parameters

- `title` - full-text search over title, album and artist words.
- `artist` - artist/band name, partial. `work` - WORK tag value, partial and case-insensitive
  (`"nier"` matches both "NieR:Automata" and "NieR Replicant"). `genre` - partial genre name.
- `starred_only` - only favourites.
- `sort` + `order`:

| User asks for | sort | order |
|---|---|---|
| top / most played / любимое по прослушиваниям | `play_count` | default (desc) |
| least played / rarely listened | `play_count` | `asc` |
| recently played / что слушал недавно | `last_played` | default (desc) |
| not played for a long time | `last_played` | `asc` (never-played songs come first; combine with `play_count` results if needed) |
| new / recently added / новое | `date_added` | default (desc) |
| random / shuffle / что-нибудь случайное | `random` | - |
| alphabetical | `title`, `artist`, `album` | default (asc) |

- `limit` (1-200, default 25), `offset` for paging. The result has `total`; fetch more pages only
  when the user needs them. With `random` don't page - call again for a new shuffle.
- `resolved` in the result shows which artists / works / genres the partial filters matched.
  Check it: if a filter matched something unintended, narrow the value and search again.
  If nothing matched, `note` explains it and `songs` is empty.

## Recipes

**Top songs of a WORK**: `search_songs(work="nier", sort="play_count", limit=20)`.
If the user's name for the work is vague or nothing matched, call
`list_tag_values(tag="work", query="<part>")` first and pick the exact value.

**What works / genres exist**: `list_tag_values(tag="work")` or `list_tag_values(tag="genre")`.

**Mix across several works or genres**: one `search_songs` call per value (a filter takes a single
partial value), then merge the ids, dropping duplicates.

**Build a playlist**:
1. Collect songs with one or more `search_songs` calls; keep only ids from results.
2. Remove duplicates, keep the intended order, respect the requested size.
3. `create_playlist(name=..., song_ids=[...])`. Pick a descriptive name if the user didn't give one.
4. Report the name, song count and a short list of what went in.

**Extend a playlist**: `list_playlists(query="<name>")` → id → `add_to_playlist`. Duplicates
are skipped automatically.

## Rules

- Never invent or guess ids. Song ids come from `search_songs` / `get_playlist`, playlist ids from
  `list_playlists` / `create_playlist`. Don't reuse ids across sessions: Navidrome upgrades can
  change them.
- `delete_playlist` and `remove_from_playlist` change the user's data: confirm with the user
  first, naming the playlist (and songs) affected.
- Smart playlists (`smart: true`) are rule-based and can't be edited; offer to create a regular
  playlist with the same songs instead.
- Keep context small: use a modest `limit`, don't dump raw JSON. Show results as a compact table
  (title, artist, album/work, plays or date when relevant).
