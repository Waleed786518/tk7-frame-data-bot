
import re
import aiohttp
import discord
from bs4 import BeautifulSoup
from discord import app_commands

import os

TOKEN = os.environ["DISCORD_TOKEN"]
BASE_URL = "https://tekkendocs.com/t7"


def normalize_move(move: str) -> str:
    move = re.sub(r"\s+", "", move.lower())
    # Treat b1 and b+1 as equivalent, while preserving button combinations
    move = re.sub(r"(?<=[a-z])\+(?=\d)", "", move)
    return move


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
    description="Look up Tekken 7 move frame data from TekkenDocs"
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

    if not character or not move.strip():
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
                        f"TekkenDocs returned HTTP {response.status} "
                        f"for `{character}`. Check the character name."
                    )
                    return

                html = await response.text()

        row = get_frame_data(html, move)

        if row is None:
            soup = BeautifulSoup(html, "html.parser")
            has_command_table = any(
                (cells := table.find_all("tr"))
                and cells[0].find(["th", "td"])
                and cells[0].find(["th", "td"]).get_text(
                    " ", strip=True
                ).lower() == "command"
                for table in soup.find_all("table")
            )

            if not has_command_table:
                await interaction.followup.send(
                    "I couldn't read the frame-data table on TekkenDocs. "
                    "The page layout may have changed."
                )
            else:
                await interaction.followup.send(
                    f"No exact match for `{move}` found in TekkenDocs "
                    f"T7 data for `{character}`."
                )
            return

        command = row.pop("Command", move)
        embed = discord.Embed(
            title=f"{character.title()} — {command}",
            url=url,
            description="Source: TekkenDocs · Tekken 7",
            color=discord.Color.blurple()
        )

        for key, value in row.items():
            if value:
                embed.add_field(
                    name=key[:256],
                    value=value[:1024],
                    inline=True
                )

        embed.set_footer(text="Data source: TekkenDocs · Tekken 7")
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
