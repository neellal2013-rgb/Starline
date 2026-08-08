import asyncio
import logging
import typing
import discord
from discord import app_commands
from discord.ext import commands

# Set up logging for moderation actions
logger = logging.getLogger("ModerationCog")

# Custom Emoji Constants
EMOJI_BAN = "<:Ban:1535687126966861856>"
EMOJI_CROSS = "<:Cross:1535661936434479124>"
EMOJI_ARROW = "<a:Arrow:1535641409074372628>"
EMOJI_TICK = "<a:Tick:1535660962684870798>"

# Channel Configurations
SERVER_MSG_CHANNEL_ID = 917378312832180224
AUDIT_LOG_CHANNEL_ID = 1535687126966861856


# =============================================================================
# INTERACTIVE UI COMPONENTS
# =============================================================================
class UndoUnbanView(discord.ui.View):
    """An interactive component view that allows moderators to quickly re-ban a user."""

    def __init__(self, target_user: discord.User, moderator: discord.Member, original_reason: str):
        super().__init__(timeout=180.0)
        self.target_user = target_user
        self.moderator = moderator
        self.original_reason = original_reason

    @discord.ui.button(
        label="Re-Ban User",
        style=discord.ButtonStyle.danger,
        custom_id="btn_reban_user",
        emoji="🔨"
    )
    async def reban_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ensure only the original moderator or admins can click the button
        if interaction.user.id != self.moderator.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Only {self.moderator.mention} or an Administrator can use this shortcut.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.guild.ban(
                self.target_user,
                reason=f"[Undo Unban] Re-banned by {interaction.user}. Original reason: {self.original_reason}",
                delete_message_days=0
            )
            
            button.disabled = True
            button.label = "User Re-Banned"
            await interaction.message.edit(view=self)

            await interaction.followup.send(
                f"{EMOJI_TICK} Successfully re-banned **{self.target_user}** (`{self.target_user.id}`).",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Failed to re-ban user due to missing hierarchy or permissions.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"{EMOJI_CROSS} An unexpected error occurred: {e}",
                ephemeral=True,
            )


