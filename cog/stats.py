import os
import platform
import time
import datetime
import psutil
import discord
from discord import app_commands
from discord.ext import commands

# Start timestamp for uptime tracking
BOT_START_TIME = time.time()
DEV_USER_ID = 1472526217277079633
ALLOWED_GUILD_ID = 903668276062724207


def create_progress_bar(percent: float, length: int = 10) -> str:
    """Generates a visual progress bar string for CPU/RAM usage."""
    filled_length = int(length * percent // 100)
    bar = "█" * filled_length + "░" * (length - filled_length)
    return f"`[{bar}]` **{percent:.1f}%**"


class StatsView(discord.ui.View):
    """Interactive view for refreshing stats or viewing extra metadata."""

    def __init__(self, cog, interaction: discord.Interaction):
        super().__init__(timeout=120)
        self.cog = cog
        self.original_interaction = interaction

    @discord.ui.button(
        label="Refresh",
        style=discord.ButtonStyle.primary,
        emoji="🔄",
        custom_id="refresh_stats",
    )
    async def refresh_callback(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        embed = await self.cog.generate_stats_embed()
        await interaction.response.edit_message(embed=embed, view=self)


class Stats(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def get_uptime(self) -> str:
        """Calculates readable uptime duration from start time."""
        delta = datetime.timedelta(seconds=int(time.time() - BOT_START_TIME))
        days, remainder = divmod(delta.seconds, 3600)
        hours, minutes = divmod(remainder, 60)
        if delta.days > 0:
            return f"{delta.days}d {days}h {minutes}m"
        return f"{hours}h {minutes}m {delta.seconds % 60}s"

    async def generate_stats_embed(self) -> discord.Embed:
        """Constructs the advanced stats embed."""
        # System Resource Usage
        ram = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=None)
        disk = psutil.disk_usage("/")

        # Process Specific Metrics
        process = psutil.Process(os.getpid())
        bot_ram_mb = process.memory_info().rss / (1024 * 1024)

        # Network Traffic
        net = psutil.net_io_counters()
        net_recv = net.bytes_recv / (1024**2)
        net_sent = net.bytes_sent / (1024**2)

        # Discord Metrics
        guild = self.bot.get_guild(ALLOWED_GUILD_ID)
        member_count = guild.member_count if guild else 0
        ping = round(self.bot.latency * 1000)

        # Bot User Details
        bot_user = self.bot.user
        member_in_guild = guild.get_member(bot_user.id) if guild else None
        nickname = (
            member_in_guild.nick
            if member_in_guild and member_in_guild.nick
            else f"{bot_user.name}"
        )

        embed = discord.Embed(
            title="⚡ STARLINE CAFE — SYSTEM CORE METRICS",
            description=(
                f"```ansi\n"
                f"\u001b[1;34m[SYSTEM STATUS]\u001b[0m Operational\n"
                f"\u001b[1;32m[GATEWAY PING]\u001b[0m {ping} ms\n"
                f"\u001b[1;33m[BOT UPTIME]\u001b[0m {self.get_uptime()}\n"
                f"```"
            ),
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=discord.utils.utcnow(),
        )

        # 1. Hardware & Hosting Environment
        ram_bar = create_progress_bar(ram.percent)
        cpu_bar = create_progress_bar(cpu_percent)

        embed.add_field(
            name="💻 Hosting & Hardware",
            value=(
                f"**CPU Load:** {cpu_bar}\n"
                f"**RAM Usage:** {ram_bar}\n"
                f"**Process RAM:** `{bot_ram_mb:.1f} MB`\n"
                f"**Storage:** `{disk.used / (1024**3):.1f} / {disk.total / (1024**3):.1f} GB`\n"
                f"**Net I/O:** `⬇️ {net_recv:.1f} MB | ⬆️ {net_sent:.1f} MB`"
            ),
            inline=False,
        )

        # 2. Environment & Dependencies
        embed.add_field(
            name="⚙️ Environment Specs",
            value=(
                f"**Language:** `Python {platform.python_version()}`\n"
                f"**Library:** `discord.py v{discord.__version__}`\n"
                f"**OS Platform:** `{platform.system()} {platform.release()}`\n"
                f"**Architecture:** `{platform.machine()}`"
            ),
            inline=True,
        )

        # 3. Discord & Guild Scope
        embed.add_field(
            name="🌐 Discord Scope",
            value=(
                f"**Server Type:** `Private Infrastructure`\n"
                f"**Guild ID:** `{ALLOWED_GUILD_ID}`\n"
                f"**Total Members:** `{member_count}`\n"
                f"**Activities Count:** `4 (Cycling 5s)`"
            ),
            inline=True,
        )

        # 4. Identity & Authorization
        embed.add_field(
            name="🤖 Bot Identity",
            value=(
                f"**Username:** `{bot_user.name}`\n"
                f"**Nickname:** `{nickname}`\n"
                f"**Intents Status:** `Guilds, Members, Messages (3/3)`\n"
                f"**Developer / Creator:** <@{DEV_USER_ID}>"
            ),
            inline=False,
        )

        embed.set_thumbnail(url=bot_user.display_avatar.url)
        embed.set_footer(
            text="Starline Café Core Diagnostics • Live Dashboard",
            icon_url=guild.icon.url if guild and guild.icon else None,
        )

        return embed

    @app_commands.command(
        name="stats", description="Displays detailed statistics about the bot."
    )
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed = await self.generate_stats_embed()
        view = StatsView(self, interaction)
        await interaction.followup.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot))
      
