import uuid
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dateutil import parser as dateutil_parser

import access
import config
import data_store


class Announcements(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Hidden from members without Manage Messages by default (server admins can grant it to a
    # role in Server Settings > Integrations); access.can_manage_announcements re-checks at run time.
    announce_group = app_commands.Group(
        name="announce",
        description="Schedule camp announcements",
        guild_only=True,
        default_permissions=discord.Permissions(manage_messages=True),
    )

    async def _deny(self, interaction: discord.Interaction) -> bool:
        if access.can_manage_announcements(interaction.user):
            return False
        await interaction.response.send_message(
            f"Only camp leads ({config.LEAD_ROLE_NAME}) or moderators can manage announcements.", ephemeral=True)
        return True

    # ── /announce add ─────────────────────────────────────────────────────────

    @announce_group.command(name="add", description="Schedule a camp announcement")
    @app_commands.describe(
        message="The announcement text",
        when="When to send it (e.g. '2027-01-15 10:00', 'tomorrow at noon', 'in 2 hours')",
        channel="Channel to post in (defaults to the configured announcement channel)",
    )
    async def announce_add(
        self,
        interaction: discord.Interaction,
        message: str,
        when: str,
        channel: discord.TextChannel | None = None,
    ):
        if await self._deny(interaction):
            return
        if channel is not None and not channel.permissions_for(interaction.user).send_messages:
            await interaction.response.send_message(
                f"You can't post in {channel.mention} yourself, so I won't schedule it there.", ephemeral=True)
            return
        try:
            scheduled_dt = dateutil_parser.parse(when, fuzzy=True)
            if scheduled_dt.tzinfo is None:
                scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError):
            await interaction.response.send_message(
                f"Couldn't parse the time `{when}`. Try something like `2027-01-15 10:00` or `in 3 hours`.",
                ephemeral=True,
            )
            return

        if scheduled_dt <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "That time is already in the past!", ephemeral=True
            )
            return

        channel_id = str(channel.id) if channel else str(config.ANNOUNCE_CHANNEL_ID)

        ann = {
            "id": str(uuid.uuid4()),
            "message": message,
            "channel_id": channel_id,
            "scheduled_for": scheduled_dt.isoformat(),
            "sent": False,
            "created_by": str(interaction.user.id),
        }
        data_store.upsert_announcement(ann)

        embed = discord.Embed(
            title="Announcement Scheduled",
            description=message,
            color=discord.Color.gold(),
        )
        embed.add_field(name="Sends at", value=f"<t:{int(scheduled_dt.timestamp())}:F>", inline=True)
        embed.add_field(name="Channel", value=f"<#{channel_id}>", inline=True)
        embed.set_footer(text=f"ID: {ann['id'][:8]}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /announce list ────────────────────────────────────────────────────────

    @announce_group.command(name="list", description="List upcoming scheduled announcements")
    async def announce_list(self, interaction: discord.Interaction):
        if await self._deny(interaction):
            return
        all_anns = data_store.get_announcements()
        pending = [a for a in all_anns if not a.get("sent")]

        if not pending:
            await interaction.response.send_message(
                "No upcoming announcements scheduled.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"{config.CAMP_NAME} — Scheduled Announcements",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"{len(pending)} pending")

        for ann in sorted(pending, key=lambda a: a["scheduled_for"]):
            dt = datetime.fromisoformat(ann["scheduled_for"])
            ts = int(dt.timestamp())
            embed.add_field(
                name=f"ID: {ann['id'][:8]}",
                value=f"<t:{ts}:F>\n{ann['message'][:100]}{'...' if len(ann['message']) > 100 else ''}\nChannel: <#{ann['channel_id']}>",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /announce cancel ──────────────────────────────────────────────────────

    @announce_group.command(name="cancel", description="Cancel a scheduled announcement")
    @app_commands.describe(announcement_id="The first 8 characters of the announcement ID")
    async def announce_cancel(self, interaction: discord.Interaction, announcement_id: str):
        if await self._deny(interaction):
            return
        if len(announcement_id) < 4:
            await interaction.response.send_message("Give at least the first 4 characters of the ID.", ephemeral=True)
            return
        all_anns = data_store.get_announcements()
        match = next(
            (a for a in all_anns if a["id"].startswith(announcement_id) and not a.get("sent")),
            None,
        )
        if not match:
            await interaction.response.send_message(
                f"No pending announcement found with ID starting with `{announcement_id}`.",
                ephemeral=True,
            )
            return

        data_store.delete_announcement(match["id"])
        await interaction.response.send_message(
            f"Announcement `{announcement_id}` cancelled.", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Announcements(bot))
