
import os
import re
import aiohttp
import discord
from discord import app_commands

TOKEN = os.environ["DISCORD_TOKEN"]
BASE_URL = "https://tekkendocs.com/api/t7"

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@tree.command(name="fd", description="Look up Tekken 7 move frame data")
@app_commands.describe(
    character="Character name, e.g. bryan",
    move="Move notation, e.g. b1"
)
async def fd(interaction: discord.Interaction, character: str, move: str):
    await interaction.response.defer(thinking=True)

    character = re.sub(r"[^a-z0-9]", "", character.lower())
    url = f"{BASE_URL}/{character}/framedata"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                if response.status != 200:
                    await interaction.followup.send(
                        f"Couldn't find TekkenDocs T7 frame data for `{character}`."
                    )
                    return
                data = await response.json(content_type=None)

        if isinstance(data, dict):
            rows = data.get("moves", data.get("frameData", data))
        else:
            rows = data

        if isinstance(rows, dict):
            rows = list(rows.values()) if all(
                isinstance(v, dict) for v in rows.values()
            ) else [rows]

        if not isinstance(rows, list):
            await interaction.followup.send(
                "TekkenDocs returned an unexpected data format."
            )
            return

        matches = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            notation = str(
                row.get("input", row.get("notation", row.get("command", "")))
            )
            if notation.lower().replace(" ", "") == move.lower().replace(" ", ""):
                matches.append(row)

        if not matches:
            await interaction.followup.send(
                f"No exact match for `{move}` found in TekkenDocs T7 data for `{character}`."
            )
            return

        row = matches[0]
        embed = discord.Embed(
            title=f"{character.title()} — {move}",
            description="Source: TekkenDocs Tekken 7",
            color=discord.Color.blurple()
        )

        for key, value in row.items():
            if value is not None:
                embed.add_field(
                    name=str(key)[:256],
                    value=str(value)[:1024],
                    inline=True
                )

        embed.set_footer(text="Data source: TekkenDocs • Tekken 7")
        await interaction.followup.send(embed=embed)

    except Exception as error:
        print(f"Frame-data lookup error: {error}")
        await interaction.followup.send(
            "Something went wrong while retrieving TekkenDocs frame data."
        )


@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user}")


client.run(TOKEN)
