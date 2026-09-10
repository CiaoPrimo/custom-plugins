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

DEFAULT_GREETING = (
    "Hello there, thank you for contacting Delivr Support.\n\n"
    "> I am **{input}**, and I am a support member who will be assisting "
    "you throughout the process.\n"
    "> \n"
    "> Is there anything I can help you with?"
)


class ReplyTemplates(commands.Cog, name="Reply Templates"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.plugin_db.get_partition(self)
        self.templates = {}
        self._old_callbacks = {}

    async def cog_load(self):
        await self.bot.wait_until_ready()

        async for doc in self.db.find():
            self.templates[doc["_id"]] = doc["content"]

        if "greeting" not in self.templates:
            await self.db.find_one_and_update(
                {"_id": "greeting"}, {"$set": {"content": DEFAULT_GREETING}}, upsert=True
            )
            self.templates["greeting"] = DEFAULT_GREETING

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

    def fill_tags(self, text):
        def sub(m):
            tpl = self.templates.get(m.group("name").lower())
            if tpl is None:
                return m.group(0)
            return tpl.replace("{input}", m.group("body"))

        return TAG_RE.sub(sub, text)

    def fill_names(self, text, author):
        return NAME_TAG_RE.sub(lambda m: NAME_TAGS[m.group(0).upper()](author), text)

    @commands.group(name="template", aliases=["templates", "tmpl"], invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def template_(self, ctx):
        await ctx.send_help(ctx.command)

    @template_.command(name="add", aliases=["create", "set", "edit"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def template_add(self, ctx, name: str.lower, *, content: str):
        await self.db.find_one_and_update(
            {"_id": name}, {"$set": {"content": content}}, upsert=True
        )
        self.templates[name] = content
        await ctx.send(
            embed=discord.Embed(
                color=self.bot.main_color,
                description=f"Saved `{name}`. Use it as `{{{name}}}text{{{name}2}}`",
            )
        )

    @template_.command(name="remove", aliases=["delete", "del"])
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def template_remove(self, ctx, name: str.lower):
        if name not in self.templates:
            return await ctx.send(
                embed=discord.Embed(color=self.bot.error_color, description=f"No template `{name}`.")
            )
        await self.db.delete_one({"_id": name})
        del self.templates[name]
        await ctx.send(embed=discord.Embed(color=self.bot.main_color, description=f"Deleted `{name}`."))

    @template_.command(name="list")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def template_list(self, ctx):
        if not self.templates:
            return await ctx.send(
                embed=discord.Embed(color=self.bot.error_color, description="No templates yet.")
            )
        desc = "\n".join(f"`{n}`" for n in sorted(self.templates))
        await ctx.send(embed=discord.Embed(title="Templates", color=self.bot.main_color, description=desc))

    @template_.command(name="show")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def template_show(self, ctx, name: str.lower):
        content = self.templates.get(name)
        if content is None:
            return await ctx.send(
                embed=discord.Embed(color=self.bot.error_color, description=f"No template `{name}`.")
            )
        await ctx.send(embed=discord.Embed(title=name, color=self.bot.main_color, description=content))

    @template_.command(name="preview")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def template_preview(self, ctx, name: str.lower, *, sample: str = "Example"):
        content = self.templates.get(name)
        if content is None:
            return await ctx.send(
                embed=discord.Embed(color=self.bot.error_color, description=f"No template `{name}`.")
            )
        rendered = self.fill_names(content.replace("{input}", sample), ctx.author)
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
                    "Templates: `?template` to manage `{tag}...{tag2}` snippets."
                ),
            )
        )


async def setup(bot):
    await bot.add_cog(ReplyTemplates(bot))
