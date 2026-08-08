import time
import datetime
import discord
from discord import app_commands
from discord.ext import commands

# Custom Emoji Constants
EMOJI_TICK = "<a:Tick:1535660962684870798>"
EMOJI_CROSS = "<:Cross:1535661936434479124>"
EMOJI_ARROW = "<a:Arrow:1535641409074372628>"


def format_duration(seconds: int) -> str:
    """Formats duration into human readable text."""
    if seconds < 60:
        return f"{seconds}s"
    delta = datetime.timedelta(seconds=seconds)
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if delta.days > 0:
        return f"{delta.days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs}s"


class AFK(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Format: {user_id: {"reason": str, "timestamp": float, "original_nick": str}}
        self.afk_users = {}

    async def update_nickname_afk(
        self, member: discord.Member, set_afk: bool
    ) -> None:
        """Safely updates member nickname with [AFK] prefix."""
        if not member.guild.me.guild_permissions.manage_nicknames:
            return
        if member.id == member.guild.owner_id:
            return  # Server owners cannot have their nickname changed by bots

        try:
            current_nick = member.display_name
            if set_afk:
                if not current_nick.startswith("[AFK]"):
                    new_nick = f"[AFK] {current_nick}"[:32]  # Max 32 chars
                    await member.edit(nick=new_nick)
            else:
                if member.id in self.afk_users:
                    orig_nick = self.afk_users[member.id].get("original_nick")
                    await member.edit(nick=orig_nick)
                elif current_nick.startswith("[AFK]"):
                    clean_nick = current_nick.replace("[AFK] ", "").replace(
                        "[AFK]", ""
                    )
                    await member.edit(
                        nick=clean_nick if clean_nick != member.name else None
                    )
        except (discord.Forbidden, discord.HTTPException):
            pass

    # Slash Command Implementation
    @app_commands.command(
        name="afk", description="Set your AFK status with a custom reason."
    )
    @app_commands.describe(reason="Reason for going AFK")
    async def afk_slash(
        self, interaction: discord.Interaction, reason: str = None
    ):
        if not reason or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_CROSS} AFK MISSING ARGUMENTS",
                description=(
                    f"You must provide a reason for going AFK!\n\n"
                    f"**Use:**\n"
                    f"{EMOJI_ARROW} `/afk reason:`"
                ),
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        member = interaction.user
        original_nick = member.nick

        # Save AFK state
        self.afk_users[member.id] = {
            "reason": reason,
            "timestamp": time.time(),
            "original_nick": original_nick,
        }

        # Attempt Nickname Change
        await self.update_nickname_afk(member, set_afk=True)

        embed = discord.Embed(
            title=f"{EMOJI_TICK} AFK SET",
            description=f"{member.mention} have been set afk for **{reason}**",
            color=discord.Color.brand_green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(
            text="Send a message anytime to remove AFK status",
            icon_url=member.display_avatar.url,
        )

        await interaction.response.send_message(embed=embed)

    # Hybrid Prefix Command Handler for Missing Arguments Support
    @commands.command(name="afk")
    async def afk_prefix(self, ctx: commands.Context, *, reason: str = None):
        if not reason or not reason.strip():
            embed = discord.Embed(
                title=f"{EMOJI_CROSS} AFK MISSING ARGUMENTS",
                description=(
                    f"You must provide a reason for going AFK!\n\n"
                    f"**Use:**\n"
                    f"{EMOJI_ARROW} `/afk reason:`"
                ),
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        member = ctx.author
        original_nick = member.nick

        self.afk_users[member.id] = {
            "reason": reason,
            "timestamp": time.time(),
            "original_nick": original_nick,
        }

        await self.update_nickname_afk(member, set_afk=True)

        embed = discord.Embed(
            title=f"{EMOJI_TICK} AFK SET",
            description=f"{member.mention} have been set afk for **{reason}**",
            color=discord.Color.brand_green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(
            text="Send a message anytime to remove AFK status",
            icon_url=member.display_avatar.url,
        )

        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        # 1. Remove AFK status when the AFK user types a message
        if message.author.id in self.afk_users:
            afk_data = self.afk_users[message.author.id]
            elapsed_seconds = int(time.time() - afk_data["timestamp"])
            duration_str = format_duration(elapsed_seconds)

            # Restore original nickname
            await self.update_nickname_afk(message.author, set_afk=False)

            # Remove from AFK registry
            del self.afk_users[message.author.id]

            embed = discord.Embed(
                description=f"{message.author.mention} you're set back to normal you were afk since **{duration_str}**",
                color=discord.Color.blurple(),
            )
            await message.channel.send(embed=embed, delete_after=10)

        # 2. Check if message pings/mentions an AFK user
        if message.mentions:
            for mentioned_user in message.mentions:
                if (
                    mentioned_user.id in self.afk_users
                    and mentioned_user.id != message.author.id
                ):
                    afk_data = self.afk_users[mentioned_user.id]
                    elapsed_seconds = int(time.time() - afk_data["timestamp"])
                    duration_str = format_duration(elapsed_seconds)
                    reason = afk_data["reason"]

                    embed = discord.Embed(
                        description=f"{mentioned_user.mention} the user is afk for **{reason}** since **{duration_str}**",
                        color=discord.Color.gold(),
                    )
                    embed.set_thumbnail(url=mentioned_user.display_avatar.url)
                    await message.channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(AFK(bot))
      
