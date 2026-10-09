import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import data_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lunarcrats")

COGS = [
    "cogs.members",
    "cogs.builds",
    "cogs.announcements",
    "cogs.ai_assistant",
]


class LunarcratsBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    async def setup_hook(self):
        for cog in COGS:
            await self.load_extension(cog)
            log.info(f"Loaded cog: {cog}")

        guild = discord.Object(id=config.GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced to guild")

        self.scheduler.start()
        self.scheduler.add_job(
            self._check_announcements,
            "interval",
            minutes=1,
            id="announcement_checker",
            next_run_time=datetime.now(timezone.utc),
        )
        log.info("Announcement scheduler started")

    async def on_ready(self):
        log.info(f"Logged in as {self.user} ({self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{config.EVENT_NAME} | /ask for help",
            )
        )

    async def _check_announcements(self):
        now = datetime.now(timezone.utc)
        announcements = data_store.get_announcements()
        changed = False
        for ann in announcements:
            if ann.get("sent"):
                continue
            scheduled = datetime.fromisoformat(ann["scheduled_for"])
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if now >= scheduled:
                channel = self.get_channel(int(ann["channel_id"]))
                if channel:
                    embed = discord.Embed(
                        description=ann["message"],
                        color=discord.Color.gold(),
                        timestamp=now,
                    )
                    embed.set_author(name=f"{config.CAMP_NAME} Announcement")
                    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                    log.info(f"Sent announcement {ann['id']}")
                ann["sent"] = True
                changed = True
        if changed:
            data_store.save_announcements(announcements)


async def main():
    bot = LunarcratsBot()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
