import re
import datetime
import discord
from discord.ext import commands

# Configuration
LOG_CHANNEL_ID = 917378312832180224

# Regex to detect discord server invites and general URLs
LINK_REGEX = re.compile(
    r"(https?://)?(www\.)?(discord\.(gg|io|me|li)|discordapp\.com/invite|[a-zA-Z0-9-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)

# Mute duration ladder in minutes: 1st mute (10m), 2nd mute (20m), 3rd mute (40m)
MUTE_DURATIONS = [10, 20, 40]


class LinkFilter(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-memory store for link violation counts: {user_id: count}
        self.violations = {}

    def is_immune(self, member: discord.Member) -> bool:
        """Check if user has Ban Members or Administrator permissions."""
        return (
            member.guild_permissions.ban_members
            or member.guild_permissions.administrator
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages or direct messages
        if message.author.bot or not message.guild:
            return

        # Check if message contains a link
        if LINK_REGEX.search(message.content):
            member = message.author

            # Skip if user is immune (has ban members permission or admin)
            if self.is_immune(member):
                return

            # 1. Delete the violating message instantly
            try:
                await message.delete()
            except discord.Forbidden:
                print(
                    "Error: Bot lacks 'Manage Messages' permission to delete the link."
                )
            except discord.NotFound:
                pass

            # 2. Track violation count
            user_id = member.id
            self.violations[user_id] = self.violations.get(user_id, 0) + 1
            attempts = self.violations[user_id]

            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)

            # 3. Handle Warning & Punishment Escalation
            if attempts < 3:
                # Send warning message in channel
                warn_embed = discord.Embed(
                    title="⚠️ Link Protection Warning",
                    description=f"{member.mention} you are not allowed to share links here!",
                    color=discord.Color.gold(),
                )
                warn_embed.add_field(
                    name="Warning Count",
                    value=f"**{attempts}/3** warnings before punishment.",
                    inline=False,
                )
                warn_embed.set_footer(
                    text="Private Server Anti-Link System",
                    icon_url=member.display_avatar.url,
                )
                await message.channel.send(embed=warn_embed, delete_after=10)

            elif 3 <= attempts <= 5:
                # Mute Escalation Logic (Attempt 3 -> 10m, Attempt 4 -> 20m, Attempt 5 -> 40m)
                mute_index = attempts - 3
                duration_minutes = MUTE_DURATIONS[mute_index]
                reason = f"Sending links in server ({attempts} attempts)"

                try:
                    # Timeout (Mute) user using Discord's built-in timeout feature
                    timeout_until = discord.utils.utcnow() + datetime.timedelta(
                        minutes=duration_minutes
                    )
                    await member.timeout(timeout_until, reason=reason)

                    # Send warning in chat
                    warn_embed = discord.Embed(
                        title="🔇 Member Muted",
                        description=f"{member.mention} has been muted for **{duration_minutes} minutes** for repeated link sharing.",
                        color=discord.Color.orange(),
                    )
                    await message.channel.send(
                        embed=warn_embed, delete_after=10
                    )

                    # Log Embed to LOG_CHANNEL_ID
                    if log_channel:
                        log_embed = discord.Embed(
                            title="LINK SENDING",
                            description=f"{member.mention} got mute of **{duration_minutes} mins** for {reason}",
                            color=discord.Color.orange(),
                            timestamp=discord.utils.utcnow(),
                        )
                        log_embed.add_field(
                            name="User ID", value=f"`{member.id}`", inline=True
                        )
                        log_embed.add_field(
                            name="Violations",
                            value=f"**{attempts}** times",
                            inline=True,
                        )
                        log_embed.set_thumbnail(
                            url=member.display_avatar.url
                        )
                        await log_channel.send(embed=log_embed)

                except discord.Forbidden:
                    print("Error: Bot lacks 'Moderate Members' permission.")

            else:
                # Ban User (Attempt 6+)
                reason = f"Sending links in server ({attempts} attempts)"

                try:
                    await member.ban(reason=reason, delete_message_days=0)

                    # Log Embed to LOG_CHANNEL_ID
                    if log_channel:
                        log_embed = discord.Embed(
                            title="LINK SEND",
                            description=f"{member.mention} got banned for {reason}",
                            color=discord.Color.red(),
                            timestamp=discord.utils.utcnow(),
                        )
                        log_embed.add_field(
                            name="User ID", value=f"`{member.id}`", inline=True
                        )
                        log_embed.add_field(
                            name="Total Violations",
                            value=f"**{attempts}** times",
                            inline=True,
                        )
                        log_embed.set_thumbnail(
                            url=member.display_avatar.url
                        )
                        await log_channel.send(embed=log_embed)

                    # Reset user violations after ban
                    self.violations.pop(user_id, None)

                except discord.Forbidden:
                    print("Error: Bot lacks 'Ban Members' permission.")


async def setup(bot: commands.Bot):
    await bot.add_cog(LinkFilter(bot))

