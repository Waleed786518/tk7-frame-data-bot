
import os
import re
import time

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord import app_commands

TOKEN = os.environ["DISCORD_TOKEN"]

BASE_URL = "https://tekkendocs.com/t7"
CACHE_TTL = 300
REQUEST_TIMEOUT = 20

# TekkenDocs Tekken 7 character slugs.
# Character aliases are intentionally excluded.
CHARACTERS = {
    "akuma": "Akuma",
    "alisa": "Alisa",
    "anna": "Anna",
    "armor-king": "Armor King",
    "asuka": "Asuka",
    "bob": "Bob",
    "bryan": "Bryan",
    "claudio": "Claudio",
    "devil-jin": "Devil Jin",
    "dragunov": "Dragunov",
    "eddy": "Eddy",
    "eliza": "Eliza",
    "fahkumram": "Fahkumram",
    "feng": "Feng",
    "ganryu": "Ganryu",
    "geese": "Geese",
    "gigas": "Gigas",
    "heihachi": "Heihachi",
    "hwoarang": "Hwoarang",
    "jack-7": "Jack 7",
    "jin": "Jin",
    "josie": "Josie",
    "julia": "Julia",
    "katarina": "Katarina",
    "kazumi": "Kazumi",
    "kazuya": "Kazuya",
    "king": "King",
    "kuma": "Kuma",
    "kunimitsu": "Kunimitsu",
    "lars": "Lars",
    "law": "Law",
    "lee": "Lee",
    "lei": "Lei",
    "leo": "Leo",
    "leroy": "Leroy",
    "lidia": "Lidia",
    "lili": "Lili",
    "lucky-chloe": "Lucky Chloe",
    "marduk": "Marduk",
    "master-raven": "Master Raven",
    "miguel": "Miguel",
    "negan": "Negan",
    "nina": "Nina",
    "noctis": "Noctis",
    "paul": "Paul",
    "shaheen": "Shaheen",
    "steve": "Steve",
    "xiaoyu": "Xiaoyu",
    "yoshimitsu": "Yoshimitsu",
    "zafina": "Zafina",
}

# character slug -> (timestamp, HTML)
page_cache = {}


def normalize_character(character: str) -> str:
    """Normalize a character's spelling and spacing, not aliases."""
    character = character.strip().lower()
    character = re.sub(r"\s+", "-", character)
    return re.sub(r"[^a-z0-9-]", "", character)


def normalize_move(move: str) -> str:
    """
    Normalize harmless notation differences.
    Keep plus signs between buttons and commas between moves.
    """
    move = re.sub(r"\s+", "", move.lower())

    # Diagonal direction shortcuts:
    # df3 / df+3 -> d/f+3
    # db2 / db+2 -> d/b+2
    # uf4 / uf+4 -> u/f+4
    # ub1 / ub+1 -> u/b+1
    move = re.sub(r"(?<![a-z])df(?=\+?\d)", "d/f", move)
    move = re.sub(r"(?<![a-z])db(?=\+?\d)", "d/b", move)
    move = re.sub(r"(?<![a-z])uf(?=\+?\d)", "u/f", move)
    move = re.sub(r"(?<![a-z])ub(?=\+?\d)", "u/b", move)

    # ff2 / ff+2 -> f,f+2.
    # Only normalize double-forward notation before a button number.
    move = re.sub(r"(?<![a-z])ff(?=\+?\d)", "f,f", move)

    # Allow b1 / b+1 and f1+2 / f+1+2 to match.
    # This does NOT remove the plus between 1 and 2.
    move = re.sub(r"(?<=[a-z])\+(?=\d)", "", move)

    return move


def get_frame_data(html: str, requested_move: str):
    soup = BeautifulSoup(html, "html.parser")
    target = normalize_move(requested_move)

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        header_index = None
        headers = []

        # Find the actual Command header row.
        for index, tr in enumerate(rows):
            candidate = [
                cell.get_text(" ", strip=True)
                for cell in tr.find_all(["th", "td"])
            ]

            if candidate and candidate[0].strip().lower() == "command":
                header_index = index
                headers = candidate
                break

        if header_index is None:
            continue

        for tr in rows[header_index + 1:]:
            cells = [
                cell.get_text(" ", strip=True)
                for cell in tr.find_all(["th", "td"])
            ]

            if len(cells) != len(headers) or not cells:
                continue

            if not cells[0]:
                continue

            if normalize_move(cells[0]) == target:
                return dict(zip(headers, cells))

    return None


