import discord
from discord import app_commands
from discord.ext import commands

WELCOME_CHANNEL_ID = 917378312832180224
BANNER_MESSAGE_ID = 1535639229370204231
ARROW_EMOJI = "<a:Arrow:1535641409074372628>"


class Welcome(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def send_welcome(self, member: discord.Member):
        channel = self.bot.get_channel(WELCOME_CHANNEL_ID)

        if channel:
            description = (
                f"THANK YOU FOR JOINING STARLINE CAFE
                f"{member.mention} thanks for joining server\n"
                f"visit these channels\n"
                f"{ARROW_EMOJI} Checkout - <#922323143928995911>\n"
                f"{ARROW_EMOJI} Checkout - <#917378312832180224>\n"
                f"{ARROW_EMOJI} Checkout - <#922880788305956865>\n"
                f"{ARROW_EMOJI} Checkout - <#917266635495198750>\n"
                f"{ARROW_EMOJI} Checkout - <#1536290430214471800>"
            )

            embed = discord.Embed(
                description=description, color=discord.Color.blue()
            )

            try:
                banner_msg = await channel.fetch_message(BANNER_MESSAGE_ID)

                # 1. Attachment (Direct image/GIF upload)
                if banner_msg.attachments:
                    embed.set_image(url=banner_msg.attachments[0].url)

                # 2. Embed (Tenor / Giphy link preview)
                elif banner_msg.embeds:
                    if banner_msg.embeds[0].image:
                        embed.set_image(url=banner_msg.embeds[0].image.url)
                    elif banner_msg.embeds[0].thumbnail:
                        embed.set_image(url=banner_msg.embeds[0].thumbnail.url)

                # 3. Raw URL link inside message text
                elif banner_msg.content.startswith("http"):
                    embed.set_image(url=banner_msg.content.strip())

            except discord.NotFound:
                print(
                    "Error: Target banner message was not found or was deleted."
                )
            except Exception as e:
                print(f"Error fetching banner image: {e}")

            await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.send_welcome(member)

    # Slash command using bot.tree via app_commands
    @app_commands.command(
        name="testwelcome",
        description="Test the welcome embed in the welcome channel.",
    )
    async def testwelcome(self, interaction: discord.Interaction):
        await self.send_welcome(interaction.user)
        await interaction.response.send_message(
            "Welcome embed test sent!", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Welcome(bot))
  
