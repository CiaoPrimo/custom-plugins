import asyncio

import discord
from discord.ext import commands

from core import checks
from core.models import PermissionLevel


RATING_LABELS = {
    1: "Poor",
    2: "Not great",
    3: "Good",
    4: "Very good",
    5: "Excellent",
}
SURVEY_TIMEOUT = 300  # 5 minutes


class FeedbackModal(discord.ui.Modal, title="What went wrong?"):
    reason = discord.ui.TextInput(
        label="What could we have done better?",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        self.view.result["reason"] = str(self.reason)

        for item in self.view.children:
            item.disabled = True

        await interaction.response.send_message("Thanks - we'll take a look at this.", ephemeral=True)

        if self.view.message is not None:
            try:
                await self.view.message.edit(
                    embed=discord.Embed(
                        description="Thanks for letting us know, we'll use this to improve.",
                        color=discord.Color.red(),
                    ),
                    view=self.view,
                )
            except discord.HTTPException:
                pass


class RatingSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=f"{n} - {RATING_LABELS[n]}",
                description="⭐" * n,
                value=str(n),
            )
            for n in range(1, 6)
        ]
        super().__init__(placeholder="Select a rating...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "SurveyView" = self.view
        rating = int(self.values[0])
        view.result["rating"] = rating

        if rating <= 2:
            self.disabled = True
            await interaction.response.send_modal(FeedbackModal(view))
            await interaction.message.edit(view=view)
        else:
            self.disabled = True
            await interaction.response.edit_message(
                embed=discord.Embed(
                    description="Thanks for the feedback!", color=discord.Color.green()
                ),
                view=view,
            )


class SurveyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=SURVEY_TIMEOUT)
        self.result = {"rating": None, "reason": None}
        self.message = None
        self.add_item(RatingSelect())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SupportSurvey(commands.Cog, name="Support Survey"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.plugin_db.get_partition(self)
        self.enabled = True

    async def cog_load(self):
        await self.bot.wait_until_ready()
        doc = await self.db.find_one({"_id": "settings"})
        if doc is not None:
            self.enabled = doc.get("enabled", True)

    @commands.Cog.listener()
    async def on_thread_close(self, thread, closer, silent, delete_channel, message, scheduled):
        if not self.enabled or silent:
            return

        recipient = thread.recipient
        if recipient is None:
            return

        log_channel = await self.get_log_channel()
        log_message = await self.find_log_message(log_channel, recipient) if log_channel else None

        self.bot.loop.create_task(self.run_survey(recipient, closer, log_channel, log_message))

    async def find_log_message(self, channel, recipient, attempts: int = 3):
        needles = (str(recipient.id), recipient.name, str(recipient))

        for attempt in range(attempts):
            try:
                async for msg in channel.history(limit=20):
                    if msg.author.id != self.bot.user.id or not msg.embeds:
                        continue
                    embed = msg.embeds[0]
                    haystack = " ".join(
                        str(part)
                        for part in (
                            embed.title,
                            embed.description,
                            getattr(embed.author, "name", None),
                            getattr(embed.footer, "text", None),
                        )
                        if part
                    )
                    if any(needle in haystack for needle in needles):
                        return msg
            except discord.HTTPException:
                return None

            if attempt < attempts - 1:
                await asyncio.sleep(1.5)

        return None

    async def run_survey(self, recipient, closer, log_channel, log_message):
        view = SurveyView()
        embed = discord.Embed(
            title="How was your support experience?",
            description="Pick a rating below - it only takes a second.",
            color=self.bot.main_color,
        )

        try:
            msg = await recipient.send(embed=embed, view=view)
        except discord.HTTPException:
            return

        view.message = msg

        await asyncio.sleep(SURVEY_TIMEOUT)

        try:
            await msg.delete()
        except discord.HTTPException:
            pass

        await self.post_result(recipient, closer, view.result, log_channel, log_message)

    async def post_result(self, recipient, closer, result, log_channel, log_message):
        rating = result["rating"]
        reason = result["reason"]

        if rating is None:
            desc = f"{recipient} ({recipient.id}) didn't respond to the survey."
            color = discord.Color.greyple()
        else:
            desc = f"{recipient} ({recipient.id}) rated their support **{rating}/5** ({'⭐' * rating})"
            if reason:
                desc += f"\n\n**What went wrong:**\n{reason}"
            color = discord.Color.green() if rating >= 3 else discord.Color.red()

        embed = discord.Embed(title="Support Survey Result", description=desc, color=color)
        embed.set_footer(text=f"Thread closed by {closer}")

        if log_message is not None:
            try:
                await log_message.edit(embeds=log_message.embeds + [embed])
                return
            except discord.HTTPException:
                pass

        if log_channel is not None:
            await log_channel.send(embed=embed)

    async def get_log_channel(self):
        channel = getattr(self.bot, "log_channel", None)
        if channel is not None:
            return channel
        channel_id = self.bot.config.get("log_channel_id")
        if channel_id:
            return self.bot.get_channel(int(channel_id))
        return None

    @commands.group(name="survey", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def survey_(self, ctx):
        status = "enabled" if self.enabled else "disabled"
        await ctx.send(
            embed=discord.Embed(
                color=self.bot.main_color, description=f"Support surveys are currently **{status}**."
            )
        )

    @survey_.command(name="enable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def survey_enable(self, ctx):
        self.enabled = True
        await self.db.find_one_and_update({"_id": "settings"}, {"$set": {"enabled": True}}, upsert=True)
        await ctx.send(embed=discord.Embed(color=self.bot.main_color, description="Surveys enabled."))

    @survey_.command(name="disable")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def survey_disable(self, ctx):
        self.enabled = False
        await self.db.find_one_and_update({"_id": "settings"}, {"$set": {"enabled": False}}, upsert=True)
        await ctx.send(embed=discord.Embed(color=self.bot.main_color, description="Surveys disabled."))


async def setup(bot):
    await bot.add_cog(SupportSurvey(bot))
async def setup(bot):
    await bot.add_cog(SupportSurvey(bot))
