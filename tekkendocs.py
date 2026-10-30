
import asyncio
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import aiohttp


BASE_URL = "https://tekkendocs.com"
ROSTER_URL = f"{BASE_URL}/t7/"
API_URL = f"{BASE_URL}/api/t7/{{slug}}/framedata"

CACHE_TTL = 300
REQUEST_TIMEOUT = 20
CACHE_DIR = Path("data") / "tekkendocs"

HEADERS = {
    "User-Agent": "Tekken7FrameDataBot/1.0"
}


class TekkenDocsError(Exception):
    pass


class SourceUnavailable(TekkenDocsError):
    pass


class InvalidFrameData(TekkenDocsError):
    pass


class CharacterNotFound(TekkenDocsError):
    pass


class RosterParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            attrs = dict(attrs)
            self.href = attrs.get("href")
            self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href is not None:
            self.links.append(
                (self.href, " ".join("".join(self.text).split()))
            )
            self.href = None
            self.text = []


def slugify(value):
    value = value.strip().lower()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def normalize_move(value):
    """
    Normalize Tekken move notation so shorthand and slash notation match.

    Examples:
        uf2   -> u/f2
        u/f2  -> u/f2
        u/f+2 -> u/f2
        df1   -> d/f1
        db2   -> d/b2
        ub2   -> u/b2
        ff2   -> f,f2
        u     -> u
        1+2   -> 1+2
    """
    value = str(value).casefold().strip()
    value = re.sub(r"\s+", "", value)

    # Convert shorthand diagonal directions to slash notation.
    # This deliberately requires a following button number so that
    # direction-only inputs such as "u" remain unchanged.
    value = re.sub(
        r"(?<![a-z])([ud])([fb])(?=\+?\d)",
        r"\1/\2",
        value,
    )

    # Normalize forward dash notation.
    value = re.sub(
        r"(?<![a-z])ff(?=\+?\d)",
        "f,f",
        value,
    )

    # Remove optional plus between a direction and its button.
    # This does not remove button combinations such as 1+2.
    value = re.sub(
        r"([dfub])\+(?=\d)",
        r"\1",
        value,
    )

    return value


def extract_roster(html):
    parser = RosterParser()
    parser.feed(html)

    roster = {}

    for href, label in parser.links:
        if not href or not label:
            continue

        parsed = urlparse(href)
        parts = [part for part in parsed.path.split("/") if part]

        if len(parts) < 2 or parts[0].lower() != "t7":
            continue

        slug = parts[1].lower()

        if not re.fullmatch(r"[a-z0-9-]+", slug):
            continue

        if slug in {
            "all",
            "characters",
            "guides",
            "features",
            "stats",
            "challenges",
            "meta",
            "rank",
        }:
            continue

        roster[slug] = label

    if not roster:
        raise InvalidFrameData(
            "Could not discover characters from the TekkenDocs T7 page."
        )

    return dict(
        sorted(roster.items(), key=lambda item: item[1].casefold())
    )


def validate_character_data(data, slug):
    if not isinstance(data, dict):
        raise InvalidFrameData("Frame-data JSON is not an object.")

    name = data.get("characterName")
    moves = data.get("framesNormal")

    if not isinstance(name, str) or not name.strip():
        raise InvalidFrameData("Missing characterName in JSON.")

    if not isinstance(moves, list) or not moves:
        raise InvalidFrameData("Missing or empty framesNormal list.")

    required = (
        "command",
        "hitLevel",
        "damage",
        "startup",
        "block",
        "hit",
        "counterHit",
    )

    cleaned_moves = []

    for move in moves:
        if not isinstance(move, dict):
            continue

        command = move.get("command")

        if not isinstance(command, str) or not command.strip():
            continue

        cleaned = dict(move)

        for key in required:
            value = cleaned.get(key, "")
            cleaned[key] = "" if value is None else str(value)

        if cleaned.get("notes") is None:
            cleaned["notes"] = ""

        cleaned_moves.append(cleaned)

    if not cleaned_moves:
        raise InvalidFrameData("No valid moves were found in the JSON.")

    return {
        "characterName": name.strip(),
        "characterSlug": slug,
        "framesNormal": cleaned_moves,
    }


