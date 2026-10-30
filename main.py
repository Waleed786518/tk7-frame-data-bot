```python
import os
import re
import discord

from discord import app_commands
from discord.ext import commands

from tekkendocs import (
    source,
    TekkenDocsError,
    SourceUnavailable,
    CharacterNotFound,
)


TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()


def normalize_character(value):
    value = value.strip().lower()
    value = value.replace("&", "and")
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    return value.strip("-")


def normalize_move(value):
    value = value.strip().casefold()
    value = re.sub(r"\s+", "", value)

    replacements = {
        "df": "d/f",
        "db": "d/b",
        "uf": "u/f",
        "ub": "u/b",
        "ff": "f,f",
    }

    for old, new in replacements.items():
        value = re.sub(rf"(?<![a-z]){old}(?=\+?\d)", new, value)

    # d/f1 becomes d/f+1; 1+2 remains unchanged.
    value = re.sub(r"([dfub])(\d)", r"\1+\2", value)
    value = re.sub(r"([dfub])([dfub])(\d)", r"\1/\2+\3", value)

    return value


def move_matches(query, command):
    return normalize_move(query) == normalize_move(command)


def make_embed(character_name, move, stale=False):
    slug = move.get("characterId", "")
    command = move.get("command", "Unknown move")

    embed = discord.Embed(
        title=f"{character_name} — {command}",
        url=f"https://tekkendocs.com/{slug}/{command}",
        color=discord.Color.blurple(),
    )

    fields = [
        ("Hit level", "hitLevel"),
        ("Damage", "damage"),
        ("Startup", "startup"),
        ("On block", "block"),
        ("On hit", "hit"),
        ("Counter hit", "counterHit"),
    ]

    for label, key in fields:
        value = move.get(key)
        if value is not None and str(value).strip():
            embed.add_field(
                name=label,
                value=str(value),
                inline=True,
            )

    notes = move.get("notes")
    if notes and str(notes).strip():
        embed.add_field(
            name="Notes",
            value=str(notes),
            inline=False,
        )

    if stale:
        embed.set_footer(
            text="TekkenDocs is unavailable. Showing previously cached data; it may be outdated."
        )
    else:
        embed.set_footer(text="Frame data source: TekkenDocs / RBNorway")

    return embed


class Tekken7Client(commands.Bot):
    async def setup_hook(self):
        await source.start()
        await self.tree.sync()

    async def close(self):
        await source.close()
        await super().close()


client = Tekken7Client(
    command_prefix="!",
    intents=intents,
)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}.")


@client.tree.command(
    name="fd",
    description="Look up Tekken 7 frame data for a character move.",
)
@app_commands.describe(
    character="Tekken 7 character",
    move="Move notation, e.g. df1, 1+2, or qcf2",
)
async def fd(
    interaction: discord.Interaction,
    character: str,
    move: str,
):
    await interaction.response.defer()

    try:
        roster = await source.get_roster()
        character_slug = normalize_character(character)

        # Resolve either the source slug or the displayed character name.
        if character_slug not in roster:
            matches = [
                slug for slug, name in roster.items()
                if normalize_character(name) == character_slug
            ]
            if not matches:
                await interaction.followup.send(
                    f"Character **{character}** was not found in the TekkenDocs Tekken 7 roster."
                )
                return
            character_slug = matches[0]

        character_data, stale = await source.get_character(character_slug)
        moves = character_data["framesNormal"]

        found = next(
            (
                item for item in moves
                if move_matches(move, item.get("command", ""))
            ),
            None,
        )

        if not found:
            await interaction.followup.send(
                f"Move **{move}** was not found for **{character_data['characterName']}**."
            )
            return

        found = dict(found)
        found["characterId"] = character_slug

        embed = make_embed(
            character_data["characterName"],
            found,
            stale=stale,
        )

        await interaction.followup.send(embed=embed)

    except CharacterNotFound as exc:
        await interaction.followup.send(str(exc))

    except SourceUnavailable as exc:
        await interaction.followup.send(
            f"TекkenDocs is currently unavailable: {exc}"
        )

    except TekkenDocsError as exc:
        await interaction.followup.send(
            f"Could not retrieve frame data: {exc}"
        )

    except Exception as exc:
        print(f"/fd error: {type(exc).__name__}: {exc}")
        await interaction.followup.send(
            "An unexpected error occurred while looking up frame data."
        )


@fd.autocomplete("character")
async def character_autocomplete(
    interaction: discord.Interaction,
    current: str,
):
    try:
        roster = await source.get_roster()
    except TekkenDocsError:
        return []

    current_normalized = normalize_character(current)

    choices = []
    for slug, name in roster.items():
        if (
            current_normalized in normalize_character(name)
            or current_normalized in slug
        ):
            choices.append(
                app_commands.Choice(name=name[:100], value=slug[:100])
            )

        if len(choices) >= 25:
            break

    return choices


client.run(TOKEN)
```
