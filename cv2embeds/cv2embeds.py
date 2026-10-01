"""
plugins/@local/cv2embeds/cv2embeds.py

Converts every embed the bot sends or edits — anywhere: core Modmail,
other cogs, other plugins — into a Discord Components V2 (CV2) message,
instead of touching all ~270 call sites across the codebase by hand.

It works by patching two discord.py entry points at import time:
  - discord.abc.Messageable.send
  - discord.Message.edit

Whenever a call includes `embed=`/`embeds=` and does NOT already include a
custom `view=`, the embed(s) are converted into a `discord.ui.LayoutView`
(Container + TextDisplay/Section/MediaGallery/Separator) and sent that way
instead. Calls that don't use embeds at all pass straight through untouched.

What gets converted automatically (the vast majority of the bot):
  title (+ url), description, author (+ icon, + url), fields (stacked —
  Components V2 has no native inline-column grid like classic embeds),
  thumbnail, image, color (-> container accent color), footer text, and
  timestamp (rendered as a Discord dynamic timestamp in a small subtext
  line, Discord-markdown style: "-# footer • <t:...:f>").

What is deliberately left as a classic embed (NOT converted):
  Any send/edit that already passes its own `view=` — i.e. embeds shown
  alongside interactive buttons/selects. There are 5 of these in stock
  Modmail (core/paginator.py's log paginator, core/thread.py's thread-
  creation menu and precreate menu, and the log-channel duplicate-alert
  message). Their buttons rely on `self.view` pointing at the *exact*
  View instance the code built (for state like `.value`, `.stop()`,
  custom `interaction_check`/timeout handling). Automatically rehousing
  those buttons into a new LayoutView would silently detach that logic,
  which is a functional/security regression, not just a visual one — so
  those 5 spots keep their original classic embed + view rendering. They
  can be hand-converted individually on request.

Install as a local plugin (no core file edits, no bot.py edits):
  1. Unzip so you have plugins/@local/cv2embeds/cv2embeds.py
  2. Make sure ENABLE_PLUGINS=true is set
  3. In Discord: `.plugins add local/cv2embeds`
"""

import datetime

import discord
from discord.ext import commands
from discord.utils import MISSING

from core.models import getLogger

logger = getLogger(__name__)


# ---------------------------------------------------------------------------
# Embed(s) -> Components V2 LayoutView
# ---------------------------------------------------------------------------

def _embed_to_container(embed: discord.Embed) -> discord.ui.Container:
    color = embed.colour.value if embed.colour else None
    body = []

    author_icon = embed.author.icon_url if embed.author else None

    header_lines = []
    if embed.author and embed.author.name:
        # Plain bold text — no hyperlink, even if author.url is set
        header_lines.append(f"**{embed.author.name}**")

    if embed.title:
        header_lines.append(f"### [{embed.title}]({embed.url})" if embed.url else f"### {embed.title}")
    if embed.description:
        header_lines.append(embed.description)

    thumbnail_url = None
    if embed.thumbnail and embed.thumbnail.url:
        thumbnail_url = embed.thumbnail.url
    elif author_icon:
        thumbnail_url = author_icon

    if header_lines:
        text = "\n".join(header_lines)
        if thumbnail_url:
            body.append(
                discord.ui.Section(
                    discord.ui.TextDisplay(text),
                    accessory=discord.ui.Thumbnail(thumbnail_url),
                )
            )
        else:
            body.append(discord.ui.TextDisplay(text))
    elif thumbnail_url:
        body.append(
            discord.ui.Section(
                discord.ui.TextDisplay("\u200b"),
                accessory=discord.ui.Thumbnail(thumbnail_url),
            )
        )

    for field in embed.fields:
        name = field.name or "\u200b"
        value = field.value or "\u200b"
        body.append(discord.ui.TextDisplay(f"**{name}**\n{value}"))

    if embed.image and embed.image.url:
        body.append(discord.ui.MediaGallery(discord.MediaGalleryItem(embed.image.url)))

    footer_parts = []
    if embed.footer and embed.footer.text:
        footer_parts.append(embed.footer.text)
    if embed.timestamp:
        ts = embed.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        footer_parts.append(f"<t:{int(ts.timestamp())}:f>")
    if footer_parts:
        if body:
            body.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        body.append(discord.ui.TextDisplay("-# " + " • ".join(footer_parts)))

    if not body:
        body.append(discord.ui.TextDisplay("\u200b"))

    return discord.ui.Container(*body, accent_color=color)


def _build_cv2_view(embeds, leading_content=None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    if leading_content:
        view.add_item(discord.ui.TextDisplay(leading_content))
    for i, embed in enumerate(embeds):
        view.add_item(_embed_to_container(embed))
        if i != len(embeds) - 1:
            view.add_item(discord.ui.Separator())
    return view


# ---------------------------------------------------------------------------
# Patch discord.abc.Messageable.send and discord.Message.edit
# ---------------------------------------------------------------------------

if not getattr(discord.abc.Messageable, "_cv2_plugin_patched", False):
    _original_send = discord.abc.Messageable.send

    async def _cv2_send(self, content=None, **kwargs):
        embed = kwargs.pop("embed", None)
        embeds = kwargs.pop("embeds", None)
        all_embeds = list(embeds) if embeds else ([embed] if embed else [])

        if all_embeds and kwargs.get("view") is None:
            kwargs["view"] = _build_cv2_view(all_embeds, leading_content=content)
            content = None
        elif all_embeds:
            # A real interactive view is already attached — leave it classic.
            if embed is not None:
                kwargs["embed"] = embed
            else:
                kwargs["embeds"] = embeds

        return await _original_send(self, content, **kwargs)

    discord.abc.Messageable.send = _cv2_send
    discord.abc.Messageable._cv2_plugin_patched = True

if not getattr(discord.Message, "_cv2_plugin_patched", False):
    _original_edit = discord.Message.edit

    async def _cv2_edit(self, **kwargs):
        has_embed = "embed" in kwargs or "embeds" in kwargs
        has_view = "view" in kwargs and kwargs["view"] is not None

        if has_embed and not has_view:
            embed = kwargs.pop("embed", None)
            embeds = kwargs.pop("embeds", None)
            all_embeds = list(embeds) if embeds else ([embed] if embed else [])
            leading = kwargs.get("content") if kwargs.get("content") not in (None, MISSING) else None

            kwargs["view"] = _build_cv2_view(all_embeds, leading_content=leading)
            kwargs["embeds"] = []  # explicitly drop any previously-attached classic embeds
            if "content" in kwargs:
                kwargs["content"] = None

        return await _original_edit(self, **kwargs)

    discord.Message.edit = _cv2_edit
    discord.Message._cv2_plugin_patched = True


class CV2Embeds(commands.Cog):
    """No commands — just installs the send/edit patches on load."""

    def __init__(self, bot):
        self.bot = bot
        logger.info("cv2embeds: all embeds bot-wide will now render as Components V2.")


async def setup(bot):
    await bot.add_cog(CV2Embeds(bot))