# =============================================================================
# ADVANCED BAN SYSTEM COG
# =============================================================================
class BanSystem(commands.Cog):
    """Production-grade ban management cog supporting hybrid execution and error handling."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._action_locks: typing.Dict[int, asyncio.Lock] = {}

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        """Returns an async lock to prevent race conditions during rapid command triggers."""
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
        """Robust embed dispatcher that falls back if a specific log channel isn't configured."""
        target_channel = self.bot.get_channel(channel_id) or guild.get_channel(channel_id)
        
        if target_channel and isinstance(target_channel, discord.TextChannel):
            try:
                await target_channel.send(embed=embed, view=view)
                return
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Failed to post embed to target channel {channel_id}: {e}")

        # Fallback to current execution channel
        try:
            await fallback_channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # =========================================================================
    # SLASH COMMAND: /ban
    # =========================================================================
    @app_commands.command(
        name="ban", description="Ban a user permanently from the server."
    )
    @app_commands.describe(
        user="The member to ban from the server",
        reason="The reason for banning the user",
    )
    async def ban_slash(
        self,
        interaction: discord.Interaction,
        user: typing.Optional[discord.User] = None,
        reason: typing.Optional[str] = None,
    ):
        guild = interaction.guild
        server_icon = guild.icon.url if guild.icon else None

        # 1. Validation: Missing Arguments
        if user is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_BAN} Ban Format",
                description=f"Use\n{EMOJI_ARROW} `/ban user: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. Permission Verification (Executor)
        if not interaction.user.guild_permissions.ban_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {interaction.user.mention} you are lacking permission of ban members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. Self and Owner Guard
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} You cannot ban yourself!", ephemeral=True
            )
            return

        if user.id == guild.owner_id:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} You cannot ban the owner of this server!", ephemeral=True
            )
            return

        # Lock execution per-user to prevent concurrent race conditions
        lock = self.get_user_lock(user.id)
        if lock.locked():
            await interaction.response.send_message(
                f"{EMOJI_CROSS} A moderation action is already processing for this user.", ephemeral=True
            )
            return

        async with lock:
            # 4. Hierarchy Validation
            target_member = guild.get_member(user.id)
            if target_member:
                if target_member.top_role >= interaction.user.top_role and guild.owner_id != interaction.user.id:
                    embed = discord.Embed(
                        description=f"{EMOJI_CROSS} You cannot ban **{user}** because their role is equal to or higher than yours.",
                        color=discord.Color.red(),
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return

                if target_member.top_role >= guild.me.top_role:
                    embed = discord.Embed(
                        description=f"{EMOJI_CROSS} I cannot ban **{user}** because their role is equal to or higher than my top role.",
                        color=discord.Color.red(),
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return

            # 5. Direct Messaging Notification
            dm_sent = False
            dm_embed = discord.Embed(
                title=f"{EMOJI_BAN} Ban Notification",
                description=f"You got banned from **{guild.name}** by {interaction.user.mention} for **{reason}**",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            dm_embed.set_thumbnail(url=server_icon)

            try:
                await user.send(embed=dm_embed)
                dm_sent = True
            except (discord.Forbidden, discord.HTTPException):
                dm_sent = False

            # 6. Execute Ban Action
            try:
                await guild.ban(user, reason=f"{reason} | Moderated by {interaction.user}", delete_message_days=0)

                # Ephemeral confirmation for moderator
                await interaction.response.send_message(
                    f"{EMOJI_TICK} Successfully banned **{user}** (`{user.id}`).", ephemeral=True
                )

                # Server Public Embed
                public_embed = discord.Embed(
                    title=f"{EMOJI_BAN} Ban notification",
                    description=f"{user.mention} got banned by {interaction.user.mention} for **{reason}**",
                    color=discord.Color.red(),
                    timestamp=discord.utils.utcnow(),
                )
                public_embed.set_thumbnail(url=user.display_avatar.url)
                public_embed.set_footer(
                    text=f"DM Status: {'Delivered' if dm_sent else 'Failed/Blocked'}",
                    icon_url=interaction.user.display_avatar.url,
                )

                await self.route_embed(
                    guild, interaction.channel, SERVER_MSG_CHANNEL_ID, public_embed
                )

                # System Audit Embed
                log_embed = discord.Embed(
                    title=f"{EMOJI_BAN} Member Banned",
                    description=(
                        f"**Target User:** {user.mention} (`{user.id}`)\n"
                        f"**Moderator:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                        f"**DM Status:** `{'Sent' if dm_sent else 'Failed'}`\n"
                        f"**Reason:** `{reason}`"
                    ),
                    color=discord.Color.dark_red(),
                    timestamp=discord.utils.utcnow(),
                )
                log_embed.set_thumbnail(url=user.display_avatar.url)
                await self.route_embed(
                    guild, interaction.channel, AUDIT_LOG_CHANNEL_ID, log_embed
                )

            except discord.Forbidden:
                await interaction.response.send_message(
                    f"{EMOJI_CROSS} Ban execution failed. Verify bot permissions and role hierarchy.",
                    ephemeral=True,
                )
            except discord.HTTPException as e:
                await interaction.response.send_message(
                    f"{EMOJI_CROSS} Discord API Error occurred: {e}", ephemeral=True
                )

    # =========================================================================
    # SLASH COMMAND: /unban
    # =========================================================================
    @app_commands.command(
        name="unban", description="Unban a user from the server using their User ID."
    )
    @app_commands.describe(
        user_id="The ID of the user to unban",
        reason="The reason for unbanning the user",
    )
    async def unban_slash(
        self,
        interaction: discord.Interaction,
        user_id: typing.Optional[str] = None,
        reason: typing.Optional[str] = None,
    ):
        guild = interaction.guild
        server_icon = guild.icon.url if guild.icon else None

        # 1. Validation: Missing Arguments
        if user_id is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_BAN} Unban Format",
                description=f"Use\n{EMOJI_ARROW} `/unban user_id: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 2. Permission Verification
        if not interaction.user.guild_permissions.ban_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {interaction.user.mention} you are lacking permission of ban members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 3. Target Resolution
        try:
            clean_id = int(user_id.strip("<@!>"))
            target_user = await self.bot.fetch_user(clean_id)
        except (ValueError, discord.NotFound):
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} Invalid or non-existent User ID provided.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 4. Check Ban Registry
        try:
            await guild.fetch_ban(target_user)
        except discord.NotFound:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} **{target_user}** is not currently banned in this server.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # 5. DM Direct Notification
        dm_sent = False
        dm_embed = discord.Embed(
            title=f"{EMOJI_TICK} Unban Notification",
            description=f"You have been unbanned in **{guild.name}** by {interaction.user.mention} for **{reason}**",
            color=discord.Color.brand_green(),
            timestamp=discord.utils.utcnow(),
        )
        dm_embed.set_thumbnail(url=server_icon)

        try:
            await target_user.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

        # 6. Execute Unban Action
        try:
            await guild.unban(target_user, reason=f"{reason} | Unbanned by {interaction.user}")

            await interaction.response.send_message(
                f"{EMOJI_TICK} Successfully unbanned **{target_user}** (`{target_user.id}`).", ephemeral=True
            )

            # Interactive View to Re-Ban if required
            undo_view = UndoUnbanView(
                target_user=target_user,
                moderator=interaction.user,
                original_reason=reason
            )

            # Public Server Embed
            public_embed = discord.Embed(
                title=f"{EMOJI_TICK} Unban notification",
                description=f"{target_user.mention} got unbanned by {interaction.user.mention} for **{reason}**",
                color=discord.Color.brand_green(),
                timestamp=discord.utils.utcnow(),
            )
            public_embed.set_thumbnail(url=target_user.display_avatar.url)
            public_embed.set_footer(
                text=f"DM Status: {'Delivered' if dm_sent else 'Failed/Blocked'}",
                icon_url=interaction.user.display_avatar.url,
            )

            await self.route_embed(
                guild, interaction.channel, SERVER_MSG_CHANNEL_ID, public_embed, view=undo_view
            )

            # System Audit Embed
            log_embed = discord.Embed(
                title=f"{EMOJI_TICK} Member Unbanned",
                description=(
                    f"**Target User:** {target_user.mention} (`{target_user.id}`)\n"
                    f"**Moderator:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                    f"**DM Status:** `{'Sent' if dm_sent else 'Failed'}`\n"
                    f"**Reason:** `{reason}`"
                ),
                color=discord.Color.brand_green(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.set_thumbnail(url=target_user.display_avatar.url)
            await self.route_embed(
                guild, interaction.channel, AUDIT_LOG_CHANNEL_ID, log_embed
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Unban failed due to missing bot permissions.",
                ephemeral=True,
            )

    # =========================================================================
    # PREFIX COMMANDS IMPLEMENTATION (!ban, !unban)
    # =========================================================================
    @commands.command(name="ban")
    @commands.guild_only()
    async def ban_prefix(
        self,
        ctx: commands.Context,
        user: typing.Optional[discord.User] = None,
        *,
        reason: typing.Optional[str] = None,
    ):
        server_icon = ctx.guild.icon.url if ctx.guild.icon else None

        if user is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_BAN} Ban Format",
                description=f"Use\n{EMOJI_ARROW} `/ban user: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        if not ctx.author.guild_permissions.ban_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {ctx.author.mention} you are lacking permission of ban members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        try:
            try:
                dm_embed = discord.Embed(
                    title=f"{EMOJI_BAN} Ban Notification",
                    description=f"You got banned from **{ctx.guild.name}** by {ctx.author.mention} for **{reason}**",
                    color=discord.Color.red(),
                )
                await user.send(embed=dm_embed)
            except Exception:
                pass

            await ctx.guild.ban(user, reason=f"{reason} | By {ctx.author}", delete_message_days=0)

            public_embed = discord.Embed(
                title=f"{EMOJI_BAN} Ban notification",
                description=f"{user.mention} got banned by {ctx.author.mention} for **{reason}**",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            public_embed.set_thumbnail(url=user.display_avatar.url)
            await self.route_embed(
                ctx.guild, ctx.channel, SERVER_MSG_CHANNEL_ID, public_embed
            )

            log_embed = discord.Embed(
                title=f"{EMOJI_BAN} Member Banned",
                description=f"**Target:** {user.mention} (`{user.id}`)\n**Moderator:** {ctx.author.mention} (`{ctx.author.id}`)\n**Reason:** `{reason}`",
                color=discord.Color.dark_red(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.set_thumbnail(url=user.display_avatar.url)
            await self.route_embed(
                ctx.guild, ctx.channel, AUDIT_LOG_CHANNEL_ID, log_embed
            )

        except discord.Forbidden:
            await ctx.send(f"{EMOJI_CROSS} I do not have permission to ban this user.")

    @commands.command(name="unban")
    @commands.guild_only()
    async def unban_prefix(
        self,
        ctx: commands.Context,
        user_id: typing.Optional[str] = None,
        *,
        reason: typing.Optional[str] = None,
    ):
        server_icon = ctx.guild.icon.url if ctx.guild.icon else None

        if user_id is None or reason is None or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_BAN} Unban Format",
                description=f"Use\n{EMOJI_ARROW} `/unban user_id: reason:`",
                color=discord.Color.gold(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        if not ctx.author.guild_permissions.ban_members:
            embed = discord.Embed(
                description=f"{EMOJI_CROSS} {ctx.author.mention} you are lacking permission of ban members in this server",
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=server_icon)
            await ctx.send(embed=embed)
            return

        try:
            clean_id = int(user_id.strip("<@!>"))
            target_user = await self.bot.fetch_user(clean_id)
            await ctx.guild.unban(target_user, reason=f"{reason} | By {ctx.author}")

            try:
                dm_embed = discord.Embed(
                    title=f"{EMOJI_TICK} Unban Notification",
                    description=f"You have been unbanned in **{ctx.guild.name}** by {ctx.author.mention} for **{reason}**",
                    color=discord.Color.brand_green(),
                )
                await target_user.send(embed=dm_embed)
            except Exception:
                pass

            public_embed = discord.Embed(
                title=f"{EMOJI_TICK} Unban notification",
                description=f"{target_user.mention} got unbanned by {ctx.author.mention} for **{reason}**",
                color=discord.Color.brand_green(),
                timestamp=discord.utils.utcnow(),
            )
            public_embed.set_thumbnail(url=target_user.display_avatar.url)
            await self.route_embed(
                ctx.guild, ctx.channel, SERVER_MSG_CHANNEL_ID, public_embed
            )

            log_embed = discord.Embed(
                title=f"{EMOJI_TICK} Member Unbanned",
                description=f"**Target:** {target_user.mention} (`{target_user.id}`)\n**Moderator:** {ctx.author.mention} (`{ctx.author.id}`)\n**Reason:** `{reason}`",
                color=discord.Color.brand_green(),
                timestamp=discord.utils.utcnow(),
            )
            log_embed.set_thumbnail(url=target_user.display_avatar.url)
            await self.route_embed(
                ctx.guild, ctx.channel, AUDIT_LOG_CHANNEL_ID, log_embed
            )

        except Exception as e:
            await ctx.send(f"{EMOJI_CROSS} Failed to unban user: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(BanSystem(bot))
