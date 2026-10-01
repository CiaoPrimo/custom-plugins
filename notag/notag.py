"""
plugins/@local/notag/notag.py

Removes the legacy discriminator tag (the "#2363" part) from bot accounts'
display names, everywhere str(some_bot_user) is used across the whole
process — thread reply author names, .sudo impersonation, logs, anywhere.

Background: Discord retired discriminators for regular accounts (they show
as just "username" now), but bot accounts still default to the old
"Name#1234" format. discord.py's User.__str__ (which Member.__str__ just
delegates to) is what appends that suffix:

    def __str__(self):
        if self.discriminator == "0":
            return self.name
        return f"{self.name}#{self.discriminator}"

This patches that one method so it also drops the suffix for bot accounts.
Human accounts are untouched — if a human still has an old-style
discriminator, it keeps showing (there was never a "tag" complaint about
those, and stripping it for humans is a different, unrelated ask).

Install as a local plugin (no core file edits, no bot.py edits):
  1. Unzip so you have plugins/@local/notag/notag.py
  2. Make sure ENABLE_PLUGINS=true is set
  3. In Discord: `.plugins add local/notag`
"""

import discord
from discord.ext import commands

from core.models import getLogger

logger = getLogger(__name__)

if not getattr(discord.user.BaseUser, "_notag_plugin_patched", False):
    _original_str = discord.user.BaseUser.__str__

    def _patched_str(self):
        if getattr(self, "bot", False) and self.discriminator != "0":
            return self.name
        return _original_str(self)

    discord.user.BaseUser.__str__ = _patched_str
    discord.user.BaseUser._notag_plugin_patched = True


class NoTag(commands.Cog):
    """No commands — just installs the __str__ patch on load."""

    def __init__(self, bot):
        self.bot = bot
        logger.info("notag: bot accounts will no longer show their #1234 tag.")


async def setup(bot):
    await bot.add_cog(NoTag(bot))
