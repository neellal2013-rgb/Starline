import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import logging
import asyncio
import os
import time
import math
from typing import Optional, List, Dict, Tuple, Any

# Dedicated Logging System Engine
logger = logging.getLogger("StarlineBotManager")
logger.setLevel(logging.INFO)

# Configuration Constants
LOG_CHANNEL_ID: int = 1536319515728674927
TARGET_NICKNAME: str = "Starline Supporters"
RESTRICTED_ROLE_ID: int = 915634037081657414
DB_PATH: str = "/app/data/bot_exclusions.db"


# -----------------------------------------------------------------------------
# Enterprise Asynchronous SQLite Database Engine
# -----------------------------------------------------------------------------
class BotManagerDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self):
        """Initializes tables for bot exclusions and historical audit logging."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            # Table 1: Excluded Bots
            await db.execute("""
                CREATE TABLE IF NOT EXISTS exclusions (
                    bot_id INTEGER PRIMARY KEY,
                    excluded_at REAL,
                    requested_by INTEGER,
                    reason TEXT
                )
            """)
            
            # Table 2: Audit Logs
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER,
                    guild_id INTEGER,
                    action_type TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    executor_id INTEGER,
                    timestamp REAL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_bot ON bot_audit_logs(bot_id);")
            await db.commit()

    async def add_exclusion(self, bot_id: int, requested_by: int, reason: str = "Manual override"):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO exclusions (bot_id, excluded_at, requested_by, reason)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    excluded_at = ?,
                    requested_by = ?,
                    reason = ?
            """, (bot_id, time.time(), requested_by, reason, time.time(), requested_by, reason))
            await db.commit()

    async def remove_exclusion(self, bot_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM exclusions WHERE bot_id = ?", (bot_id,))
            await db.commit()

    async def is_excluded(self, bot_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT 1 FROM exclusions WHERE bot_id = ?", (bot_id,)) as cursor:
                row = await cursor.fetchone()
                return row is not None

    async def get_all_exclusions(self) -> List[int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT bot_id FROM exclusions") as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

    async def log_audit_event(self, bot_id: int, guild_id: int, action_type: str, old_val: str, new_val: str, executor_id: Optional[int] = None):
        """Persists audit events into SQLite database history."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO bot_audit_logs (bot_id, guild_id, action_type, old_value, new_value, executor_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (bot_id, guild_id, action_type, old_val, new_val, executor_id or 0, time.time()))
            await db.commit()

    async def fetch_audit_history(self, limit: int = 50) -> List[Tuple[int, int, str, str, str, int, float]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT bot_id, guild_id, action_type, old_value, new_value, executor_id, timestamp FROM bot_audit_logs ORDER BY id DESC LIMIT ?",
                (limit,)
            ) as cursor:
                return await cursor.fetchall()


# -----------------------------------------------------------------------------
# Dynamic Interactive Pagination View for Audit Logs
# -----------------------------------------------------------------------------
class AuditLogPaginator(discord.ui.View):
    def __init__(self, author_id: int, logs: List[Tuple[int, int, str, str, str, int, float]]):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.logs = logs
        self.per_page = 5
        self.current_page = 1
        self.total_pages = max(1, math.ceil(len(self.logs) / self.per_page))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the command invoker can control this log menu.", ephemeral=True)
            return False
        return True

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="📜 Bot Management Historical Audit Logs",
            color=discord.Color.from_rgb(114, 137, 218)
        )

        start_idx = (self.current_page - 1) * self.per_page
        end_idx = start_idx + self.per_page
        batch = self.logs[start_idx:end_idx]

        if not batch:
            embed.description = "No audit log entries recorded yet."
            return embed

        for bot_id, guild_id, action_type, old_val, new_val, exec_id, ts in batch:
            time_str = f"<t:{int(ts)}:R>"
            exec_str = f"<@{exec_id}>" if exec_id else "System Automation"
            field_name = f"🤖 Bot: <@{bot_id}> | Action: `{action_type}`"
            field_value = (
                f"**Time:** {time_str}\n"
                f"**Actor:** {exec_str}\n"
                f"**Old Value:** `{old_val}`\n"
                f"**New Value:** `{new_val}`"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)

        embed.set_footer(text=f"Page {self.current_page}/{self.total_pages} • Starline Security Audit System")
        return embed

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1:
            self.current_page -= 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages:
            self.current_page += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


# -----------------------------------------------------------------------------
# Main Core Extension Cog
# -----------------------------------------------------------------------------
class BotNameManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = BotManagerDatabase(DB_PATH)
        self.target_nickname = TARGET_NICKNAME

    async def cog_load(self):
        await self.db.initialize()
        self.sequential_bot_manager.start()
        logger.info("BotNameManager Suite loaded with persistent SQLite audit storage.")

    async def cog_unload(self):
        self.sequential_bot_manager.cancel()

    # -------------------------------------------------------------------------
    # Role Policy Logic (Grants role 915634037081657414 if missing)
    # -------------------------------------------------------------------------
    async def ensure_bot_has_role(self, member: discord.Member) -> bool:
        """Grants the target role if the bot lacks it. Leaves manually assigned roles untouched."""
        guild = member.guild
        me = guild.me

        if not me.guild_permissions.manage_roles:
            return False

        target_role = guild.get_role(RESTRICTED_ROLE_ID)
        if not target_role:
            return False

        if me.top_role <= target_role:
            return False

        if target_role not in member.roles:
            try:
                await member.add_roles(target_role, reason="Automated Starline Policy: Missing bot role granted.")
                
                # Log event in Database & Channel
                await self.db.log_audit_event(
                    bot_id=member.id,
                    guild_id=guild.id,
                    action_type="RoleGranted",
                    old_val="No Role",
                    new_val=f"Role ID {RESTRICTED_ROLE_ID}"
                )
                await self.send_audit_embed(
                    guild=guild,
                    member=member,
                    old_name=member.display_name,
                    action_type="RoleGranted",
                    role_given=target_role.mention,
                    role_removed="None"
                )
                return True
            except discord.Forbidden:
                logger.warning(f"Forbidden: Unable to grant role to bot {member.name}")
            except Exception as e:
                logger.error(f"Error granting role to bot {member.name}: {e}")

        return False

    # -------------------------------------------------------------------------
    # Nickname Policy Logic
    # -------------------------------------------------------------------------
    async def process_single_bot(self, member: discord.Member) -> bool:
        """Applies 'Starline Supporters' nickname if bot is not excluded."""
        guild = member.guild

        if not member.bot or member.id == self.bot.user.id:
            return False

        if await self.db.is_excluded(member.id):
            return False

        if member.nick == self.target_nickname:
            return False

        me = guild.me
        if not me.guild_permissions.manage_nicknames or me.top_role <= member.top_role:
            return False

        try:
            old_name = member.display_name
            await member.edit(nick=self.target_nickname, reason="Automated Starline Supporters Naming Policy")
            
            # Log in Database & Channel
            await self.db.log_audit_event(
                bot_id=member.id,
                guild_id=guild.id,
                action_type="NicknameSync",
                old_val=old_name,
                new_val=self.target_nickname
            )
            await self.send_audit_embed(
                guild=guild,
                member=member,
                old_name=old_name,
                action_type="Updated",
                role_given="None",
                role_removed="None"
            )
            return True
        except discord.Forbidden:
            logger.warning(f"Forbidden: Cannot edit nickname for bot {member.name}")
        except discord.HTTPException as e:
            logger.error(f"HTTP Exception while editing bot nickname: {e}")

        return False

    async def send_audit_embed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        old_name: str,
        action_type: str = "Updated",
        role_given: str = "None",
        role_removed: str = "None",
        executor: Optional[discord.Member] = None
    ):
        """Dispatches notification embeds to channel 1536319515728674927."""
        channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if not channel:
            return

        is_reset = action_type == "Reset"
        is_locked = action_type == "Locked"
        is_role = action_type == "RoleGranted"

        if is_reset:
            color = discord.Color.from_rgb(231, 76, 60)
            title = "🔴 Bot Nickname Reset"
            desc = f"Nickname for {member.mention} was reset back to default."
        elif is_locked:
            color = discord.Color.from_rgb(46, 204, 113)
            title = "🟢 Bot Nickname Policy Re-Applied"
            desc = f"Exclusion removed for {member.mention}. Enforcing **`{self.target_nickname}`**."
        elif is_role:
            color = discord.Color.from_rgb(241, 196, 15)
            title = "🛡️ Bot Role Granted"
            desc = f"Assigned missing role policy to {member.mention}."
        else:
            color = discord.Color.from_rgb(88, 101, 242)
            title = "✨ Bot Nickname Synchronized"
            desc = f"{member.mention} nickname updated to **`{self.target_nickname}`**."

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="🤖 Target Bot", value=f"{member.name}\n(`{member.id}`)", inline=True)
        embed.add_field(name="🏷️ Old Display Name", value=f"`{old_name}`", inline=True)
        embed.add_field(name="⚡ Current Display Name", value=f"`{member.display_name}`", inline=True)

        role_info = f"**Given:** {role_given}\n**Removed:** {role_removed}"
        embed.add_field(name="Role", value=role_info, inline=False)

        if executor:
            embed.add_field(name="🛡️ Action Invoked By", value=f"{executor.mention}", inline=False)

        embed.set_footer(text=f"Server: {guild.name} • Starline Security Engine", icon_url=guild.icon.url if guild.icon else None)
        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send log embed: {e}")

    # -------------------------------------------------------------------------
    # Event Listener
    # -------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            await self.ensure_bot_has_role(member)
            await self.process_single_bot(member)

    # -------------------------------------------------------------------------
    # Background Task Loop (Sequentially updates 1 bot every 10s)
    # -------------------------------------------------------------------------
    @tasks.loop(seconds=10)
    async def sequential_bot_manager(self):
        """Monitors and processes 1 bot every 10 seconds."""
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot and member.id != self.bot.user.id:
                    target_role = guild.get_role(RESTRICTED_ROLE_ID)
                    
                    # 1. Grant missing role
                    if target_role and target_role not in member.roles:
                        updated_role = await self.ensure_bot_has_role(member)
                        if updated_role:
                            return

                    # 2. Synchronize nickname
                    is_excluded = await self.db.is_excluded(member.id)
                    if member.nick != self.target_nickname and not is_excluded:
                        updated_nick = await self.process_single_bot(member)
                        if updated_nick:
                            return

    @sequential_bot_manager.before_loop
    async def before_manager_loop(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------------------
    # Commands Suite
    # -------------------------------------------------------------------------
    @commands.hybrid_command(name="no-nickname", description="Resets a bot's nickname to default and saves an exclusion record.")
    @app_commands.describe(bot="Target bot to reset")
    @commands.has_permissions(ban_members=True)
    async def no_nickname(self, ctx: commands.Context, bot: discord.Member):
        """Resets a bot's nickname back to normal and records exclusion in database."""
        await ctx.defer(ephemeral=True)

        if not bot.bot:
            await ctx.send("❌ Target user must be a bot.", ephemeral=True)
            return

        if bot.id == self.bot.user.id:
            await ctx.send("❌ Cannot modify primary bot nickname.", ephemeral=True)
            return

        old_name = bot.display_name
        try:
            await self.db.add_exclusion(bot.id, ctx.author.id, reason=f"Requested by {ctx.author}")
            await bot.edit(nick=None, reason=f"Nickname reset requested by {ctx.author}")
            
            # Log event in Database & Channel
            await self.db.log_audit_event(
                bot_id=bot.id,
                guild_id=ctx.guild.id,
                action_type="ManualReset",
                old_val=old_name,
                new_val=bot.name,
                executor_id=ctx.author.id
            )
            await self.send_audit_embed(
                guild=ctx.guild,
                member=bot,
                old_name=old_name,
                action_type="Reset",
                role_given="None",
                role_removed="None",
                executor=ctx.author
            )

            embed = discord.Embed(
                title="✅ Nickname Reset & Exclusion Saved",
                description=f"Successfully reset nickname for {bot.mention} and saved persistent exclusion.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed, ephemeral=True)

        except discord.Forbidden:
            await ctx.send("❌ Missing permissions or hierarchy prevents modifying this bot.", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {e}", ephemeral=True)

    @commands.hybrid_group(name="botmanage", description="Admin management tools for server bots.")
    @commands.has_permissions(ban_members=True)
    async def botmanage(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Available subcommands: `lock-nickname`, `status`, `stats`, `audit-logs`.", ephemeral=True)

    @botmanage.command(name="lock-nickname", description="Removes exclusion for a bot and re-enables Starline Supporters naming.")
    @app_commands.describe(bot="Target bot to lock back into naming policy")
    async def lock_nickname(self, ctx: commands.Context, bot: discord.Member):
        await ctx.defer(ephemeral=True)

        if not bot.bot:
            await ctx.send("❌ Target must be a bot.", ephemeral=True)
            return

        old_name = bot.display_name
        await self.db.remove_exclusion(bot.id)
        await self.process_single_bot(bot)
        
        await self.db.log_audit_event(
            bot_id=bot.id,
            guild_id=ctx.guild.id,
            action_type="PolicyLocked",
            old_val=old_name,
            new_val=self.target_nickname,
            executor_id=ctx.author.id
        )
        await self.send_audit_embed(
            guild=ctx.guild,
            member=bot,
            old_name=old_name,
            action_type="Locked",
            role_given="None",
            role_removed="None",
            executor=ctx.author
        )

        embed = discord.Embed(
            title="🔒 Policy Re-applied",
            description=f"Exclusion removed for {bot.mention}. Naming policy is active again.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed, ephemeral=True)

    @botmanage.command(name="status", description="Displays all excluded bots.")
    async def status_list(self, ctx: commands.Context):
        await ctx.defer(ephemeral=True)

        exclusions = await self.db.get_all_exclusions()
        if not exclusions:
            await ctx.send("No bots are currently in the exclusion list.", ephemeral=True)
            return

        mentions = [f"• <@{bot_id}> (`{bot_id}`)" for bot_id in exclusions]
        embed = discord.Embed(
            title="📋 Excluded Bots Registry",
            description="\n".join(mentions),
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed, ephemeral=True)

    @botmanage.command(name="stats", description="Displays security stats and bot policy compliance overview.")
    async def bot_stats(self, ctx: commands.Context):
        await ctx.defer(ephemeral=True)

        guild = ctx.guild
        bots = [m for m in guild.members if m.bot and m.id != self.bot.user.id]
        total_bots = len(bots)
        
        target_role = guild.get_role(RESTRICTED_ROLE_ID)
        bots_with_role = sum(1 for b in bots if target_role and target_role in b.roles)
        bots_with_nickname = sum(1 for b in bots if b.nick == self.target_nickname)
        
        exclusions = await self.db.get_all_exclusions()
        excluded_count = sum(1 for b in bots if b.id in exclusions)

        embed = discord.Embed(
            title="📊 Bot Management Security Overview",
            color=discord.Color.blurple()
        )
        embed.add_field(name="🤖 Total Server Bots", value=f"**{total_bots}**", inline=True)
        embed.add_field(name="🏷️ Matching Nicknames", value=f"**{bots_with_nickname}/{total_bots}**", inline=True)
        embed.add_field(name="🛡️ Holding Role Policy", value=f"**{bots_with_role}/{total_bots}**", inline=True)
        embed.add_field(name="🚫 Excluded Bots", value=f"**{excluded_count}**", inline=True)
        embed.add_field(name="💾 SQLite Storage Path", value=f"`{DB_PATH}`", inline=False)

        await ctx.send(embed=embed, ephemeral=True)

    @botmanage.command(name="audit-logs", description="Browse historical management actions with dynamic pages.")
    async def audit_logs_command(self, ctx: commands.Context):
        await ctx.defer(ephemeral=True)

        history = await self.db.fetch_audit_history(limit=50)
        if not history:
            await ctx.send("No audit logs found in database.", ephemeral=True)
            return

        paginator = AuditLogPaginator(ctx.author.id, history)
        embed = paginator.build_embed()
        await ctx.send(embed=embed, view=paginator, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BotNameManager(bot))