def page_has_command_table(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])

            if (
                cells
                and cells[0].get_text(" ", strip=True).lower()
                == "command"
            ):
                return True

    return False


async def fetch_character_page(character: str) -> str:
    """Fetch a page and reuse a cached copy for five minutes."""
    now = time.monotonic()
    cached = page_cache.get(character)

    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    url = f"{BASE_URL}/{character}"
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TK7-FD Discord bot)"
    }

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers,
    ) as session:
        async with session.get(url) as response:
            if response.status == 404:
                raise ValueError("CHARACTER_NOT_FOUND")

            if response.status == 429:
                raise ValueError("RATE_LIMITED")

            if response.status >= 500:
                raise ValueError("WEBSITE_ERROR")

            if response.status != 200:
                raise ValueError("REQUEST_ERROR")

            html = await response.text()

    if not html.strip():
        raise ValueError("EMPTY_PAGE")

    if not page_has_command_table(html):
        raise ValueError("UNEXPECTED_PAGE")

    page_cache[character] = (time.monotonic(), html)
    return html


class TK7Client(discord.Client):
    async def setup_hook(self):
        # Register slash commands once when the bot starts.
        await tree.sync()


intents = discord.Intents.default()
client = TK7Client(intents=intents)
tree = app_commands.CommandTree(client)


@tree.command(
    name="fd",
    description="Look up Tekken 7 move frame data",
)
@app_commands.describe(
    character="Tekken 7 character",
    move="Move notation, e.g. b+1, df+3, or 1,2",
)
async def fd(
    interaction: discord.Interaction,
    character: str,
    move: str,
):
    await interaction.response.defer(thinking=True)

    character = normalize_character(character)
    move = move.strip()

    if not character or character not in CHARACTERS:
        await interaction.followup.send(
            "Character not found. Select a valid Tekken 7 character."
        )
        return

    if not move:
        await interaction.followup.send(
            "Enter a move, e.g. `/fd bryan b+1`."
        )
        return

    url = f"{BASE_URL}/{character}"

    try:
        html = await fetch_character_page(character)
        row = get_frame_data(html, move)

        if row is None:
            await interaction.followup.send(
                f"Move `{move}` not found. Check notation, "
                "e.g. `d/f+1`, and try again."
            )
            return

        command = row.get("Command", move)

        labels = {
            "Hit level": "Hit level",
            "Damage": "Damage",
            "Start up frame": "Startup",
            "Startup frame": "Startup",
            "Block frame": "Block",
            "Hit frame": "Hit",
            "Counter hit frame": "Counter hit",
        }

        embed = discord.Embed(
            title=f"{CHARACTERS[character]} — {command}",
            url=url,
            color=discord.Color.blurple(),
        )

        field_count = 0

        for key, value in row.items():
            if key.strip().lower() == "command" or not value:
                continue

            embed.add_field(
                name=labels.get(key, key),
                value=value[:1024],
                inline=True,
            )
            field_count += 1

        if field_count == 0:
            await interaction.followup.send(
                "Frame data unreadable. Try again later."
            )
            return

        embed.set_footer(text="Source: TekkenDocs · Tekken 7")
        await interaction.followup.send(embed=embed)

    except ValueError as error:
        messages = {
            "CHARACTER_NOT_FOUND":
                "Character page not found. Check the character name.",
            "RATE_LIMITED":
                "Rate limited by TekkenDocs. Wait, then retry.",
            "WEBSITE_ERROR":
                "TekkenDocs server error. Try again shortly.",
            "REQUEST_ERROR":
                "TekkenDocs request failed. Try again shortly.",
            "EMPTY_PAGE":
                "TekkenDocs returned an empty page. Retry shortly.",
            "UNEXPECTED_PAGE":
                "Frame data unreadable. Try again later.",
        }

        await interaction.followup.send(
            messages.get(str(error), "Lookup failed. Try again.")
        )

    except (aiohttp.ClientError, TimeoutError) as error:
        print(f"TekkenDocs connection error: {error}")
        await interaction.followup.send(
            "TekkenDocs unavailable or timed out. Retry shortly."
        )

    except Exception as error:
        print(f"Frame-data lookup error: {error}")
        await interaction.followup.send(
            "Lookup failed. Retry; report it if it persists."
        )


@fd.autocomplete("character")
async def character_autocomplete(
    interaction: discord.Interaction,
    current: str,
):
    query = normalize_character(current)

    matches = [
        app_commands.Choice(name=name, value=slug)
        for slug, name in CHARACTERS.items()
        if query in slug or query in name.lower()
    ]

    return matches[:25]


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")


client.run(TOKEN)
