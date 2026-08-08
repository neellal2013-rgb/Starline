import asyncio
import logging
import typing
import discord
from discord import app_commands
from discord.ext import commands

# Initialize logger for moderation tracking
logger = logging.getLogger("KickModerationCog")

# Custom Emoji Constants
EMOJI_KICK = "<:Kick:1535685465506586685>"
EMOJI_CROSS = "<:Cross:1535661936434479124>"
EMOJI_ARROW = "<a:Arrow:1535641409074372628>"
EMOJI_TICK = "<a:Tick:1535660962684870798>"

# Channel Configurations
SERVER_MSG_CHANNEL_ID = 917378312832180224
AUDIT_LOG_CHANNEL_ID = 1535687126966861856


# =============================================================================
# INTERACTIVE UI COMPONENTS
# =============================================================================
class KickActionView(discord.ui.View):
    """Interactive component view attached to kick notices allowing quick administrative actions."""

    def __init__(
        self,
        target_user: discord.User,
        moderator: discord.Member,
        original_reason: str,
    ):
        super().__init__(timeout=300.0)
        self.target_user = target_user
        self.moderator = moderator
        self.original_reason = original_reason

    @discord.ui.button(
        label="Escalate to Ban",
        style=discord.ButtonStyle.danger,
        custom_id="btn_kick_escalate_ban",
        emoji="🔨",
    )
    async def escalate_ban_callback(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Allows moderators to escalate a prior kick into a permanent ban directly from the log channel."""
        if (
            interaction.user.id != self.moderator.id
            and not interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Only {self.moderator.mention} or an Administrator can use this shortcut.",
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} You are lacking permission to ban members in this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.guild.ban(
                self.target_user,
                reason=f"[Escalated from Kick] By {interaction.user}. Original reason: {self.original_reason}",
                delete_message_days=0,
            )

            button.disabled = True
            button.label = "Escalated to Ban"
            button.style = discord.ButtonStyle.secondary
            await interaction.message.edit(view=self)

            await interaction.followup.send(
                f"{EMOJI_TICK} Successfully escalated kick to ban for **{self.target_user}** (`{self.target_user.id}`).",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Failed to ban user due to missing hierarchy or bot permissions.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"{EMOJI_CROSS} An unexpected error occurred: {e}",
                ephemeral=True,
            )


# =============================================================================
# ADVANCED KICK SYSTEM COG
# =============================================================================
class KickSystem(commands.Cog):
    """Production-grade kick management cog featuring rate-limiting, error protection, and dual-channel routing."""

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
        """Routes embeds to specified log channels, automatically falling back to current channel if unaccessible."""
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

        # Fallback to local channel if designated target is inaccessible
        try:
            await fallback_channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # =========================================================================
    # SLASH COMMAND: /kick
    # =========================================================================
    @app_commands.command(
        name="kick", description="Kick a user from the server with a reason."
    )
    @app_commands.describe(
        user="The member to kick from the server",
        reason="The reason for kicking the user",
    )
    async def kick_slash(
        self,
        interaction: discord.Interaction,
        user: typing.Optional[discord.User] = None,
        reason: typing.Optional[str] = None,
    ):
        guild = interaction.guild
        server_icon = guild.icon.url if guild.icon else None

        # 1. Missing Argument Formatting Guard
        if user is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_KICK} Kick Format",
                description=f"Use\n{EMOJI_ARROW} `/kick user: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. Executor Permission Validation
        if not interaction.user.guild_permissions.kick_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {interaction.user.mention} you are lacking permission of kick members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. Prevent Self & Owner Enforcement
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} You cannot kick yourself!", ephemeral=True
            )
            return

        if user.id == guild.owner_id:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} You cannot kick the server owner!",
                ephemeral=True,
            )
            return

        # Lock execution per user to prevent concurrent duplicate actions
        lock = self.get_user_lock(user.id)
        if lock.locked():
            await interaction.response.send_message(
                f"{EMOJI_CROSS} A moderation command is currently executing for this user.",
                ephemeral=True,
            )
            return

        async with lock:
            # 4. Role Hierarchy Validation
            target_member = guild.get_member(user.id)

            if target_member is None:
                embed = discord.Embed(
                    description=f"{EMOJI_CROSS} **{user}** is not in this server or could not be found.",
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )
                return

            if (
                target_member.top_role >= interaction.user.top_role
                and guild.owner_id != interaction.user.id
            ):
                embed = discord.Embed(
                    description=f"{EMOJI_CROSS} You cannot kick **{user}** because their highest role is equal to or higher than yours.",
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )
                return

            if target_member.top_role >= guild.me.top_role:
                embed = discord.Embed(
                    description=f"{EMOJI_CROSS} I cannot kick **{user}** because their highest role is equal to or higher than my top role.",
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )
                return

            # 5. Direct Message Target
            dm_sent = False
            dm_embed = discord.Embed(
                title=f"{EMOJI_KICK} Kick Notification",
                description=f"You got kicked from **{guild.name}** by {interaction.user.mention} for **{reason}**",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            dm_embed.set_thumbnail(url=server_icon)

            try:
                await target_member.send(embed=dm_embed)
                dm_sent = True
            except (discord.Forbidden, discord.HTTPException):
                dm_sent = False

            # 6. Execute Kick Action
            try:
                await target_member.kick(
                    reason=f"{reason} | Kicked by {interaction.user}"
                )

                # Ephemeral feedback to moderator
                await interaction.response.send_message(
                    f"{EMOJI_TICK} Successfully kicked **{user}** (`{user.id}`).",
                    ephemeral=True,
                )

                # Interactive UI view for action logs
                action_view = KickActionView(
                    target_user=user,
                    moderator=interaction.user,
                    original_reason=reason,
                )

                # Server Public Response Embed
                public_embed = discord.Embed(
                    title=f"{EMOJI_KICK} Kick notification",
                    description=f"{user.mention} got kicked by {interaction.user.mention} for **{reason}**",
                    color=discord.Color.orange(),
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
                    title=f"{EMOJI_KICK} Member Kicked",
                    description=(
                        f"**Target Member:** {user.mention} (`{user.id}`)\n"
                        f"**Moderator:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                        f"**DM Status:** `{'Delivered' if dm_sent else 'Failed/Blocked'}`\n"
                        f"**Reason:** `{reason}`"
                    ),
                    color=discord.Color.dark_orange(),
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
                    f"{EMOJI_CROSS} Kick failed. Check bot permissions and role hierarchy position.",
                    ephemeral=True,
                )
            except discord.HTTPException as e:
                await interaction.response.send_message(
                    f"{EMOJI_CROSS} Discord API Error during kick execution: {e}",
                    ephemeral=True,
                )

    # =========================================================================
    # PREFIX COMMAND IMPLEMENTATION (!kick)
    # =========================================================================
    @commands.command(name="kick")
    @commands.guild_only()
    async def kick_prefix(
        self,
        ctx: commands.Context,
        user: typing.Optional[discord.Member] = None,
        *,
        reason: typing.Optional[str] = None,
    ):
        server_icon = ctx.guild.icon.url if ctx.guild.icon else None

        # 1. Missing Argument Handler
        if user is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_KICK} Kick Format",
                description=f"Use\n{EMOJI_ARROW} `/kick user: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        # 2. Permission Check
        if not ctx.author.guild_permissions.kick_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {ctx.author.mention} you are lacking permission of kick members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        # 3. Guard Self / Owner
        if user.id == ctx.author.id:
            await ctx.send(f"{EMOJI_CROSS} You cannot kick yourself!")
            return

        if user.id == ctx.guild.owner_id:
            await ctx.send(f"{EMOJI_CROSS} You cannot kick the server owner!")
            return

        # 4. Role Hierarchy Check
        if (
            user.top_role >= ctx.author.top_role
            and ctx.guild.owner_id != ctx.author.id
        ):
            await ctx.send(
                f"{EMOJI_CROSS} You cannot kick **{user}** due to role hierarchy."
            )
            return

        if user.top_role >= ctx.guild.me.top_role:
            await ctx.send(
                f"{EMOJI_CROSS} I cannot kick **{user}** as their role is higher than mine."
            )
            return

        # 5. DM Dispatch
        dm_sent = False
        try:
            dm_embed = discord.Embed(
                title=f"{EMOJI_KICK} Kick Notification",
                description=f"You got kicked from **{ctx.guild.name}** by {ctx.author.mention} for **{reason}**",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow(),
            )
            dm_embed.set_thumbnail(url=server_icon)
            await user.send(embed=dm_embed)
            dm_sent = True
        except Exception:
            dm_sent = False

        # 6. Kick Execution & Embed Dispatch
        try:
            await user.kick(reason=f"{reason} | By {ctx.author}")

            action_view = KickActionView(
                target_user=user,
                moderator=ctx.author,
                original_reason=reason,
            )

            public_embed = discord.Embed(
                title=f"{EMOJI_KICK} Kick notification",
                description=f"{user.mention} got kicked by {ctx.author.mention} for **{reason}**",
                color=discord.Color.orange(),
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
                title=f"{EMOJI_KICK} Member Kicked",
                description=(
                    f"**Target Member:** {user.mention} (`{user.id}`)\n"
                    f"**Moderator:** {ctx.author.mention} (`{ctx.author.id}`)\n"
                    f"**DM Status:** `{'Delivered' if dm_sent else 'Failed/Blocked'}`\n"
                    f"**Reason:** `{reason}`"
                ),
                color=discord.Color.dark_orange(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.set_thumbnail(url=user.display_avatar.url)

            await self.route_embed(
                ctx.guild,
                ctx.channel,
                AUDIT_LOG_CHANNEL_ID,
                log_embed,
                view=action_view,
            )

        except discord.Forbidden:
            await ctx.send(
                f"{EMOJI_CROSS} I do not have permission to kick this user."
            )
        except Exception as e:
            await ctx.send(f"{EMOJI_CROSS} Failed to kick user: {e}")

    # =========================================================================
    # COG ERROR HANDLING
    # =========================================================================
    @kick_prefix.error
    async def kick_prefix_error(
        self, ctx: commands.Context, error: commands.CommandError
    ):
        """Dedicated error handler for prefix command exceptions."""
        server_icon = ctx.guild.icon.url if ctx.guild.icon else None

        if isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title=f"{EMOJI_KICK} Kick Format",
                description=f"Use\n{EMOJI_ARROW} `/kick user: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send(
                f"{EMOJI_CROSS} Could not find that member in this server."
            )
        elif isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {ctx.author.mention} you are lacking permission of kick members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(KickSystem(bot))
                 
