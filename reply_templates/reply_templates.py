import re

import discord
from discord.ext import commands

from core import checks
from core.models import PermissionLevel


PATCHED_CMDS = ("reply", "freply", "fareply", "areply", "preply")

# {tag}text{tag} or {tag}text{tag2} or {tag}text{/tag}
TAG_RE = re.compile(r"\{(?P<name>[a-zA-Z0-9_]+)\}(?P<body>.*?)\{/?(?P=name)2?\}", re.DOTALL)

NAME_TAGS = {
    "!USERNAME!": lambda a: a.name,
    "!DISPLAYNAME!": lambda a: a.display_name,
    "!MENTION!": lambda a: a.mention,
    "!TAG!": lambda a: str(a),
    "!ID!": lambda a: str(a.id),
}
NAME_TAG_RE = re.compile("|".join(re.escape(k) for k in NAME_TAGS), re.IGNORECASE)


class ReplyTemplates(commands.Cog, name="Reply Templates"):
    def __init__(self, bot):
        self.bot = bot
        self._old_callbacks = {}

    async def cog_load(self):
        await self.bot.wait_until_ready()

        for name in PATCHED_CMDS:
            cmd = self.bot.get_command(name)
            if cmd is None or name in self._old_callbacks:
                continue
            self._old_callbacks[name] = cmd.callback
            cmd.callback = self._patch(cmd.callback)

    def cog_unload(self):
        for name, cb in self._old_callbacks.items():
            cmd = self.bot.get_command(name)
            if cmd is not None:
                cmd.callback = cb
        self._old_callbacks = {}

    def _patch(self, old_callback):
        async def new_callback(cog, ctx, *, msg=""):
            msg = self.fill_tags(msg)
            msg = self.fill_names(msg, ctx.author)
            return await old_callback(cog, ctx, msg=msg)

        return new_callback

    def get_snippet(self, name):
        # go through modmail's own snippet aliasing if it exists, otherwise
        # just look it up directly in bot.snippets
        resolve = getattr(self.bot, "_resolve_snippet", None)
        if resolve is not None:
            resolved = resolve(name)
            if resolved is None:
                return None
            return self.bot.snippets.get(resolved)
        return self.bot.snippets.get(name)

    def fill_tags(self, text):
        def sub(m):
            content = self.get_snippet(m.group("name").lower())
            if content is None:
                return m.group(0)
            if "{input}" in content:
                return content.replace("{input}", m.group("body"))
            # no placeholder in the snippet, just tack the wrapped text on
            return f"{content}{m.group('body')}"

        return TAG_RE.sub(sub, text)

    def fill_names(self, text, author):
        return NAME_TAG_RE.sub(lambda m: NAME_TAGS[m.group(0).upper()](author), text)

    @commands.command(name="tagpreview")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def tagpreview(self, ctx, name: str.lower, *, sample: str = "Example"):
        """Preview what {name}text{name2} would render as, using your existing snippet."""
        content = self.get_snippet(name)
        if content is None:
            return await ctx.send(
                embed=discord.Embed(color=self.bot.error_color, description=f"No snippet `{name}`.")
            )
        rendered = content.replace("{input}", sample) if "{input}" in content else f"{content}{sample}"
        rendered = self.fill_names(rendered, ctx.author)
        await ctx.send(
            embed=discord.Embed(title=f"preview: {name}", color=self.bot.main_color, description=rendered)
        )

    @commands.command(name="placeholders", aliases=["placeholder"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def placeholders_cmd(self, ctx):
        await ctx.send(
            embed=discord.Embed(
                title="Reply placeholders",
                color=self.bot.main_color,
                description=(
                    "Usable in reply/areply/freply/fareply/preply:\n\n"
                    "`!USERNAME!`, `!DISPLAYNAME!`, `!MENTION!`, `!TAG!`, `!ID!`\n\n"
                    "Snippet tags: `{name}text{name2}` pulls from an existing "
                    "`?snippet` of that name. If the snippet's text contains "
                    "`{input}`, your wrapped text is dropped in there - "
                    "otherwise it's just appended to the end of the snippet."
                ),
            )
        )


async def setup(bot):
    await bot.add_cog(ReplyTemplates(bot))
