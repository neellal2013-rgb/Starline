import asyncio
import logging
import os
import sys
from itertools import cycle
import discord
from discord.ext import commands, tasks

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("DiscordBot")

# ---------------------------------------------------------------------------
# Bot Configuration
# ---------------------------------------------------------------------------
ALLOWED_GUILD_ID = 903668276062724207

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,  # Disabled default help command for custom implementations
)

# ---------------------------------------------------------------------------
# Advanced Status Loop Setup
# ---------------------------------------------------------------------------
status_cycle = cycle(
    [
        lambda guild: discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{guild.member_count if guild else 0} members",
        ),
        lambda guild: discord.Activity(
            type=discord.ActivityType.listening, name="welcome events"
        ),
        lambda guild: discord.Streaming(
            name="Private Server Live", url="https://www.twitch.tv/directory"
        ),
        lambda guild: discord.Game(name="discord.py v2.x | /testwelcome"),
    ]
)


@tasks.loop(seconds=5)
async def rotate_status():
    """Rotates the bot's status every 5 seconds using server metadata."""
    guild = bot.get_guild(ALLOWED_GUILD_ID)
    current_activity_func = next(status_cycle)
    activity = current_activity_func(guild)

    await bot.change_presence(status=discord.Status.online, activity=activity)


@rotate_status.before_loop
async def before_status_loop():
    """Wait until the bot internal cache is ready before starting loop."""
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Bot Event Listeners
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info(f"Logged in successfully as {bot.user} (ID: {bot.user.id})")

    # Start presence loop if inactive
    if not rotate_status.is_running():
        rotate_status.start()
        logger.info("Started dynamic status rotation loop.")

    # Application Command Syncing
    try:
        synced = await bot.tree.sync()
        logger.info(
            f"Synced {len(synced)} application command(s) globally."
        )
    except Exception as e:
        logger.error(f"Failed to sync application commands: {e}")

    # Enforce private server policy on existing guilds
    for guild in bot.guilds:
        if guild.id != ALLOWED_GUILD_ID:
            logger.warning(
                f"Unauthorized server detected: {guild.name} ({guild.id}). Leaving..."
            )
            await guild.leave()


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Instantly leave any unauthorized guild invitations."""
    if guild.id != ALLOWED_GUILD_ID:
        logger.warning(
            f"Attempted invite to unauthorized server: {guild.name} ({guild.id}). Leaving..."
        )
        await guild.leave()


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    """Global handler for classic prefix commands."""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ You lack the required permissions to execute this command."
        )
    else:
        logger.error(f"Error in command '{ctx.command}': {error}")


# ---------------------------------------------------------------------------
# Developer Cog Management Commands (Admin Only)
# ---------------------------------------------------------------------------
@bot.command(name="reload")
@commands.is_owner()
async def reload_extension(ctx: commands.Context, extension: str):
    """Reloads a specific cog on the fly without restarting the bot."""
    try:
        await bot.reload_extension(f"cog.{extension}")
        await ctx.send(f"✅ Successfully reloaded extension `cog.{extension}`.")
        logger.info(f"Extension cog.{extension} reloaded by owner.")
    except Exception as e:
        await ctx.send(f"❌ Failed to reload `cog.{extension}`: ```py\n{e}\n```")


# ---------------------------------------------------------------------------
# Core Startup Initialization
# ---------------------------------------------------------------------------
async def load_all_cogs():
    """Dynamically locates and loads all cog modules inside the ./cog directory."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cog_dir = os.path.join(base_dir, "cog")

    if not os.path.exists(cog_dir):
        logger.error(
            f"Directory '{cog_dir}' does not exist. Create a 'cog' directory."
        )
        return

    for filename in os.listdir(cog_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            extension_name = f"cog.{filename[:-3]}"
            try:
                await bot.load_extension(extension_name)
                logger.info(f"Loaded extension: {extension_name}")
            except Exception as e:
                logger.error(
                    f"Failed to load extension {extension_name}: {e}"
                )


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical(
            "DISCORD_TOKEN environment variable missing. Halting startup."
        )
        raise ValueError("DISCORD_TOKEN environment variable missing!")

    async with bot:
        await load_all_cogs()
        await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot execution terminated by user.")
        
