
import os
import re
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands

from tekkendocs import (
    source,
    normalize_move,
    TekkenDocsError,
    SourceUnavailable,
    CharacterNotFound,
)


TOKEN = os.environ["DISCORD_TOKEN"]


def character_slug(value):
    value = value.strip().lower()
    value = value.replace("&", "and")
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    return value.strip("-")


def make_move_url(slug, command):
    # TekkenDocs documents command pages using /character/command.
    # Convert common notation separators to the compact URL notation.
    compact = command.strip().lower()
    compact = compact.replace(" ", "")
    compact = compact.replace(",", "")
    compact = compact.replace("/", "")
    compact = compact.replace("+", "")
    compact = compact.replace("*", "")
    compact = re.sub(r"[^a-z0-9]", "", compact)

    if compact:
        return f"https://tekkendocs.com/{slug}/{quote(compact)}"

    return f"https://tekkendocs.com/{slug}"


def make_embed(character_name, slug, move, stale=False):
    command = move.get("command", "Unknown move")

    embed = discord.Embed(
        title=f"{character_name} — {command}",
        url=make_move_url(slug, command),
        color=discord.Color.blurple(),
    )

    fields = [
        ("Hit level", "hitLevel"),
        ("Damage", "damage"),
        ("Startup", "startup"),
        ("Block", "block"),
        ("Hit", "hit"),
        ("Counter hit", "counterHit"),
    ]

    for label, key in fields:
        value = move.get(key, "")

        if value is not None and str(value).strip():
            embed.add_field(
                name=label,
                value=str(value),
                inline=True,
            )

    notes = move.get("notes", "")

    if notes and str(notes).strip():
        embed.add_field(
            name="Notes",
            value=str(notes),
            inline=False,
        )

    if stale:
        embed.set_footer(
            text=(
                "TekkenDocs is unavailable. Showing cached data "
                "which may be outdated."
            )
        )
    else:
        embed.set_footer(
            text="Frame data: TekkenDocs / RBNorway"
        )

    return embed


class Tekken7Bot(commands.Bot):
    async def setup_hook(self):
        await source.start()
        await self.tree.sync()

    async def close(self):
        await source.close()
        await super().close()


intents = discord.Intents.default()

bot = Tekken7Bot(
    command_prefix="!",
    intents=intents,
)


@bot.event
async def on_ready():
    print(f"Tekken 7 bot logged in as {bot.user}")


@bot.tree.command(
    name="fd",
    description="Look up Tekken 7 frame data.",
)
@app_commands.describe(
    character="Choose a Tekken 7 character.",
    move="Enter a move, for example df1, 1+2, or qcf2.",
)
async def fd(
    interaction: discord.Interaction,
    character: str,
    move: str,
):
    await interaction.response.defer()

    try:
        roster = await source.get_roster()

        requested_slug = character_slug(character)

        # Autocomplete supplies the slug. Also accept a displayed character name.
        if requested_slug not in roster:
            matches = [
                slug
                for slug, name in roster.items()
                if character_slug(name) == requested_slug
            ]

            if not matches:
                await interaction.followup.send(
                    f"Character **{character}** was not found "
                    "in the TekkenDocs Tekken 7 roster."
                )
                return

            requested_slug = matches[0]

        data, stale = await source.get_character(requested_slug)

        normalized_query = normalize_move(move)

        found = next(
            (
                item
                for item in data["framesNormal"]
                if normalize_move(item.get("command", "")) == normalized_query
            ),
            None,
        )

        if found is None:
            await interaction.followup.send(
                f"Move **{move}** was not found for "
                f"**{data['characterName']}**."
            )
            return

        embed = make_embed(
            data["characterName"],
            requested_slug,
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
        print(
            f"/fd error: {type(exc).__name__}: {exc}"
        )
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

    query = character_slug(current)

    choices = []

    for slug, name in roster.items():
        if (
            query in character_slug(name)
            or query in slug
        ):
            choices.append(
                app_commands.Choice(
                    name=name[:100],
                    value=slug[:100],
                )
            )

        if len(choices) >= 25:
            break

    return choices


bot.run(TOKEN)
