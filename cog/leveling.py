import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import aiohttp
import io
import math
import time
import logging
from typing import Optional, List, Dict, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageOps

# Setup dedicated logger
logger = logging.getLogger("LevelingSystem")
logger.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# Configuration Constants
# -----------------------------------------------------------------------------
ANNOUNCEMENT_CHANNEL_ID: int = 922420213176205322
DB_FILE: str = "level.db"
MAX_LEVEL: int = 100

# Milestone Role IDs Mapping
ROLE_REWARDS: Dict[int, int] = {
    1: 1536272004368302150,
    5: 1536272268789809214,
    10: 1536272415687057509,
    15: 1536272596914413639,
    25: 1536272818084380692,
    30: 1536272932299346030,
    40: 1536273203716956201,
    50: 1536273336177397901,
    75: 1536273559851241503,
    100: 1536273727380131840,
}

# -----------------------------------------------------------------------------
# Progression Formula Calculations
# Level 1 = 300 XP. Each subsequent level requires +200 XP.
# Formula: Total XP = 100 * Level^2 + 200 * Level
# -----------------------------------------------------------------------------
class LevelCalculator:
    @staticmethod
    def get_xp_for_level(level: int) -> int:
        if level <= 0:
            return 0
        if level > MAX_LEVEL:
            level = MAX_LEVEL
        return 100 * (level ** 2) + 200 * level

    @staticmethod
    def get_level_from_xp(xp: int) -> Tuple[int, int, int]:
        if xp <= 0:
            return 0, 0, 300

        # Quadratic Solution for 100*L^2 + 200*L - XP = 0
        discriminant = 40000 + 400 * xp
        level = int((-200 + math.sqrt(discriminant)) / 200)

        if level >= MAX_LEVEL:
            level = MAX_LEVEL
            base_xp = LevelCalculator.get_xp_for_level(MAX_LEVEL)
            return MAX_LEVEL, xp - base_xp, 0

        base_xp = LevelCalculator.get_xp_for_level(level)
        next_xp = LevelCalculator.get_xp_for_level(level + 1)

        xp_in_level = xp - base_xp
        needed_xp = next_xp - base_xp

        return level, xp_in_level, needed_xp


