import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from typing import Optional

# Dedicated logger setup
logger = logging.getLogger("StarlineSupporters")
logger.setLevel(logging.INFO)

# Configuration
LOG_CHANNEL_ID: int = 1535655458013052959
TARGET_NICKNAME: str = "Starline Supporters"

class BotNameManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_nickname = TARGET_NICKNAME

    async def cog_load(self):
        """Starts the background monitoring task loop when cog loads."""
        self.sequential_bot_renamer.start()
        logger.info("BotNameManager cog loaded and sequential renamer loop started.")

    async def cog_unload(self):
        """Cancels background task loop when cog unloads."""
        self.sequential_bot_renamer.cancel()

    # -------------------------------------------------------------------------
    # Core Utility Methods
    # -------------------------------------------------------------------------
    async def process_bot_nickname(self, member: discord.Member) -> bool:
        """
        Checks and updates a single bot member's nickname to 'Starline Supporters'.
        Returns True if the nickname was successfully updated, False otherwise.
        """
        guild = member.guild

        # Ensure target is a bot and not our own bot
        if not member.bot or member.id == self.bot.user.id:
            return False

        # If current nickname is already the target, skip
        if member.nick == self.target_nickname:
            return False

        # Check bot permissions
        me = guild.me
        if not me.guild_permissions.manage_nicknames:
            logger.warning(f"Cannot edit {member.display_name}: Missing 'Manage Nicknames' permission in {guild.name}.")
            return False

        # Check role hierarchy (our highest role must be higher than target bot's top role)
        if me.top_role <= member.top_role:
            logger.warning(f"Cannot edit {member.display_name}: Role hierarchy restriction in {guild.name}.")
            return False

        try:
            old_name = member.display_name
            await member.edit(nick=self.target_nickname, reason="Automated Starline Supporters Nickname Policy")
            
            # Send notification embed log
            await self.send_nickname_log(guild, member, old_name)
            return True

        except discord.Forbidden:
            logger.error(f"Forbidden: Unable to change nickname for bot {member} in {guild.name}.")
        except discord.HTTPException as e:
            logger.error(f"HTTP Error changing nickname for bot {member}: {e}")
        
        return False

    async def send_nickname_log(self, guild: discord.Guild, member: discord.Member, old_name: str):
        """Sends a rich, modern embed logging the nickname modification."""
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            return

        embed = discord.Embed(
            title="✨ Bot Nickname Updated",
            description=f"{member.mention} has been renamed to **`{self.target_nickname}`**.",
            color=discord.Color.from_rgb(88, 101, 242)
        )
        
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="🤖 Bot User", value=f"{member.name} (`{member.id}`)", inline=True)
        embed.add_field(name="🏷️ Previous Name", value=f"`{old_name}`", inline=True)
        embed.add_field(name="⚡ New Nickname", value=f"`{self.target_nickname}`", inline=True)
        embed.set_footer(text=f"Server: {guild.name}", icon_url=guild.icon.url if guild.icon else None)
        embed.timestamp = discord.utils.utcnow()

        try:
            await log_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send log embed to channel {LOG_CHANNEL_ID}: {e}")

    # -------------------------------------------------------------------------
    # Sequential Background Task: Runs every 10 seconds for 1 bot
    # -------------------------------------------------------------------------
    @tasks.loop(seconds=10)
    async def sequential_bot_renamer(self):
        """Finds ONE bot whose name needs updating, changes it, and waits 10s for the next."""
        for guild in self.bot.guilds:
            for member in guild.members:
                # Target other bots whose nickname is NOT yet 'Starline Supporters'
                if member.bot and member.id != self.bot.user.id:
                    if member.nick != self.target_nickname:
                        updated = await self.process_bot_nickname(member)
                        if updated:
                            # Stop execution for this 10-second interval once 1 bot is updated
                            return

    @sequential_bot_renamer.before_loop
    async def before_renamer_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(BotNameManager(bot))
          
