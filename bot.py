import asyncio
import os
import discord
from discord.ext import commands

# 1. Configuration
ALLOWED_GUILD_ID = 903668276062724207

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in successfully as {bot.user} (ID: {bot.user.id})")

    # 2. Set Custom Bot Activity Status
    # You can change discord.ActivityType.watching to listening, playing, etc.
    activity = discord.Activity(
        type=discord.ActivityType.watching, name="over the server"
    )
    await bot.change_presence(
        status=discord.Status.online, activity=activity
    )

    # 3. Sync Slash Commands (bot.tree)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    # 4. Leave any unauthorized servers automatically
    for guild in bot.guilds:
        if guild.id != ALLOWED_GUILD_ID:
            print(
                f"Leaving unauthorized server: {guild.name} (ID: {guild.id})"
            )
            await guild.leave()


# 5. Instantly leave if invited to another server
@bot.event
async def on_guild_join(guild: discord.Guild):
    if guild.id != ALLOWED_GUILD_ID:
        print(f"Unauthorized invite to {guild.name}. Leaving immediately...")
        await guild.leave()


async def main():
    # Fetch token from environment variables (Railway)
    token = os.getenv("DISCORD_TOKEN")

    if not token:
        raise ValueError("DISCORD_TOKEN environment variable is missing!")

    async with bot:
        # Load all cog files from the 'cog' directory
        for filename in os.listdir("./cog"):
            if filename.endswith(".py"):
                cog_name = f"cog.{filename[:-3]}"
                await bot.load_extension(cog_name)
                print(f"Loaded cog: {cog_name}")

        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
  