# -----------------------------------------------------------------------------
# Database Connection Manager
# -----------------------------------------------------------------------------
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 0,
                    last_msg_time REAL DEFAULT 0,
                    messages_sent INTEGER DEFAULT 0
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_xp ON users(xp DESC);")
            await db.commit()

    async def get_user(self, user_id: int) -> Optional[Tuple[int, int, float, int]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT xp, level, last_msg_time, messages_sent FROM users WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone()

    async def add_single_xp(self, user_id: int, now: float) -> Tuple[int, int, int]:
        """Adds exactly 1 XP per message to user record and calculates updated status."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            current_xp = (row[0] if row else 0) + 1  # Exactly 1 XP added
            old_level = row[1] if row else 0

            new_level, _, _ = LevelCalculator.get_level_from_xp(current_xp)

            await db.execute("""
                INSERT INTO users (user_id, xp, level, last_msg_time, messages_sent)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    xp = xp + 1,
                    level = ?,
                    last_msg_time = ?,
                    messages_sent = messages_sent + 1
            """, (user_id, current_xp, new_level, now, new_level, now))
            await db.commit()

            return old_level, new_level, current_xp

    async def get_leaderboard(self) -> List[Tuple[int, int, int]]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT user_id, xp, level FROM users ORDER BY xp DESC") as cursor:
                return await cursor.fetchall()

    async def modify_user_xp(self, user_id: int, xp_amount: int, set_mode: bool = False) -> Tuple[int, int, int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            current_xp = row[0] if row else 0
            old_level = row[1] if row else 0

            final_xp = max(0, xp_amount) if set_mode else max(0, current_xp + xp_amount)
            new_level, _, _ = LevelCalculator.get_level_from_xp(final_xp)

            await db.execute("""
                INSERT INTO users (user_id, xp, level) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET xp = ?, level = ?
            """, (user_id, final_xp, new_level, final_xp, new_level))
            await db.commit()

            return old_level, new_level, final_xp


# -----------------------------------------------------------------------------
# PIL Image Renderer
# -----------------------------------------------------------------------------
class RenderEngine:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def _fetch_avatar(self, avatar_url: str) -> Image.Image:
        try:
            async with self.session.get(avatar_url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    img = Image.open(io.BytesIO(data)).convert("RGBA")
                else:
                    img = Image.new("RGBA", (200, 200), (40, 44, 52, 255))
        except Exception:
            img = Image.new("RGBA", (200, 200), (40, 44, 52, 255))

        size = (400, 400)
        img = img.resize(size, Image.Resampling.LANCZOS)
        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size[0], size[1]), fill=255)

        output = ImageOps.fit(img, size, centering=(0.5, 0.5))
        output.putalpha(mask)
        return output

    async def render_rank_card(self, member: discord.Member, rank: int, level: int, current_xp: int, needed_xp: int) -> io.BytesIO:
        W, H = 1000, 320
        base = Image.new("RGBA", (W, H), (15, 16, 22, 255))
        draw = ImageDraw.Draw(base)

        draw.rounded_rectangle([20, 20, W - 20, H - 20], radius=25, fill=(26, 28, 38, 220), outline=(50, 54, 76, 255), width=2)

        avatar = await self._fetch_avatar(member.display_avatar.with_format("png").url)
        avatar = avatar.resize((200, 200), Image.Resampling.LANCZOS)
        base.paste(avatar, (50, 60), avatar)

        draw.ellipse((46, 56, 254, 264), outline=(88, 101, 242, 255), width=4)

        try:
            font_title = ImageFont.truetype("arial.ttf", 40)
            font_sub = ImageFont.truetype("arial.ttf", 28)
            font_small = ImageFont.truetype("arial.ttf", 22)
        except OSError:
            font_title = font_sub = font_small = ImageFont.load_default()

        username = member.display_name[:16]
        draw.text((280, 65), username, fill=(255, 255, 255, 255), font=font_title)

        draw.text((700, 65), f"RANK #{rank}", fill=(160, 170, 200, 255), font=font_sub)
        draw.text((850, 65), f"LEVEL {level}", fill=(88, 101, 242, 255), font=font_sub)

        bar_x, bar_y, bar_w, bar_h = 280, 180, 670, 36
        draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=18, fill=(40, 44, 60, 255))

        progress = min(1.0, current_xp / max(1, needed_xp)) if level < MAX_LEVEL else 1.0
        fill_w = int(bar_w * progress)

        if fill_w > 0:
            draw.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], radius=18, fill=(88, 101, 242, 255))

        xp_text = f"{current_xp:,} / {needed_xp:,} XP" if level < MAX_LEVEL else "MAX LEVEL REACHED"
        draw.text((bar_x + 15, bar_y - 32), xp_text, fill=(210, 220, 240, 255), font=font_small)

        buffer = io.BytesIO()
        base.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    async def render_levelup_card(self, member: discord.Member, old_level: int, new_level: int) -> io.BytesIO:
        W, H = 850, 260
        base = Image.new("RGBA", (W, H), (18, 18, 26, 255))
        draw = ImageDraw.Draw(base)

        draw.rounded_rectangle([12, 12, W - 12, H - 12], radius=22, fill=(28, 30, 42, 255), outline=(88, 101, 242, 255), width=3)

        avatar = await self._fetch_avatar(member.display_avatar.with_format("png").url)
        avatar = avatar.resize((160, 160), Image.Resampling.LANCZOS)
        base.paste(avatar, (40, 50), avatar)

        try:
            font_large = ImageFont.truetype("arial.ttf", 38)
            font_mid = ImageFont.truetype("arial.ttf", 28)
        except OSError:
            font_large = font_mid = ImageFont.load_default()

        draw.text((230, 50), "LEVEL UPGRADE!", fill=(255, 215, 0, 255), font=font_large)
        draw.text((230, 105), f"Great job, {member.display_name}!", fill=(255, 255, 255, 255), font=font_mid)

        progression_str = f"Old Rank: {old_level}  •  New Rank: {new_level}"
        draw.text((230, 155), progression_str, fill=(114, 137, 218, 255), font=font_mid)

        buffer = io.BytesIO()
        base.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer


# -----------------------------------------------------------------------------
# Paginated Leaderboard UI
# -----------------------------------------------------------------------------
class DynamicLeaderboardView(discord.ui.View):
    def __init__(self, bot: commands.Bot, author_id: int, records: List[Tuple[int, int, int]]):
        super().__init__(timeout=180)
        self.bot = bot
        self.author_id = author_id
        self.records = records
        self.per_page = 10
        self.current_page = 1
        self.total_pages = max(1, math.ceil(len(self.records) / self.per_page))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the command invoker can control pages.", ephemeral=True)
            return False
        return True

    def generate_embed(self, guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(title="🏆 Server Ranking Leaderboard", color=discord.Color.blurple())

        start_index = (self.current_page - 1) * self.per_page
        end_index = start_index + self.per_page
        current_batch = self.records[start_index:end_index]

        field_content = []
        for index, (user_id, xp, level) in enumerate(current_batch, start=start_index + 1):
            member = guild.get_member(user_id)
            user_str = member.mention if member else f"<@{user_id}>"

            role_tag = "No Role"
            for lvl_req in sorted(ROLE_REWARDS.keys(), reverse=True):
                if level >= lvl_req:
                    role_tag = f"<@&{ROLE_REWARDS[lvl_req]}>"
                    break

            field_content.append(f"**{index}.** {user_str} = Level {level} = {role_tag}")

        embed.description = "\n".join(field_content) if field_content else "No ranked users available."
        embed.set_footer(text=f"Page: {self.current_page}/{self.total_pages}")
        return embed

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 1
        await interaction.response.edit_message(embed=self.generate_embed(interaction.guild), view=self)

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1:
            self.current_page -= 1
        await interaction.response.edit_message(embed=self.generate_embed(interaction.guild), view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages:
            self.current_page += 1
        await interaction.response.edit_message(embed=self.generate_embed(interaction.guild), view=self)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = self.total_pages
        await interaction.response.edit_message(embed=self.generate_embed(interaction.guild), view=self)


# -----------------------------------------------------------------------------
# Main System Extension Cog
# -----------------------------------------------------------------------------
class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = DatabaseManager(DB_FILE)
        self.session = aiohttp.ClientSession()
        self.renderer = RenderEngine(self.session)

    async def cog_load(self):
        await self.db.initialize()
        logger.info("Leveling Engine loaded (1 msg = 1 XP mode enabled).")

    async def cog_unload(self):
        await self.session.close()

    async def sync_roles_for_member(self, member: discord.Member, level: int):
        for lvl_req, role_id in ROLE_REWARDS.items():
            if level >= lvl_req:
                role = member.guild.get_role(role_id)
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"Level {lvl_req} Auto Assignment")
                    except discord.Forbidden:
                        logger.warning(f"Insufficient permissions to assign role {role_id}")

    # -------------------------------------------------------------------------
    # Core Event: Every Message Grants 1 XP
    # -------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        user_id = message.author.id
        now = time.time()

        # Add 1 XP per message instantly with no cooldown
        old_level, new_level, _ = await self.db.add_single_xp(user_id, now)

        # Trigger Level-Up sequence when level increases
        if new_level > old_level:
            if isinstance(message.author, discord.Member):
                await self.sync_roles_for_member(message.author, new_level)

            announcement_channel = self.bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
            if announcement_channel:
                card_buffer = await self.renderer.render_levelup_card(message.author, old_level, new_level)
                file = discord.File(fp=card_buffer, filename="levelup.png")
                await announcement_channel.send(
                    content=f"🎉 {message.author.mention} reached level **{new_level}**!",
                    file=file
                )

    # -------------------------------------------------------------------------
    # Commands Section
    # -------------------------------------------------------------------------
    @commands.hybrid_command(name="rank", description="Generates member rank card.")
    @app_commands.describe(member="Member to view level progress for")
    async def rank(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        await ctx.defer()
        target = member or ctx.author

        user_data = await self.db.get_user(target.id)
        user_xp = user_data[0] if user_data else 0

        leaderboard = await self.db.get_leaderboard()
        rank_pos = next((i for i, record in enumerate(leaderboard, start=1) if record[0] == target.id), len(leaderboard) + 1)

        level, current_level_xp, needed_xp = LevelCalculator.get_level_from_xp(user_xp)

        img_buffer = await self.renderer.render_rank_card(target, rank_pos, level, current_level_xp, needed_xp)
        file = discord.File(fp=img_buffer, filename="rank.png")
        await ctx.send(file=file)

    @commands.hybrid_command(name="leaderboard", description="View server ranking leaderboard with dynamic pages.")
    async def leaderboard(self, ctx: commands.Context):
        await ctx.defer()
        records = await self.db.get_leaderboard()

        if not records:
            await ctx.send("Leaderboard is currently empty.")
            return

        view = DynamicLeaderboardView(self.bot, ctx.author.id, records)
        embed = view.generate_embed(ctx.guild)
        await ctx.send(embed=embed, view=view)

    # -------------------------------------------------------------------------
    # Admin Controls
    # -------------------------------------------------------------------------
    @commands.hybrid_group(name="leveladmin", description="Administrative control for user leveling progression.")
    @commands.has_permissions(administrator=True)
    async def leveladmin(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Invalid subcommand. Use: `addxp`, `setxp`, `reset`", ephemeral=True)

    @leveladmin.command(name="addxp", description="Grants XP to a target user.")
    async def add_xp(self, ctx: commands.Context, member: discord.Member, amount: int):
        await ctx.defer(ephemeral=True)
        _, new_lvl, total = await self.db.modify_user_xp(member.id, amount, set_mode=False)
        await self.sync_roles_for_member(member, new_lvl)
        await ctx.send(f"✅ Added **{amount:,} XP** to {member.mention}. Total XP: **{total:,}** (Level {new_lvl})")

    @leveladmin.command(name="setxp", description="Sets a user's exact XP value.")
    async def set_xp(self, ctx: commands.Context, member: discord.Member, amount: int):
        await ctx.defer(ephemeral=True)
        _, new_lvl, total = await self.db.modify_user_xp(member.id, amount, set_mode=True)
        await self.sync_roles_for_member(member, new_lvl)
        await ctx.send(f"✅ Updated {member.mention}'s XP to **{total:,}** (Level {new_lvl})")

    @leveladmin.command(name="reset", description="Resets a member's level progression.")
    async def reset_user(self, ctx: commands.Context, member: discord.Member):
        await ctx.defer(ephemeral=True)
        await self.db.modify_user_xp(member.id, 0, set_mode=True)
        await ctx.send(f"⚠️ Successfully reset leveling data for {member.mention}.")

async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))
          
