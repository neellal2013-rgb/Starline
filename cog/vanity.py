import discord
from discord.ext import commands

VANITY_URL = "https://discord.gg/KStacWPft"


class Vanity(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore messages sent by bots or outside of a server channel
        if message.author.bot or not message.guild:
            return

        # Check if the message content stripped of whitespace is exactly "vanity"
        if message.content.strip().lower() == "vanity":
            embed = discord.Embed(
                title="✨ Server Vanity Link",
                description=f"This is the vanity:\n{VANITY_URL}",
                color=discord.Color.from_rgb(88, 101, 242),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_thumbnail(
                url=message.guild.icon.url if message.guild.icon else None
            )
            embed.set_footer(
                text=f"Requested by {message.author.display_name}",
                icon_url=message.author.display_avatar.url,
            )

            await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Vanity(bot))
  
