import discord
from discord.ext import commands
from discord import app_commands
from typing import Dict

# Mirroring your defined role milestones
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

class RankList(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="ranklist", description="Displays all level milestones and their unlockable roles.")
    async def ranklist(self, ctx: commands.Context):
        """Displays the list of levels and their associated rewards."""
        embed = discord.Embed(
            title="📜 Server Level Rewards List",
            description="Here are the level milestones and the roles you unlock as you progress:",
            color=discord.Color.from_rgb(114, 137, 218)
        )

        lines = []
        for level, role_id in sorted(ROLE_REWARDS.items()):
            lines.append(f"**Level {level}** = <@&{role_id}>")

        embed.add_field(
            name="Milestones",
            value="\n".join(lines) if lines else "No role rewards configured.",
            inline=False
        )

        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(RankList(bot))
  