class TekkenDocs:
    def __init__(self):
        self.session = None
        self.roster = {}
        self.roster_fetched_at = 0
        self.roster_lock = asyncio.Lock()
        self.character_locks = {}

    async def start(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers=HEADERS,
        )

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _request_text(self, url):
        try:
            async with self.session.get(url) as response:
                if response.status == 404:
                    raise SourceUnavailable(
                        "TekkenDocs page was not found."
                    )

                if response.status == 429:
                    raise SourceUnavailable(
                        "TekkenDocs rate-limited the bot."
                    )

                if response.status >= 500:
                    raise SourceUnavailable(
                        f"TekkenDocs server error: {response.status}"
                    )

                if response.status != 200:
                    raise SourceUnavailable(
                        f"TekkenDocs returned HTTP {response.status}."
                    )

                body = await response.text()

                if not body.strip():
                    raise SourceUnavailable(
                        "TekkenDocs returned an empty page."
                    )

                return body

        except asyncio.TimeoutError as exc:
            raise SourceUnavailable(
                "TekkenDocs request timed out."
            ) from exc

        except aiohttp.ClientError as exc:
            raise SourceUnavailable(
                "Could not connect to TekkenDocs."
            ) from exc

    async def _request_json(self, url):
        try:
            async with self.session.get(url) as response:
                if response.status == 404:
                    raise SourceUnavailable(
                        "Character JSON endpoint was not found."
                    )

                if response.status == 429:
                    raise SourceUnavailable(
                        "TekkenDocs rate-limited the bot."
                    )

                if response.status >= 500:
                    raise SourceUnavailable(
                        f"TekkenDocs server error: {response.status}"
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
            raise SourceUnavailable(
                "TekkenDocs request timed out."
            ) from exc

        except aiohttp.ClientError as exc:
            raise SourceUnavailable(
                "Could not connect to TekkenDocs."
            ) from exc

    def _roster_cache_path(self):
        return CACHE_DIR / "roster.json"

    def _load_roster_cache(self):
        path = self._roster_cache_path()

        try:
            with path.open("r", encoding="utf-8") as file:
                cached = json.load(file)

            roster = cached["roster"]
            fetched_at = float(cached["fetched_at"])

            if not isinstance(roster, dict) or not roster:
                return None

            return roster, fetched_at

        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_roster_cache(self, roster):
        path = self._roster_cache_path()
        temporary = path.with_suffix(".tmp")

        payload = {
            "fetched_at": time.time(),
            "roster": roster,
        }

        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)

        temporary.replace(path)

    async def get_roster(self):
        now = time.time()

        if (
            self.roster
            and now - self.roster_fetched_at < CACHE_TTL
        ):
            return self.roster

        async with self.roster_lock:
            now = time.time()

            if (
                self.roster
                and now - self.roster_fetched_at < CACHE_TTL
            ):
                return self.roster

            disk_cache = self._load_roster_cache()

            if disk_cache:
                cached_roster, cached_time = disk_cache

                if now - cached_time < CACHE_TTL:
                    self.roster = cached_roster
                    self.roster_fetched_at = cached_time
                    return self.roster

            try:
                html = await self._request_text(ROSTER_URL)
                roster = extract_roster(html)

                self._save_roster_cache(roster)

                self.roster = roster
                self.roster_fetched_at = time.time()

                return self.roster

            except (SourceUnavailable, InvalidFrameData):
                if self.roster:
                    return self.roster

                if disk_cache:
                    self.roster = disk_cache[0]
                    self.roster_fetched_at = disk_cache[1]
                    return self.roster

                raise

    def _character_cache_path(self, slug):
        return CACHE_DIR / f"{slug}.json"

    def _load_character_cache(self, slug):
        path = self._character_cache_path(slug)

        try:
            with path.open("r", encoding="utf-8") as file:
                cached = json.load(file)

            data = validate_character_data(cached["data"], slug)
            fetched_at = float(cached["fetched_at"])

            return data, fetched_at

        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            InvalidFrameData,
        ):
            return None

    def _save_character_cache(self, slug, data):
        path = self._character_cache_path(slug)
        temporary = path.with_suffix(".tmp")

        payload = {
            "fetched_at": time.time(),
            "data": data,
        }

        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)

        temporary.replace(path)

    async def get_character(self, slug):
        slug = slugify(slug)

        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise CharacterNotFound("Invalid character name.")

        lock = self.character_locks.setdefault(
            slug,
            asyncio.Lock(),
        )

        async with lock:
            cached = self._load_character_cache(slug)
            now = time.time()

            if cached and now - cached[1] < CACHE_TTL:
                return cached[0], False

            try:
                raw = await self._request_json(
                    API_URL.format(slug=slug)
                )

                data = validate_character_data(raw, slug)

                # Replace the old cache only after validating fresh JSON.
                self._save_character_cache(slug, data)

                return data, False

            except (SourceUnavailable, InvalidFrameData):
                if cached:
                    return cached[0], True

                raise


source = TekkenDocs()
