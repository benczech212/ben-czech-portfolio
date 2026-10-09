"""Who may change shared camp state from Discord.

Discord's `default_permissions` only hides a command until a server admin overrides it in
Server Settings > Integrations, so the cogs also check here at run time.
"""
import discord

import config


def is_camp_lead(member: discord.abc.User) -> bool:
    """Server managers, or anyone holding the configured camp-lead role."""
    if not isinstance(member, discord.Member):
        return False          # DMs: no guild roles to check
    if member.guild_permissions.manage_guild:
        return True
    return any(r.name == config.LEAD_ROLE_NAME for r in member.roles)


def can_manage_announcements(member: discord.abc.User) -> bool:
    return isinstance(member, discord.Member) and (
        member.guild_permissions.manage_messages or is_camp_lead(member))
