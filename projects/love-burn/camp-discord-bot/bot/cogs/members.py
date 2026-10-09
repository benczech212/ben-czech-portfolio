from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

import config
import data_store


class Members(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── /signup ──────────────────────────────────────────────────────────────

    @app_commands.command(name="signup", description=f"Register yourself as a {config.CAMP_NAME} camp member")
    @app_commands.describe(roles="Your role(s), comma-separated (e.g. DJ, Builder, Cook)")
    async def signup(self, interaction: discord.Interaction, roles: str = "Member"):
        discord_id = str(interaction.user.id)
        role_list = [r.strip() for r in roles.split(",") if r.strip()]

        existing = data_store.get_member(discord_id)
        member = {
            "discord_id": discord_id,
            "discord_name": str(interaction.user),
            "display_name": interaction.user.display_name,
            "roles": role_list,
            "joined_at": str(date.today()),
            "notes": existing.get("notes", "") if existing else "",
        }
        data_store.upsert_member(member)

        verb = "Updated" if existing else "Registered"
        embed = discord.Embed(
            title=f"{verb}!",
            description=f"Welcome to **{config.CAMP_NAME}**, {interaction.user.mention}!",
            color=discord.Color.green(),
        )
        embed.add_field(name="Roles", value=", ".join(role_list), inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    # ── /roster ───────────────────────────────────────────────────────────────

    @app_commands.command(name="roster", description="Show the full camp member roster")
    async def roster(self, interaction: discord.Interaction):
        members = data_store.get_members()
        if not members:
            await interaction.response.send_message(
                "No members signed up yet. Be the first with `/signup`!", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"{config.CAMP_NAME} — {config.EVENT_NAME} Roster",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{len(members)} member(s) total")

        for m in members:
            roles_str = ", ".join(m.get("roles", [])) or "No roles"
            embed.add_field(
                name=m["display_name"],
                value=f"Roles: {roles_str}\nJoined: {m.get('joined_at', '?')}",
                inline=True,
            )

        await interaction.response.send_message(embed=embed)

    # ── /whois ────────────────────────────────────────────────────────────────

    @app_commands.command(name="whois", description="Look up a camp member's profile")
    @app_commands.describe(member="The Discord user to look up")
    async def whois(self, interaction: discord.Interaction, member: discord.Member):
        record = data_store.get_member(str(member.id))
        if not record:
            await interaction.response.send_message(
                f"{member.mention} hasn't signed up yet. Tell them to use `/signup`!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=record["display_name"],
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Roles", value=", ".join(record.get("roles", [])) or "None", inline=False)
        embed.add_field(name="Joined", value=record.get("joined_at", "?"), inline=True)
        if record.get("notes"):
            embed.add_field(name="Notes", value=record["notes"], inline=False)

        await interaction.response.send_message(embed=embed)

    # ── /myprofile ────────────────────────────────────────────────────────────

    @app_commands.command(name="myprofile", description="View or update your own camp profile")
    @app_commands.describe(
        roles="Update your roles (comma-separated). Leave blank to just view.",
        notes="Add a personal note (e.g. skills, what you're bringing)",
    )
    async def myprofile(
        self,
        interaction: discord.Interaction,
        roles: str | None = None,
        notes: str | None = None,
    ):
        discord_id = str(interaction.user.id)
        record = data_store.get_member(discord_id)

        if record is None and roles is None and notes is None:
            await interaction.response.send_message(
                "You're not signed up yet. Use `/signup` first!", ephemeral=True
            )
            return

        if record is None:
            record = {
                "discord_id": discord_id,
                "discord_name": str(interaction.user),
                "display_name": interaction.user.display_name,
                "roles": [],
                "joined_at": str(date.today()),
                "notes": "",
            }

        if roles is not None:
            record["roles"] = [r.strip() for r in roles.split(",") if r.strip()]
        if notes is not None:
            record["notes"] = notes

        if roles is not None or notes is not None:
            data_store.upsert_member(record)

        embed = discord.Embed(
            title=f"Your {config.CAMP_NAME} Profile",
            color=discord.Color.og_blurple(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Name", value=record["display_name"], inline=True)
        embed.add_field(name="Roles", value=", ".join(record.get("roles", [])) or "None", inline=True)
        embed.add_field(name="Joined", value=record.get("joined_at", "?"), inline=True)
        if record.get("notes"):
            embed.add_field(name="Notes", value=record["notes"], inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Members(bot))
