
import re
import os
import aiohttp
import discord
from bs4 import BeautifulSoup
from discord import app_commands

TOKEN = os.environ["DISCORD_TOKEN"]
BASE_URL = "https://tekkendocs.com/t7"


def normalize_move(move: str) -> str:
    move = re.sub(r"\s+", "", move.lower())
    return re.sub(r"(?<=[a-z])\+(?=\d)", "", move)


def get_frame_data(html: str, requested_move: str):
    soup = BeautifulSoup(html, "html.parser")
    target = normalize_move(requested_move)

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        headers = [
            cell.get_text(" ", strip=True)
            for cell in rows[0].find_all(["th", "td"])
        ]

        if not headers or headers[0].lower() != "command":
            continue

        for tr in rows[1:]:
            cells = [
                cell.get_text(" ", strip=True)
                for cell in tr.find_all(["th", "td"])
            ]

            if len(cells) != len(headers):
                continue

            if normalize_move(cells[0]) == target:
                return dict(zip(headers, cells))

    return None


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@tree.command(
    name="fd",
    description="Look up Tekken 7 move frame data"
)
@app_commands.describe(
    character="Character name, e.g. bryan",
    move="Move notation, e.g. b+1 or b1"
)
async def fd(
    interaction: discord.Interaction,
    character: str,
    move: str
):
    await interaction.response.defer(thinking=True)

    character = re.sub(r"[^a-z0-9]", "", character.lower())
    move = move.strip()

    if not character or not move:
        await interaction.followup.send(
            "Please provide a character and a move."
        )
        return

    url = f"{BASE_URL}/{character}"

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TK7-FD Discord bot)"
        }

        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers
        ) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    await interaction.followup.send(
                        f"Character `{character}` not found on TekkenDocs."
                    )
                    return

                html = await response.text()

        row = get_frame_data(html, move)

        if row is None:
            await interaction.followup.send(
                f"No match for `{move}` found for `{character}` on TekkenDocs."
            )
            return

        command = row.pop("Command", move)

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
            title=f"{character.title()} — {command}",
            url=url,
            color=discord.Color.blurple()
        )

        for key, value in row.items():
            if not value:
                continue

            label = labels.get(key, key)

            embed.add_field(
                name=label,
                value=value,
                inline=True
            )

        await interaction.followup.send(embed=embed)

    except aiohttp.ClientError as error:
        print(f"TekkenDocs request error: {error}")
        await interaction.followup.send(
            "Couldn't connect to TekkenDocs. Please try again later."
        )
    except Exception as error:
        print(f"Frame-data lookup error: {error}")
        await interaction.followup.send(
            "Something went wrong while reading TekkenDocs."
        )


@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")


client.run(TOKEN)
