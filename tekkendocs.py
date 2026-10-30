```python
import asyncio
import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import aiohttp


BASE_URL = "https://tekkendocs.com"
T7_PAGE_URL = f"{BASE_URL}/t7"
API_URL = f"{BASE_URL}/api/t7/{{slug}}/framedata"

CACHE_TTL = 300
REQUEST_TIMEOUT = 20
CACHE_DIR = Path("data") / "tekkendocs"

HEADERS = {
    "User-Agent": "Tekken7FrameDataDiscordBot/1.0"
}


class TekkenDocsError(Exception):
    pass


class SourceUnavailable(TekkenDocsError):
    pass


class CharacterNotFound(TekkenDocsError):
    pass


class InvalidFrameData(TekkenDocsError):
    pass


class _RosterParser(HTMLParser):
    """Collect character links from the TekkenDocs T7 page."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            attrs = dict(attrs)
            self._href = attrs.get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            label = " ".join("".join(self._text).split())
            self.links.append((self._href, label))
            self._href = None
            self._text = []


def _slugify(value):
    value = value.strip().lower()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _valid_slug(value):
    return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value))


def _extract_roster(html):
    parser = _RosterParser()
    parser.feed(html)

    characters = {}

    excluded = {
        "t7", "t8", "features", "credits", "about", "all",
        "login", "register", "settings", "guides", "meta",
        "challenges", "stats", "rank", "leaderboards"
    }

    for href, label in parser.links:
        if not href or not label:
            continue

        parsed = urlparse(href)
        path = parsed.path.strip("/")
        parts = [part for part in path.split("/") if part]

        if not parts:
            continue

        # Accept character links in either /character or /t7/character form.
        if len(parts) == 2 and parts[0].lower() == "t7":
            slug = parts[1].lower()
        elif len(parts) == 1:
            slug = parts[0].lower()
        else:
            continue

        if slug in excluded or not _valid_slug(slug):
            continue

        # Ignore links that are not character labels.
        if len(label) > 40:
            continue

        characters[slug] = label

    if not characters:
        raise InvalidFrameData(
            "TekkenDocs roster page did not expose any character links."
        )

    return dict(sorted(characters.items(), key=lambda item: item[1].casefold()))


def _validate_frame_data(data, slug):
    if not isinstance(data, dict):
        raise InvalidFrameData("The JSON response was not an object.")

    character_name = data.get("characterName")
    moves = data.get("framesNormal")

    if not isinstance(character_name, str) or not character_name.strip():
        raise InvalidFrameData("The JSON response has no characterName.")

    if not isinstance(moves, list) or not moves:
        raise InvalidFrameData("The JSON response has no valid framesNormal list.")

    valid_moves = []

    required_fields = (
        "command",
        "hitLevel",
        "damage",
        "startup",
        "block",
        "hit",
        "counterHit",
    )

    for move in moves:
        if not isinstance(move, dict):
            continue

        command = move.get("command")
        if not isinstance(command, str) or not command.strip():
            continue

        # Preserve the source fields, including optional notes and identifiers.
        cleaned = dict(move)
        for field in required_fields:
            value = cleaned.get(field, "")
            if value is None:
                cleaned[field] = ""
            elif not isinstance(value, (str, int, float)):
                cleaned[field] = str(value)

        valid_moves.append(cleaned)

    if not valid_moves:
        raise InvalidFrameData("No usable moves were found in framesNormal.")

    return {
        "characterName": character_name.strip(),
        "characterSlug": slug,
        "framesNormal": valid_moves,
    }


class TekkenDocs:
    def __init__(self):
        self.session = None
        self.roster = {}
        self.roster_loaded_at = 0
        self.roster_lock = asyncio.Lock()
        self.character_locks = {}

    async def start(self):
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers=HEADERS
        )
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _get_text(self, url):
        try:
            async with self.session.get(url) as response:
                if response.status == 404:
                    raise SourceUnavailable("TekkenDocs returned 404.")
                if response.status == 429:
                    raise SourceUnavailable("TekkenDocs rate-limited the request.")
                if response.status >= 500:
                    raise SourceUnavailable(
                        f"TekkenDocs server error ({response.status})."
                    )
                if response.status != 200:
                    raise SourceUnavailable(
                        f"TekkenDocs returned HTTP {response.status}."
                    )

                text = await response.text()
                if not text.strip():
                    raise SourceUnavailable("TekkenDocs returned an empty response.")
                return text

        except asyncio.TimeoutError as exc:
            raise SourceUnavailable("TekkenDocs request timed out.") from exc
        except aiohttp.ClientError as exc:
            raise SourceUnavailable("Could not connect to TekkenDocs.") from exc

    async def _get_json(self, url):
        try:
            async with self.session.get(url) as response:
                if response.status == 404:
                    raise SourceUnavailable("Frame-data endpoint was not found.")
                if response.status == 429:
                    raise SourceUnavailable("TekkenDocs rate-limited the request.")
                if response.status >= 500:
                    raise SourceUnavailable(
                        f"TekkenDocs server error ({response.status})."
                    )
                if response.status != 200:
                    raise SourceUnavailable(
                        f"TekkenDocs returned HTTP {response.status}."
                    )

                try:
                    return await response.json(content_type=None)
                except (aiohttp.ContentTypeError, json.JSONDecodeError) as exc:
                    raise InvalidFrameData(
                        "TekkenDocs returned invalid JSON."
                    ) from exc

        except asyncio.TimeoutError as exc:
            raise SourceUnavailable("TekkenDocs request timed out.") from exc
        except aiohttp.ClientError as exc:
            raise SourceUnavailable("Could not connect to TekkenDocs.") from exc

    async def get_roster(self, force_refresh=False):
        now = time.monotonic()

        if (
            self.roster
            and not force_refresh
            and now - self.roster_loaded_at < CACHE_TTL
        ):
            return self.roster

        async with self.roster_lock:
            now = time.monotonic()
            if (
                self.roster
                and not force_refresh
                and now - self.roster_loaded_at < CACHE_TTL
            ):
                return self.roster

            try:
                html = await self._get_text(T7_PAGE_URL)
                roster = _extract_roster(html)
                self.roster = roster
                self.roster_loaded_at = time.monotonic()
                return self.roster

            except (SourceUnavailable, InvalidFrameData):
                if self.roster:
                    return self.roster
                raise

    def _cache_path(self, slug):
        return CACHE_DIR / f"{slug}.json"

    def _read_cache(self, slug):
        path = self._cache_path(slug)
        if not path.exists():
            return None

        try:
            with path.open("r", encoding="utf-8") as file:
                cached = json.load(file)

            data = _validate_frame_data(cached["data"], slug)
            fetched_at = float(cached["fetched_at"])
            return data, fetched_at

        except (OSError, ValueError, KeyError, TypeError, InvalidFrameData):
            return None

    def _write_cache(self, slug, data):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(slug)
        temporary = path.with_suffix(".tmp")

        payload = {
            "fetched_at": time.time(),
            "data": data,
        }

        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)

        temporary.replace(path)

    async def get_character(self, slug):
        slug = _slugify(slug)

        if not _valid_slug(slug):
            raise CharacterNotFound("Invalid character name.")

        lock = self.character_locks.setdefault(slug, asyncio.Lock())

        async with lock:
            cached = self._read_cache(slug)
            now = time.time()

            if cached:
                cached_data, fetched_at = cached
                if now - fetched_at < CACHE_TTL:
                    return cached_data, False

            try:
                raw_data = await self._get_json(API_URL.format(slug=slug))
                data = _validate_frame_data(raw_data, slug)

                # Only replace a cache after the new response passes validation.
                self._write_cache(slug, data)
                return data, False

            except (SourceUnavailable, InvalidFrameData):
                if cached:
                    return cached[0], True
                raise


source = TekkenDocs()
```
