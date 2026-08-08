import asyncio
import datetime
import logging
import typing
import discord
from discord import app_commands
from discord.ext import commands

# Configure Local Logging
logger = logging.getLogger("DiscordBot.Purge")

# Custom Emoji Constants
EMOJI_DUSTBIN = "<:Dustbin:1535670766631264369>"
EMOJI_TICK = "<a:Tick:1535660962684870798>"
EMOJI_CROSS = "<:Cross:1535661936434479124>"
EMOJI_ARROW = "<a:Arrow:1535641409074372628>"

# Audit Log Channel ID for Purge Events
PURGE_LOG_CHANNEL_ID = 917378312832180224


class PurgeConfirmView(discord.ui.View):
    """Interactive confirmation view for large batch purges."""

    def __init__(
        self,
        cog: "Purge",
        author: discord.User,
        amount: int,
        filter_type: str,
        target_user: typing.Optional[discord.User] = None,
    ):
        super().__init__(timeout=30)
        self.cog = cog
        self.author = author
        self.amount = amount
        self.filter_type = filter_type
        self.target_user = target_user
        self.value = None

    @discord.ui.button(
        label="Confirm Purge",
        style=discord.ButtonStyle.danger,
        emoji="⚠️",
        custom_id="confirm_purge",
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ You are not authorized to use these controls.",
                ephemeral=True,
            )
            return
        self.value = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        emoji="❌",
        custom_id="cancel_purge",
    )
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ You are not authorized to use these controls.",
                ephemeral=True,
            )
            return
        self.value = False
        self.stop()
        embed = discord.Embed(
            title=f"{EMOJI_CROSS} Purge Cancelled",
            description="The bulk deletion request was aborted by the user.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


class Purge(commands.Cog):
    """Advanced moderation module for channel message cleanup and logging."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def create_filter_check(
        self,
        filter_type: str,
        target_user: typing.Optional[discord.User] = None,
    ):
        """Constructs predicate functions for selective purging."""

        def check(msg: discord.Message) -> bool:
            # 14 days limitation check for Discord API bulk delete
            two_weeks_ago = discord.utils.utcnow() - datetime.timedelta(
                days=14
            )
            if msg.created_at < two_weeks_ago:
                return False

            if filter_type == "bots":
                return msg.author.bot
            elif filter_type == "users":
                return not msg.author.bot
            elif filter_type == "links":
                return "http://" in msg.content or "https://" in msg.content
            elif filter_type == "attachments":
                return len(msg.attachments) > 0
            elif filter_type == "target" and target_user:
                return msg.author.id == target_user.id
            return True

        return check

    async def log_purge_action(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        moderator: discord.User,
        count: int,
        filter_type: str,
    ):
        """Dispatches rich audit logs to the configured moderation log channel."""
        log_channel = self.bot.get_channel(PURGE_LOG_CHANNEL_ID)
        if not log_channel:
            return

        embed = discord.Embed(
            title=f"{EMOJI_DUSTBIN} Bulk Delete Audit Log",
            color=discord.Color.dark_red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Moderator", value=f"{moderator.mention} (`{moderator.id}`)", inline=True)
        embed.add_field(name="Channel", value=f"{channel.mention} (`{channel.id}`)", inline=True)
        embed.add_field(name="Messages Deleted", value=f"**{count}**", inline=True)
        embed.add_field(name="Filter Applied", value=f"`{filter_type.upper()}`", inline=True)
        embed.set_footer(text=f"Guild: {guild.name}", icon_url=guild.icon.url if guild.icon else None)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException as e:
            logger.error(f"Failed to dispatch purge log: {e}")

    # ---------------------------------------------------------------------------
    # Primary Slash Command Implementation
    # ---------------------------------------------------------------------------
    @app_commands.command(
        name="purge",
        description="Bulk delete a specified number of messages (Max: 200).",
    )
    @app_commands.describe(
        number="The number of messages to delete (1-200)",
        filter_type="Filter criteria: all, bots, users, links, attachments",
        target_user="Target a specific user's messages only",
    )
    @app_commands.choices(
        filter_type=[
            app_commands.Choice(name="All Messages", value="all"),
            app_commands.Choice(name="Bot Messages Only", value="bots"),
            app_commands.Choice(name="Human Messages Only", value="users"),
            app_commands.Choice(name="Messages with Links", value="links"),
            app_commands.Choice(name="Messages with Attachments", value="attachments"),
        ]
    )
    async def purge_slash(
        self,
        interaction: discord.Interaction,
        number: typing.Optional[int] = None,
        filter_type: typing.Optional[app_commands.Choice[str]] = None,
        target_user: typing.Optional[discord.User] = None,
    ):
        server_icon = (
            interaction.guild.icon.url if interaction.guild.icon else None
        )
        selected_filter = filter_type.value if filter_type else "all"

        if target_user:
            selected_filter = "target"

        # 1. Missing Required Argument Handler
        if number is None:
            embed = discord.Embed(
                title=f"{EMOJI_DUSTBIN} Purge",
                description=f"Use\n{EMOJI_ARROW} use `/purge number:` limit 200.",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. Permission Validation
        if not interaction.user.guild_permissions.manage_messages:
            embed = discord.Embed(
                description=f"{interaction.user.mention} you're lacking permission of `Manage Messages`.",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. Parameter Boundary Check (> 200 limit)
        if number > 200 or number <= 0:
            embed = discord.Embed(
                title=f"{EMOJI_DUSTBIN} Purge",
                description=f"{EMOJI_CROSS} Purging {number}",
                color=discord.Color.red(),
            )
            embed.set_footer(text="The maximum purge limit is 200 messages.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 4. Interactive Confirmation for Large Operations (> 50 messages)
        if number > 50:
            confirm_embed = discord.Embed(
                title=f"{EMOJI_DUSTBIN} Confirm Large Purge Operation",
                description=(
                    f"You are about to delete up to **{number}** messages in {interaction.channel.mention}.\n"
                    f"**Filter Mode:** `{selected_filter.upper()}`\n\n"
                    f"Are you sure you want to proceed?"
                ),
                color=discord.Color.orange(),
            )
            view = PurgeConfirmView(
                self, interaction.user, number, selected_filter, target_user
            )
            await interaction.response.send_message(
                embed=confirm_embed, view=view, ephemeral=True
            )
            await view.wait()

            if view.value is not True:
                return
        else:
            await interaction.response.defer(ephemeral=True)

        # 5. Execute Purge Routine
        check_func = self.create_filter_check(selected_filter, target_user)

        try:
            deleted_messages = await interaction.channel.purge(
                limit=number, check=check_func
            )
            count = len(deleted_messages)

            embed = discord.Embed(
                title=f"{EMOJI_DUSTBIN} Purge",
                description=f"{EMOJI_TICK} Purging {count}",
                color=discord.Color.brand_green(),
            )
            embed.set_footer(text=f"Filter: {selected_filter.capitalize()}")

            if interaction.response.is_done():
                await interaction.followup.send(embed=embed)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)

            # Audit Log Emission
            await self.log_purge_action(
                interaction.guild,
                interaction.channel,
                interaction.user,
                count,
                selected_filter,
            )

        except discord.Forbidden:
            embed = discord.Embed(
                title=f"{EMOJI_CROSS} Permission Error",
                description="Bot requires `Manage Messages` permissions to perform bulk operations.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
        except discord.HTTPException as e:
            logger.error(f"HTTP Error during purge operation: {e}")

    # ---------------------------------------------------------------------------
    # Legacy Prefix Command Handler
    # ---------------------------------------------------------------------------
    @commands.command(name="purge", aliases=["clear", "clean"])
    async def purge_prefix(self, ctx: commands.Context, number: typing.Optional[int] = None):
        server_icon = ctx.guild.icon.url if ctx.guild.icon else None

        # 1. Missing Required Argument Handler
        if number is None:
            embed = discord.Embed(
                title=f"{EMOJI_DUSTBIN} Purge",
                description=f"Use\n{EMOJI_ARROW} use `/purge number:` limit 200.",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        # 2. Permission Validation
        if not ctx.author.guild_permissions.manage_messages:
            embed = discord.Embed(
                description=f"{ctx.author.mention} you're lacking permission of `Manage Messages`.",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        # 3. Parameter Boundary Check (> 200 limit)
        if number > 200 or number <= 0:
            embed = discord.Embed(
                title=f"{EMOJI_DUSTBIN} Purge",
                description=f"{EMOJI_CROSS} Purging {number}",
                color=discord.Color.red(),
            )
            embed.set_footer(text="The maximum purge limit is 200 messages.")
            await ctx.send(embed=embed)
            return

        # Delete Command Invocation Message
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        # 4. Execute Bulk Delete
        check_func = self.create_filter_check("all")

        try:
            deleted = await ctx.channel.purge(limit=number, check=check_func)
            count = len(deleted)

            embed = discord.Embed(
                title=f"{EMOJI_DUSTBIN} Purge",
                description=f"{EMOJI_TICK} Purging {count}",
                color=discord.Color.brand_green(),
            )
            response_msg = await ctx.send(embed=embed)

            # Auto-clean notification embed after 5 seconds
            await asyncio.sleep(5)
            try:
                await response_msg.delete()
            except discord.HTTPException:
                pass

            # Audit Log Emission
            await self.log_purge_action(
                ctx.guild, ctx.channel, ctx.author, count, "all"
            )

        except discord.Forbidden:
            embed = discord.Embed(
                title=f"{EMOJI_CROSS} Error",
                description="I lack the required `Manage Messages` permission to delete messages.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Purge(bot))
      
