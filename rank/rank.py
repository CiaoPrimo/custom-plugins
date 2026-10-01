"""
cogs/rank.py

Adds two commands to Modmail (https://github.com/modmail-dev/Modmail):

  .role [rank]
      Overrides the "rank" (the footer text Modmail normally derives from
      your highest Discord role) shown on your replies IN THE CURRENT
      TICKET ONLY. Run `.role` with no argument to clear it. The override
      lives in memory, keyed by (thread channel, your user id), so:
        - it never shows up in any other ticket
        - it's automatically forgotten when the ticket closes
        - it's automatically forgotten if the bot restarts
        - other staff replying in the same ticket are unaffected

  .sudo <user> <message>
      Sends a reply in the current ticket that looks exactly like <user>
      sent it (name, avatar, and their rank/role tag) instead of you.
      Anything <user> has set with .role in this ticket is respected,
      same as if they'd typed it themselves.

Install:
  1. Drop this file at cogs/rank.py
  2. In bot.py, add "cogs.rank" to the `self.loaded_cogs` list
     (next to "cogs.modmail", "cogs.plugins", etc.)
  3. Restart the bot.

This does not modify any core/ files. It works by patching two things at
import time, scoped to this module only:
  - core.thread.Thread.send   -> wrapped so it can flag an active override
  - core.thread.get_top_role  -> wrapped so it returns the override, if any,
                                  instead of computing the member's real
                                  top role

Both wrapped functions fall through to the original behavior whenever no
override is active, so nothing else about Modmail changes.
"""

import copy
import contextvars

import discord
from discord.ext import commands

from core import checks
from core.models import PermissionLevel, getLogger
from core.utils import safe_typing
import core.thread as thread_module

logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# Patch machinery (module-level so it's only ever installed once)
# ---------------------------------------------------------------------------

# channel_id -> {user_id: "override text"}
_role_overrides: dict = {}

# Set for the lifetime of a single Thread.send(...) call so the patched
# get_top_role() knows what to return, without touching unrelated calls
# happening concurrently (contextvars are per-Task, so parallel sends to
# multiple recipients don't leak into each other).
_active_override = contextvars.ContextVar("modmail_rank_override", default=None)

if not getattr(thread_module.Thread, "_rank_plugin_patched", False):
    _original_send = thread_module.Thread.send
    _original_get_top_role = thread_module.get_top_role

    async def _patched_send(
        self,
        message,
        destination=None,
        from_mod=False,
        note=False,
        anonymous=False,
        plain=False,
        persistent_note=False,
        thread_creation=False,
        *,
        content_override=None,
    ):
        override = None
        if from_mod and not note:
            try:
                channel_id = self.channel.id if self.channel else None
            except Exception:
                channel_id = None
            if channel_id is not None:
                override = _role_overrides.get(channel_id, {}).get(message.author.id)

        token = _active_override.set(override) if override is not None else None
        try:
            return await _original_send(
                self,
                message,
                destination,
                from_mod,
                note,
                anonymous,
                plain,
                persistent_note,
                thread_creation,
                content_override=content_override,
            )
        finally:
            if token is not None:
                _active_override.reset(token)

    def _patched_get_top_role(member, hoisted=True):
        override = _active_override.get()
        if override is not None:
            return override
        return _original_get_top_role(member, hoisted)

    thread_module.Thread.send = _patched_send
    thread_module.Thread.get_top_role = staticmethod(_patched_get_top_role)  # harmless if unused
    thread_module.get_top_role = _patched_get_top_role
    thread_module.Thread._rank_plugin_patched = True


class Rank(commands.Cog):
    """Per-ticket rank override and message impersonation."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_thread_close(self, thread, closer, silent, delete_channel, message, scheduled):
        # Forget any override the moment the ticket closes.
        try:
            _role_overrides.pop(thread.channel.id, None)
        except Exception:
            pass

    @commands.command(name="role")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    @checks.thread_only()
    async def role_(self, ctx, *, rank: str = None):
        """
        Override what your replies show as your rank, for this ticket only.

        Usage:
          .role Head Admin   -> your replies in this ticket now show "Head Admin"
          .role              -> clear the override, back to your real top role
        """
        channel_id = ctx.channel.id
        rank = rank.strip() if rank else None

        if not rank:
            _role_overrides.get(channel_id, {}).pop(ctx.author.id, None)
            embed = discord.Embed(
                color=self.bot.main_color,
                description="Rank override cleared for this ticket — back to your real top role.",
            )
            return await ctx.send(embed=embed)

        _role_overrides.setdefault(channel_id, {})[ctx.author.id] = rank
        embed = discord.Embed(
            color=self.bot.main_color,
            description=f"Your rank in this ticket is now shown as **{rank}**. "
            "This only applies here and won't carry over to other tickets.",
        )
        await ctx.send(embed=embed)

    @commands.command(name="sudo")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    @checks.thread_only()
    async def sudo(self, ctx, member: discord.Member, *, msg: str):
        """
        Send a reply in this ticket as if <member> sent it.

        Usage:
          .sudo @SomeMod Thanks for waiting, we'll get this sorted.
          .sudo SomeMod   Thanks for waiting, we'll get this sorted.
        """
        fake_message = copy.copy(ctx.message)
        fake_message.author = member
        fake_message.content = msg

        async with safe_typing(ctx):
            await ctx.thread.reply(fake_message, msg)


async def setup(bot):
    await bot.add_cog(Rank(bot))
