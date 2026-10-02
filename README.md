# navidrome-mcp

MCP-сервер для [Navidrome](https://www.navidrome.org/) на Python + [FastMCP](https://gofastmcp.com) и skill, описывающий работу с ним.

Возможности:
- треки с сортировкой по числу прослушиваний, дате последнего прослушивания, дате добавления, случайные;
- поиск с фильтрами: избранное, WORK (тег `work`, например «NieR»), исполнитель/группа, жанр, название. Фильтры и сортировки комбинируются;
- плейлисты: список, просмотр, создание, добавление и удаление треков, удаление плейлиста.

Сервер работает через нативный REST API Navidrome (`/api/*`, Navidrome 0.55+). Subsonic API не умеет сортировать треки по количеству прослушиваний.

## Инструменты

| Инструмент | Назначение |
|---|---|
| `search_songs` | Поиск и выборка треков. Параметры: `title`, `artist`, `work`, `genre`, `starred_only`, `sort` (`play_count`, `last_played`, `date_added`, `random`, `title`, `artist`, `album`), `order`, `limit`, `offset` |
| `list_tag_values` | Значения тега (`work`, `genre`, `mood`, …); количество треков Navidrome считает только для жанров |
| `list_playlists` / `get_playlist` | Список плейлистов, плейлист с треками |
| `create_playlist` / `add_to_playlist` / `remove_from_playlist` / `delete_playlist` | Редактирование плейлистов |

Фильтры `work`, `genre` и `artist` принимают часть названия. Например, `work="nier"` найдёт и «NieR:Automata», и «NieR Replicant». Что именно совпало, сервер возвращает в поле `resolved`.

WORK читается из стандартного тега Navidrome `work`: ID3 `TIT1`, `TXXX:WORK`, MP4 `©wrk`, Vorbis `WORK`.

## Запуск в Docker

```bash
cp .env.example .env   # заполнить NAVIDROME_URL, NAVIDROME_USERNAME, NAVIDROME_PASSWORD, MCP_AUTH_TOKEN
docker compose up -d --build
```

| Переменная | Описание |
|---|---|
| `NAVIDROME_URL` | Адрес Navidrome, доступный из контейнера (например `http://navidrome:4533`) |
| `NAVIDROME_USERNAME` / `NAVIDROME_PASSWORD` | Учётная запись Navidrome. Плейлисты создаются от её имени |
| `MCP_AUTH_TOKEN` | Bearer-токен для MCP-клиентов, сгенерировать: `openssl rand -hex 32`. Без него сервер не запускается |
| `MCP_PORT` | Порт, по умолчанию `8000` |
| `NAVIDROME_TIMEOUT` | Таймаут запросов к Navidrome в секундах, по умолчанию `30` |

- MCP endpoint: `http://<host>:8000/mcp`. Нужен заголовок `Authorization: Bearer <MCP_AUTH_TOKEN>`.
- Healthcheck: `GET /health`, без авторизации.

Токен проверяется на уровне HTTP-транспорта по стандартной схеме авторизации MCP. Заголовок подставляет MCP-клиент, в контекст модели он не попадает.

Готовый образ публикуется в `ghcr.io/xcfa/navidrome-mcp` при пуше тега `vX.Y.Z` (см. `.github/workflows/publish.yml`).

## Подключение к opencode

В `opencode.json` (проектный или `~/.config/opencode/opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "navidrome": {
      "type": "remote",
      "url": "http://localhost:8000/mcp",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer {env:NAVIDROME_MCP_TOKEN}"
      }
    }
  }
}
```

- `NAVIDROME_MCP_TOKEN` — переменная окружения со значением `MCP_AUTH_TOKEN`.
- `"oauth": false` отключает OAuth-discovery, раз используется статический токен.
- Skill: скопировать `skills/navidrome` в `~/.config/opencode/skills/navidrome/` (глобально) или в `.opencode/skills/navidrome/` (в проект).

## Подключение к Claude Code

```bash
claude mcp add --transport http navidrome http://localhost:8000/mcp --header "Authorization: Bearer $NAVIDROME_MCP_TOKEN"
```

Skill: скопировать `skills/navidrome` в `~/.claude/skills/navidrome/`.

## Разработка

```bash
uv sync
uv run pytest
uv run navidrome-mcp   # читает .env из текущей директории
```

Тесты используют поддельный Navidrome на `httpx.MockTransport` и in-memory клиент FastMCP. Живой сервер для них не нужен.

## Релиз

1. Поднять `version` в `pyproject.toml` и закоммитить.
2. `git tag vX.Y.Z && git push origin master --tags`.
3. Workflow прогонит тесты, сверит тег с версией и опубликует multi-arch образ (`amd64`, `arm64`) в GHCR с тегами `X.Y.Z`, `X.Y` и `latest`.
