import asyncio
import logging
import re
import typing
import discord
from discord import app_commands
from discord.ext import commands

# Initialize logger for moderation tracking
logger = logging.getLogger("MuteModerationCog")

# Custom Emoji Constants
EMOJI_MUTE = "<a:Mute:1535685459080912999>"
EMOJI_CROSS = "<:Cross:1535661936434479124>"
EMOJI_ARROW = "<a:Arrow:1535641409074372628>"
EMOJI_TICK = "<a:Tick:1535660962684870798>"

# Channel Configurations
SERVER_MSG_CHANNEL_ID = 917378312832180224
AUDIT_LOG_CHANNEL_ID = 1535687126966861856

# Duration parsing pattern (e.g., 10s, 5m, 2h, 1d)
TIME_REGEX = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)


def parse_duration(duration_str: str) -> typing.Optional[int]:
    """Parses duration strings like 10s, 5m, 2h, 1d into total seconds."""
    match = TIME_REGEX.match(duration_str.strip())
    if not match:
        return None

    value, unit = int(match.group(1)), match.group(2).lower()
    unit_multipliers = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }
    return value * unit_multipliers[unit]


# =============================================================================
# INTERACTIVE UI COMPONENTS
# =============================================================================
class MuteActionView(discord.ui.View):
    """Interactive component view attached to mute logs allowing quick unmute actions."""

    def __init__(
        self,
        target_member: discord.Member,
        moderator: discord.Member,
        original_reason: str,
    ):
        super().__init__(timeout=300.0)
        self.target_member = target_member
        self.moderator = moderator
        self.original_reason = original_reason

    @discord.ui.button(
        label="Quick Unmute",
        style=discord.ButtonStyle.success,
        custom_id="btn_quick_unmute",
        emoji="🔊",
    )
    async def quick_unmute_callback(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Allows moderators to remove a timeout directly from the log embed."""
        if (
            interaction.user.id != self.moderator.id
            and not interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Only {self.moderator.mention} or an Administrator can use this shortcut.",
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} You are lacking permission to moderate members in this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await self.target_member.timeout(
                None,
                reason=f"[Quick Unmute] By {interaction.user}. Original mute reason: {self.original_reason}",
            )

            button.disabled = True
            button.label = "Unmuted"
            button.style = discord.ButtonStyle.secondary
            await interaction.message.edit(view=self)

            await interaction.followup.send(
                f"{EMOJI_TICK} Successfully removed timeout for **{self.target_member}** (`{self.target_member.id}`).",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Failed to remove timeout due to missing hierarchy or bot permissions.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"{EMOJI_CROSS} An unexpected error occurred: {e}",
                ephemeral=True,
            )


# =============================================================================
# ADVANCED MUTE / TIMEOUT SYSTEM COG
# =============================================================================
class MuteSystem(commands.Cog):
    """Production-grade mute/timeout cog featuring duration parsing, error handling, and dual-channel routing."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._action_locks: typing.Dict[int, asyncio.Lock] = {}

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        """Returns an async lock to prevent race conditions during rapid command execution on the same user."""
        if user_id not in self._action_locks:
            self._action_locks[user_id] = asyncio.Lock()
        return self._action_locks[user_id]

    async def route_embed(
        self,
        guild: discord.Guild,
        fallback_channel: discord.TextChannel,
        channel_id: int,
        embed: discord.Embed,
        view: typing.Optional[discord.ui.View] = None,
    ):
        """Routes embeds to specified log channels, falling back to execution channel if target is inaccessible."""
        target_channel = self.bot.get_channel(channel_id) or guild.get_channel(
            channel_id
        )

        if target_channel and isinstance(target_channel, discord.TextChannel):
            try:
                await target_channel.send(embed=embed, view=view)
                return
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(
                    f"Failed to dispatch embed to configured channel {channel_id}: {e}"
                )

        try:
            await fallback_channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # =========================================================================
    # SLASH COMMAND: /mute
    # =========================================================================
    @app_commands.command(
        name="mute", description="Timeout/mute a member in the server with duration."
    )
    @app_commands.describe(
        user="The member to mute",
        duration="Duration of timeout (e.g., 10s, 5m, 2h, 1d)",
        reason="The reason for muting the user",
    )
    async def mute_slash(
        self,
        interaction: discord.Interaction,
        user: typing.Optional[discord.Member] = None,
        duration: typing.Optional[str] = None,
        reason: typing.Optional[str] = None,
    ):
        guild = interaction.guild
        server_icon = guild.icon.url if guild.icon else None

        # 1. Missing Argument Formatting Guard
        if user is None or duration is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_MUTE} Mute Format",
                description=f"Use\n{EMOJI_ARROW} `/mute user: duration: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. Duration Parsing Check
        seconds = parse_duration(duration)
        if seconds is None or seconds <= 0 or seconds > 2419200:  # Max 28 days
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} Invalid duration! Use `s` (seconds), `m` (minutes), `h` (hours), or `d` (days). Maximum duration is 28 days.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. Executor Permission Validation
        if not interaction.user.guild_permissions.moderate_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {interaction.user.mention} you are lacking permission of moderate members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 4. Prevent Self & Owner Enforcement
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} You cannot mute yourself!", ephemeral=True
            )
            return

        if user.id == guild.owner_id:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} You cannot mute the server owner!",
                ephemeral=True,
            )
            return

        # Lock execution per user
        lock = self.get_user_lock(user.id)
        if lock.locked():
            await interaction.response.send_message(
                f"{EMOJI_CROSS} A moderation action is currently processing for this user.",
                ephemeral=True,
            )
            return

        async with lock:
            # 5. Hierarchy Validation
            if (
                user.top_role >= interaction.user.top_role
                and guild.owner_id != interaction.user.id
            ):
                embed = discord.Embed(
                    description=f"{EMOJI_CROSS} You cannot mute **{user}** because their highest role is equal to or higher than yours.",
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )
                return

            if user.top_role >= guild.me.top_role:
                embed = discord.Embed(
                    description=f"{EMOJI_CROSS} I cannot mute **{user}** because their highest role is equal to or higher than my top role.",
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )
                return

            # 6. Direct Message Notification
            dm_sent = False
            dm_embed = discord.Embed(
                title=f"{EMOJI_MUTE} Mute Notification",
                description=f"You got muted in **{guild.name}** by {interaction.user.mention} for **{duration}** for **{reason}**",
                color=discord.Color.gold(),
                timestamp=discord.utils.utcnow(),
            )
            dm_embed.set_thumbnail(url=server_icon)

            try:
                await user.send(embed=dm_embed)
                dm_sent = True
            except (discord.Forbidden, discord.HTTPException):
                dm_sent = False

            # 7. Timeout Execution
            until_time = discord.utils.utcnow() + discord.utils.datetime.timedelta(
                seconds=seconds
            )
            try:
                await user.timeout(
                    until_time,
                    reason=f"{reason} | Muted by {interaction.user} for {duration}",
                )

                await interaction.response.send_message(
                    f"{EMOJI_TICK} Successfully muted **{user}** (`{user.id}`) for `{duration}`.",
                    ephemeral=True,
                )

                action_view = MuteActionView(
                    target_member=user,
                    moderator=interaction.user,
                    original_reason=reason,
                )

                # Public Announcement Embed
                public_embed = discord.Embed(
                    title=f"{EMOJI_MUTE} Mute notification",
                    description=f"{user.mention} got muted by {interaction.user.mention} for **{duration}** for **{reason}**",
                    color=discord.Color.gold(),
                    timestamp=discord.utils.utcnow(),
                )
                public_embed.set_thumbnail(url=user.display_avatar.url)
                public_embed.set_footer(
                    text=f"DM Status: {'Delivered' if dm_sent else 'Failed/Blocked'}",
                    icon_url=interaction.user.display_avatar.url,
                )

                await self.route_embed(
                    guild,
                    interaction.channel,
                    SERVER_MSG_CHANNEL_ID,
                    public_embed,
                )

                # Audit Log Embed
                log_embed = discord.Embed(
                    title=f"{EMOJI_MUTE} Member Muted",
                    description=(
                        f"**Target Member:** {user.mention} (`{user.id}`)\n"
                        f"**Moderator:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                        f"**Duration:** `{duration}`\n"
                        f"**DM Status:** `{'Delivered' if dm_sent else 'Failed/Blocked'}`\n"
                        f"**Reason:** `{reason}`"
                    ),
                    color=discord.Color.gold(),
                    timestamp=discord.utils.utcnow(),
                )
                log_embed.set_thumbnail(url=user.display_avatar.url)

                await self.route_embed(
                    guild,
                    interaction.channel,
                    AUDIT_LOG_CHANNEL_ID,
                    log_embed,
                    view=action_view,
                )

            except discord.Forbidden:
                await interaction.response.send_message(
                    f"{EMOJI_CROSS} Mute execution failed. Check bot permissions and role hierarchy.",
                    ephemeral=True,
                )
            except discord.HTTPException as e:
                await interaction.response.send_message(
                    f"{EMOJI_CROSS} Discord API Error during mute execution: {e}",
                    ephemeral=True,
                )

    # =========================================================================
    # SLASH COMMAND: /unmute
    # =========================================================================
    @app_commands.command(
        name="unmute", description="Remove timeout/unmute a member in the server."
    )
    @app_commands.describe(
        user="The member to unmute",
        reason="The reason for unmuting the user",
    )
    async def unmute_slash(
        self,
        interaction: discord.Interaction,
        user: typing.Optional[discord.Member] = None,
        reason: typing.Optional[str] = None,
    ):
        guild = interaction.guild
        server_icon = guild.icon.url if guild.icon else None

        # 1. Missing Argument Formatting Guard
        if user is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_MUTE} Unmute Format",
                description=f"Use\n{EMOJI_ARROW} `/unmute user: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. Permission Validation
        if not interaction.user.guild_permissions.moderate_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {interaction.user.mention} you are lacking permission of moderate members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. Active Timeout Check
        if not user.is_timed_out():
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} **{user}** is not currently muted or timed out.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 4. DM Direct Notification
        dm_sent = False
        dm_embed = discord.Embed(
            title=f"{EMOJI_TICK} Unmute Notification",
            description=f"You have been unmuted in **{guild.name}** by {interaction.user.mention} for **{reason}**",
            color=discord.Color.brand_green(),
            timestamp=discord.utils.utcnow(),
        )
        dm_embed.set_thumbnail(url=server_icon)

        try:
            await user.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

        # 5. Remove Timeout
        try:
            await user.timeout(None, reason=f"{reason} | Unmuted by {interaction.user}")

            await interaction.response.send_message(
                f"{EMOJI_TICK} Successfully unmuted **{user}** (`{user.id}`).",
                ephemeral=True,
            )

            public_embed = discord.Embed(
                title=f"{EMOJI_TICK} Unmute notification",
                description=f"{user.mention} got unmuted by {interaction.user.mention} for **{reason}**",
                color=discord.Color.brand_green(),
                timestamp=discord.utils.utcnow(),
            )
            public_embed.set_thumbnail(url=user.display_avatar.url)
            public_embed.set_footer(
                text=f"DM Status: {'Delivered' if dm_sent else 'Failed/Blocked'}",
                icon_url=interaction.user.display_avatar.url,
            )

            await self.route_embed(
                guild,
                interaction.channel,
                SERVER_MSG_CHANNEL_ID,
                public_embed,
            )

            log_embed = discord.Embed(
                title=f"{EMOJI_TICK} Member Unmuted",
                description=(
                    f"**Target Member:** {user.mention} (`{user.id}`)\n"
                    f"**Moderator:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                    f"**DM Status:** `{'Delivered' if dm_sent else 'Failed/Blocked'}`\n"
                    f"**Reason:** `{reason}`"
                ),
                color=discord.Color.brand_green(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.set_thumbnail(url=user.display_avatar.url)

            await self.route_embed(
                guild,
                interaction.channel,
                AUDIT_LOG_CHANNEL_ID,
                log_embed,
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Unmute failed due to missing bot permissions or hierarchy.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Discord API Error during unmute: {e}",
                ephemeral=True,
            )

    # =========================================================================
    # PREFIX COMMANDS (!mute, !unmute)
    # =========================================================================
    @commands.command(name="mute")
    @commands.guild_only()
    async def mute_prefix(
        self,
        ctx: commands.Context,
        user: typing.Optional[discord.Member] = None,
        duration: typing.Optional[str] = None,
        *,
        reason: typing.Optional[str] = None,
    ):
        server_icon = ctx.guild.icon.url if ctx.guild.icon else None

        if user is None or duration is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_MUTE} Mute Format",
                description=f"Use\n{EMOJI_ARROW} `/mute user: duration: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        seconds = parse_duration(duration)
        if seconds is None or seconds <= 0 or seconds > 2419200:
            await ctx.send(
                f"{EMOJI_CROSS} Invalid duration! Format with `s`, `m`, `h`, or `d` up to 28 days."
            )
            return

        if not ctx.author.guild_permissions.moderate_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {ctx.author.mention} you are lacking permission of moderate members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        if not user.is_timed_out():
            await ctx.send(f"{EMOJI_CROSS} **{user}** is not currently muted.")
            return

        dm_sent = False
        try:
            dm_embed = discord.Embed(
                title=f"{EMOJI_TICK} Unmute Notification",
                description=f"You have been unmuted in **{ctx.guild.name}** by {ctx.author.mention} for **{reason}**",
                color=discord.Color.brand_green(),
            )
            await user.send(embed=dm_embed)
            dm_sent = True
        except Exception:
            dm_sent = False

        try:
            await user.timeout(None, reason=f"{reason} | By {ctx.author}")

            public_embed = discord.Embed(
                title=f"{EMOJI_TICK} Unmute notification",
                description=f"{user.mention} got unmuted by {ctx.author.mention} for **{reason}**",
                color=discord.Color.brand_green(),
                timestamp=discord.utils.utcnow(),
            )
            public_embed.set_thumbnail(url=user.display_avatar.url)
            public_embed.set_footer(
                text=f"DM Status: {'Delivered' if dm_sent else 'Failed/Blocked'}",
                icon_url=ctx.author.display_avatar.url,
            )

            await self.route_embed(
                ctx.guild, ctx.channel, SERVER_MSG_CHANNEL_ID, public_embed
            )

            log_embed = discord.Embed(
                title=f"{EMOJI_TICK} Member Unmuted",
                description=(
                    f"**Target Member:** {user.mention} (`{user.id}`)\n"
                    f"**Moderator:** {ctx.author.mention} (`{ctx.author.id}`)\n"
                    f"**Reason:** `{reason}`"
                ),
                color=discord.Color.brand_green(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.set_thumbnail(url=user.display_avatar.url)

            await self.route_embed(
                ctx.guild, ctx.channel, AUDIT_LOG_CHANNEL_ID, log_embed
            )

        except Exception as e:
            await ctx.send(f"{EMOJI_CROSS} Failed to unmute user: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(MuteSystem(bot))
