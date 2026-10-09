import uuid
from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

import access
import config
import data_store

STATUS_COLORS = {
    "planning": discord.Color.yellow(),
    "in-progress": discord.Color.blue(),
    "complete": discord.Color.green(),
    "on-hold": discord.Color.red(),
}

STATUS_EMOJI = {
    "planning": "📐",
    "in-progress": "🔧",
    "complete": "✅",
    "on-hold": "⏸️",
}


def _on_team(build: dict, user: discord.abc.User) -> bool:
    uid = str(user.id)
    return uid == build.get("created_by") or uid in build.get("assignees", [])


class Builds(commands.Cog):
    """Anyone may add and list projects. Updating a project's status is for its team or a camp
    lead; adding people to a project is for its creator or a camp lead (bot/access.py)."""

    def __init__(self, bot):
        self.bot = bot

    build_group = app_commands.Group(name="build", description="Manage camp build projects", guild_only=True)

    # ── /build add ────────────────────────────────────────────────────────────

    @build_group.command(name="add", description="Add a new build project")
    @app_commands.describe(
        name="Short project name",
        description="What are you building?",
    )
    async def build_add(self, interaction: discord.Interaction, name: str, description: str):
        existing = data_store.get_build_by_name(name)
        if existing:
            await interaction.response.send_message(
                f"A project named **{name}** already exists. Use `/build update` to change it.",
                ephemeral=True,
            )
            return

        build = {
            "id": str(uuid.uuid4()),
            "name": name,
            "description": description,
            "status": "planning",
            "assignees": [str(interaction.user.id)],
            "created_by": str(interaction.user.id),
            "created_at": str(date.today()),
        }
        data_store.upsert_build(build)

        embed = discord.Embed(
            title=f"Build project created: {name}",
            description=description,
            color=STATUS_COLORS["planning"],
        )
        embed.add_field(name="Status", value=f"{STATUS_EMOJI['planning']} planning", inline=True)
        embed.add_field(name="Lead", value=interaction.user.mention, inline=True)
        embed.set_footer(text=f"ID: {build['id'][:8]}")
        await interaction.response.send_message(embed=embed)

    # ── /build list ───────────────────────────────────────────────────────────

    @build_group.command(name="list", description="List all camp build projects")
    async def build_list(self, interaction: discord.Interaction):
        builds = data_store.get_builds()
        if not builds:
            await interaction.response.send_message(
                "No build projects yet. Start one with `/build add`!", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"{config.CAMP_NAME} — Build Projects",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{len(builds)} project(s)")

        for b in builds:
            status = b.get("status", "planning")
            emoji = STATUS_EMOJI.get(status, "📋")
            assignee_mentions = []
            for aid in b.get("assignees", []):
                member = interaction.guild.get_member(int(aid))
                assignee_mentions.append(member.display_name if member else f"<@{aid}>")
            assignees_str = ", ".join(assignee_mentions) if assignee_mentions else "Unassigned"
            embed.add_field(
                name=f"{emoji} {b['name']}",
                value=f"{b['description'][:80]}{'...' if len(b['description']) > 80 else ''}\n"
                      f"**Status:** {status} | **Team:** {assignees_str}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    # ── /build update ─────────────────────────────────────────────────────────

    @build_group.command(name="update", description="Update a build project's status")
    @app_commands.describe(
        name="Project name",
        status="New status",
    )
    @app_commands.choices(
        status=[app_commands.Choice(name=s, value=s) for s in config.BUILD_STATUSES]
    )
    async def build_update(self, interaction: discord.Interaction, name: str, status: str):
        build = data_store.get_build_by_name(name)
        if not build:
            await interaction.response.send_message(
                f"No project named **{name}** found. Check `/build list`.", ephemeral=True
            )
            return
        if not (_on_team(build, interaction.user) or access.is_camp_lead(interaction.user)):
            await interaction.response.send_message(
                f"Only the **{build['name']}** team or a camp lead ({config.LEAD_ROLE_NAME}) can change its status.",
                ephemeral=True,
            )
            return

        build["status"] = status
        data_store.upsert_build(build)

        emoji = STATUS_EMOJI.get(status, "📋")
        embed = discord.Embed(
            title=f"Build updated: {name}",
            description=f"Status changed to **{emoji} {status}**",
            color=STATUS_COLORS.get(status, discord.Color.greyple()),
        )
        await interaction.response.send_message(embed=embed)

    # ── /build assign ─────────────────────────────────────────────────────────

    @build_group.command(name="assign", description="Add a member to a build project")
    @app_commands.describe(
        name="Project name",
        member="Discord member to assign",
    )
    async def build_assign(self, interaction: discord.Interaction, name: str, member: discord.Member):
        build = data_store.get_build_by_name(name)
        if not build:
            await interaction.response.send_message(
                f"No project named **{name}** found. Check `/build list`.", ephemeral=True
            )
            return
        if not (str(interaction.user.id) == build.get("created_by") or access.is_camp_lead(interaction.user)):
            await interaction.response.send_message(
                f"Only whoever created **{build['name']}** or a camp lead ({config.LEAD_ROLE_NAME}) can add people to it.",
                ephemeral=True,
            )
            return

        assignees = build.setdefault("assignees", [])
        if str(member.id) in assignees:
            await interaction.response.send_message(
                f"{member.mention} is already on **{name}**.", ephemeral=True
            )
            return

        assignees.append(str(member.id))
        data_store.upsert_build(build)

        embed = discord.Embed(
            title=f"Assigned to {name}",
            description=f"{member.mention} has been added to **{name}**.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Builds(bot))
