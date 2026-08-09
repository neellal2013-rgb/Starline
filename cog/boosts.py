import logging
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Dict, List, Any

# Logging Configuration
logger = logging.getLogger("BoostLogger")
logger.setLevel(logging.INFO)

# Configuration Constants
CHANNEL_ID = 1536110137859768350
ROLE_ID = 1532025223069827303

# Aesthetic Color Palette
EMBED_COLOR_BOOST = 0xF47FFF    # Discord Nitro Pink
EMBED_COLOR_UNBOOST = 0x2F3136  # Dark Neutral Gray
EMBED_COLOR_INFO = 0x5865F2     # Blurple

# Nitro Assets
NITRO_ICON_URL = "https://cdn.discordapp.com/emojis/1032338102603816990.gif"


class BoosterPaginatorView(discord.ui.View):
    """Interactive paginator view for stepping through booster records."""

    def __init__(self, embeds: List[discord.Embed], author_id: int):
        super().__init__(timeout=180)
        self.embeds = embeds
        self.author_id = author_id
        self.current_page = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You cannot interact with this menu.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self.update_page(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self.update_page(interaction)

    async def update_page(self, interaction: discord.Interaction):
        # Update button state
        self.children[0].disabled = (self.current_page == 0)
        self.children[1].disabled = (self.current_page == len(self.embeds) - 1)

        await interaction.response.edit_message(
            embed=self.embeds[self.current_page],
            view=self
        )


class Boosts(commands.Cog):
    """Advanced Server Boost Management Cog with analytics, role management, and tracking."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # In-memory dictionary to track past booster user IDs mapped to their user info dicts
        self.past_boosters_data: Dict[int, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # HELPER UTILITIES
    # ------------------------------------------------------------------

    def create_progress_bar(self, count: int, target: int = 14) -> str:
        """Generates a visual ASCII-style progress bar for server boost progression."""
        if target <= 0:
            target = 1
        percentage = min(count / target, 1.0)
        filled_blocks = int(percentage * 10)
        empty_blocks = 10 - filled_blocks
        return f"`[{'▰' * filled_blocks}{'▱' * empty_blocks}]` ({count}/{target})"

    def format_member_roles(self, member: discord.Member) -> str:
        """Formats non-everyone roles assigned to a member."""
        roles = [r.mention for r in member.roles if not r.is_default()]
        if not roles:
            return "None"
        formatted = ", ".join(roles)
        return formatted if len(formatted) <= 250 else f"{formatted[:245]}..."

    def create_boost_announcement_embed(self, member: discord.Member, role: Optional[discord.Role]) -> discord.Embed:
        """Constructs an elaborate embed announcing a new server boost."""
        guild = member.guild
        role_mention = role.mention if role else "Reward Role"

        embed = discord.Embed(
            title="✨ Server Boost Detected!",
            description=(
                f"{member.mention} thanks boosting server you'll get {role_mention} role "
                f"and thanks for supporting the server we all will appreciate you!!"
            ),
            color=EMBED_COLOR_BOOST
        )

        embed.set_author(name=f"Boost Received from {member.display_name}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(
            name="👤 Supporter",
            value=f"**Mention:** {member.mention}\n**Username:** `{member.name}`\n**ID:** `{member.id}`",
            inline=True
        )

        embed.add_field(
            name="📈 Server Status",
            value=(
                f"**Total Boosts:** `{guild.premium_subscription_count}`\n"
                f"**Guild Level:** `Tier {guild.premium_tier}`\n"
                f"**Active Boosters:** `{len(guild.premium_subscribers)}`"
            ),
            inline=True
        )

        # Progress bar towards next tier target
        target_boosts = 2 if guild.premium_tier == 0 else (7 if guild.premium_tier == 1 else 14)
        embed.add_field(
            name="🎯 Tier Progress",
            value=self.create_progress_bar(guild.premium_subscription_count, target_boosts),
            inline=False
        )

        if member.banner:
            embed.set_image(url=member.banner.url)

        footer_icon = guild.icon.url if guild.icon else NITRO_ICON_URL
        embed.set_footer(text=f"{guild.name} • Thank you for boosting!", icon_url=footer_icon)

        return embed

    # ------------------------------------------------------------------
    # EVENT LISTENERS
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Monitors member updates to handle boost additions and revocations."""
        guild = after.guild

        # 1. EVENT: Member started boosting
        if before.premium_since is None and after.premium_since is not None:
            logger.info(f"User {after.name} ({after.id}) started boosting {guild.name}.")
            
            # Grant Reward Role
            role = guild.get_role(ROLE_ID)
            if role and role not in after.roles:
                try:
                    await after.add_roles(role, reason="Server Boost Active")
                except discord.Forbidden:
                    logger.error(f"Missing permissions to grant role {ROLE_ID} to {after.id}.")
                except discord.HTTPException as err:
                    logger.error(f"Failed to add role to {after.id}: {err}")

            # Send Announcement Embed
            channel = guild.get_channel(CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                embed = self.create_boost_announcement_embed(after, role)
                try:
                    await channel.send(content=f"🎉 {after.mention}", embed=embed)
                except (discord.Forbidden, discord.HTTPException) as err:
                    logger.error(f"Failed to send boost message in channel {CHANNEL_ID}: {err}")

        # 2. EVENT: Member stopped boosting
        elif before.premium_since is not None and after.premium_since is None:
            logger.info(f"User {after.name} ({after.id}) stopped boosting {guild.name}.")

            # Record in past boosters dictionary
            self.past_boosters_data[after.id] = {
                "name": after.name,
                "display_name": after.display_name,
                "roles": [r.id for r in after.roles if not r.is_default()]
            }

            # Revoke Reward Role
            role = guild.get_role(ROLE_ID)
            if role and role in after.roles:
                try:
                    await after.remove_roles(role, reason="Server Boost Expired/Transferred")
                except discord.Forbidden:
                    logger.error(f"Missing permissions to remove role {ROLE_ID} from {after.id}.")
                except discord.HTTPException as err:
                    logger.error(f"Failed to remove role from {after.id}: {err}")

            # Send Direct Message Notification
            try:
                dm_embed = discord.Embed(
                    title="💔 Server Boost Status Notice",
                    description=(
                        f"Your boost for **{guild.name}** has ended or was transferred.\n\n"
                        f"The **{role.name if role else 'Booster Reward'}** role has been removed from your account. "
                        f"Feel free to boost again anytime to reclaim your perks!"
                    ),
                    color=EMBED_COLOR_UNBOOST
                )
                if guild.icon:
                    dm_embed.set_thumbnail(url=guild.icon.url)
                dm_embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)

                await after.send(embed=dm_embed)
            except (discord.Forbidden, discord.HTTPException):
                # Fails silently if user has DMs disabled
                pass

    # ------------------------------------------------------------------
    # SLASH COMMANDS
    # ------------------------------------------------------------------

    @app_commands.command(name="boostsinfo", description="Displays detailed metrics, active boosters, and past boosters.")
    async def boosts_info(self, interaction: discord.Interaction):
        """Displays rich server boost metrics across paginated interactive embeds."""
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This command can only be executed within a server.", ephemeral=True)
            return

        await interaction.response.defer()

        # Build Page 1: General Analytics & Active Boosters
        embed_page1 = discord.Embed(
            title=f"✨ {guild.name} Boost Analytics — Overview",
            color=EMBED_COLOR_BOOST
        )

        embed_page1.add_field(
            name="📊 General Statistics",
            value=(
                f"**Total Boost Count:** `{guild.premium_subscription_count}`\n"
                f"**Current Level:** `Tier {guild.premium_tier}`\n"
                f"**Active Subscriber Count:** `{len(guild.premium_subscribers)}`"
            ),
            inline=False
        )

        # Build Active Boosters List
        active_list: List[str] = []
        for member in guild.premium_subscribers:
            roles_str = self.format_member_roles(member)
            active_list.append(f"• {member.mention} (`{member.name}`) — **Roles:** {roles_str}")

        active_value = "\n".join(active_list) if active_list else "No active boosters found at this moment."
        if len(active_value) > 1024:
            active_value = active_value[:1015] + "\n...and more"

        embed_page1.add_field(
            name=f"🚀 Current Boosters ({len(guild.premium_subscribers)})",
            value=active_value,
            inline=False
        )

        if guild.icon:
            embed_page1.set_thumbnail(url=guild.icon.url)
        embed_page1.set_footer(text="Page 1/2 • Guild Boost System")

        # Build Page 2: Past Boosters Records
        embed_page2 = discord.Embed(
            title=f"📜 {guild.name} Boost Analytics — Historical Records",
            color=EMBED_COLOR_INFO
        )

        past_list: List[str] = []
        for user_id, data in self.past_boosters_data.items():
            member = guild.get_member(user_id)
            if member and member in guild.premium_subscribers:
                continue  # Skip active boosters

            if member:
                roles_str = self.format_member_roles(member)
                past_list.append(f"• {member.mention} (`{data['name']}`) — **Roles:** {roles_str}")
            else:
                past_list.append(f"• `{data['name']}` (`ID: {user_id}`) — *(Left Server)*")

        past_value = "\n".join(past_list) if past_list else "No historical boost loss recorded since last reboot."
        if len(past_value) > 1024:
            past_value = past_value[:1015] + "\n...and more"

        embed_page2.add_field(
            name=f"💔 Past Boosters ({len(past_list)})",
            value=past_value,
            inline=False
        )

        if guild.icon:
            embed_page2.set_thumbnail(url=guild.icon.url)
        embed_page2.set_footer(text="Page 2/2 • Guild Boost System")

        pages = [embed_page1, embed_page2]

        if len(pages) > 1:
            view = BoosterPaginatorView(embeds=pages, author_id=interaction.user.id)
            await interaction.followup.send(embed=pages[0], view=view)
        else:
            await interaction.followup.send(embed=pages[0])


async def setup(bot: commands.Bot):
    """Entry point for extension setup."""
    await bot.add_cog(Boosts(bot))
              
