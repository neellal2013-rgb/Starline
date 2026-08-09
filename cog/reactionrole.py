import os
import sys
import logging
import asyncio
from typing import Dict, Optional, List, Set

import discord
from discord.ext import commands, tasks

# ==============================================================================
# LOGGING SETUP
# ==============================================================================
logger = logging.getLogger("Cogs.ReactionRoles")

# ==============================================================================
# CONFIGURATION
# ==============================================================================
TARGET_CHANNEL_ID: int = 917266635495198750

COLOR_ROLE_MAP: Dict[str, int] = {
    "💜": 917265912170696796,  # Purple
    "🟡": 920210092819873792,  # Yellow
    "🟣": 915955210537533480,  # Pink
    "⬛": 917265577733656586,  # Black
    "🟢": 917265207871565874,  # Green
    "🔵": 917265423777546260,  # Blue
    "🔴": 917265127676452904,  # Red
}

ROLE_DISPLAY_NAMES: Dict[int, str] = {
    917265912170696796: "purple",
    920210092819873792: "yellow",
    915955210537533480: "pink",
    917265577733656586: "black",
    917265207871565874: "green",
    917265423777546260: "blue",
    917265127676452904: "Red",
}

EMBED_TITLE: str = "Role Menu: Favorite colour"
EMBED_BASE_DESCRIPTION: str = "React to give yourself a role.\n"


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_menu_message_id: Optional[int] = None

    # ==========================================================================
    # COG LIFECYCLE HOOKS
    # ==========================================================================
    async def cog_load(self):
        """Triggered automatically when the cog is loaded."""
        logger.info("ReactionRoles Cog loaded into bot runtime.")
        self.initialize_menu.start()

    async def cog_unload(self):
        """Triggered when cog is unloaded/reloaded."""
        self.initialize_menu.cancel()
        self.background_health_check.cancel()
        logger.info("ReactionRoles Cog unloaded.")

    # ==========================================================================
    # HELPER UTILITIES
    # ==========================================================================
    def generate_menu_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=EMBED_TITLE,
            description=EMBED_BASE_DESCRIPTION,
            color=discord.Color.dark_theme(),
        )

        lines: List[str] = [EMBED_BASE_DESCRIPTION]
        for emoji, role_id in COLOR_ROLE_MAP.items():
            role_name = ROLE_DISPLAY_NAMES.get(role_id, "unknown")
            lines.append(f"{emoji} : `{role_name}`")

        embed.description = "\n".join(lines)
        return embed

    async def get_target_channel(self) -> Optional[discord.TextChannel]:
        channel = self.bot.get_channel(TARGET_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(TARGET_CHANNEL_ID)
            except discord.NotFound:
                logger.error(f"Channel ID {TARGET_CHANNEL_ID} not found.")
                return None
            except discord.Forbidden:
                logger.error(
                    f"Bot lacks permissions to access channel {TARGET_CHANNEL_ID}."
                )
                return None
            except discord.HTTPException as err:
                logger.error(f"HTTP error fetching channel: {err}")
                return None

        if not isinstance(channel, discord.TextChannel):
            logger.error(
                f"Channel ID {TARGET_CHANNEL_ID} is not a text channel."
            )
            return None

        return channel

    async def fetch_or_post_menu_message(
        self, channel: discord.TextChannel
    ) -> Optional[discord.Message]:
        embed = self.generate_menu_embed()
        existing_message: Optional[discord.Message] = None

        try:
            async for message in channel.history(limit=30):
                if message.author == self.bot.user and message.embeds:
                    if message.embeds[0].title == EMBED_TITLE:
                        existing_message = message
                        logger.info(
                            f"Found existing menu message ID: {message.id}"
                        )
                        break
        except Exception as err:
            logger.error(f"Error checking channel history: {err}")
            return None

        if existing_message:
            try:
                await existing_message.edit(embed=embed)
                return existing_message
            except discord.HTTPException as err:
                logger.error(f"Failed editing existing menu embed: {err}")
                return existing_message
        else:
            try:
                new_message = await channel.send(embed=embed)
                logger.info(
                    f"Posted new reaction role message ID: {new_message.id}"
                )
                return new_message
            except discord.HTTPException as err:
                logger.error(f"Failed posting menu message: {err}")
                return None

    async def ensure_reactions_added(self, message: discord.Message) -> None:
        existing_emojis: Set[str] = {
            str(reaction.emoji) for reaction in message.reactions
        }

        for emoji in COLOR_ROLE_MAP.keys():
            if emoji not in existing_emojis:
                try:
                    await message.add_reaction(emoji)
                    await asyncio.sleep(0.3)
                except Exception as err:
                    logger.error(f"Failed adding reaction {emoji}: {err}")

    # ==========================================================================
    # AUTOMATED STARTUP INITIALIZER & TASKS
    # ==========================================================================
    @tasks.loop(count=1)
    async def initialize_menu(self):
        await self.bot.wait_until_ready()

        channel = await self.get_target_channel()
        if not channel:
            return

        menu_message = await self.fetch_or_post_menu_message(channel)
        if not menu_message:
            return

        self.target_menu_message_id = menu_message.id
        await self.ensure_reactions_added(menu_message)

        if not self.background_health_check.is_running():
            self.background_health_check.start()

        logger.info("ReactionRoles initialization complete and running.")

    @tasks.loop(minutes=15)
    async def background_health_check(self):
        if self.target_menu_message_id is None:
            channel = await self.get_target_channel()
            if channel:
                msg = await self.fetch_or_post_menu_message(channel)
                if msg:
                    self.target_menu_message_id = msg.id

    # ==========================================================================
    # EVENT LISTENERS
    # ==========================================================================
    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ):
        if payload.user_id == self.bot.user.id:
            return

        if payload.channel_id != TARGET_CHANNEL_ID:
            return

        if (
            self.target_menu_message_id
            and payload.message_id != self.target_menu_message_id
        ):
            return

        emoji_str = str(payload.emoji)
        if emoji_str not in COLOR_ROLE_MAP:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        target_role_id = COLOR_ROLE_MAP[emoji_str]
        role = guild.get_role(target_role_id)
        if not role:
            return

        member = guild.get_member(payload.user_id)
        if not member:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return

        if member.bot:
            return

        # Strip existing color roles so user can only hold 1 color role at a time
        current_color_roles = [
            r
            for r in member.roles
            if r.id in COLOR_ROLE_MAP.values() and r.id != target_role_id
        ]
        if current_color_roles:
            try:
                await member.remove_roles(
                    *current_color_roles, reason="Changing color choice"
                )
            except discord.HTTPException as err:
                logger.error(
                    f"Failed removing existing color roles from {member}: {err}"
                )

        if role not in member.roles:
            try:
                await member.add_roles(
                    role, reason="Assigned via automatic color reaction menu"
                )
                logger.info(
                    f"Assigned color role '{role.name}' to {member.display_name}"
                )
            except discord.HTTPException as err:
                logger.error(f"Failed assigning role {role.name}: {err}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ):
        if payload.user_id == self.bot.user.id:
            return

        if payload.channel_id != TARGET_CHANNEL_ID:
            return

        if (
            self.target_menu_message_id
            and payload.message_id != self.target_menu_message_id
        ):
            return

        emoji_str = str(payload.emoji)
        if emoji_str not in COLOR_ROLE_MAP:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        target_role_id = COLOR_ROLE_MAP[emoji_str]
        role = guild.get_role(target_role_id)
        if not role:
            return

        member = guild.get_member(payload.user_id)
        if not member:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return

        if member.bot:
            return

        if role in member.roles:
            try:
                await member.remove_roles(
                    role, reason="Removed via automatic color reaction menu"
                )
                logger.info(
                    f"Removed color role '{role.name}' from {member.display_name}"
                )
            except discord.HTTPException as err:
                logger.error(f"Failed removing role {role.name}: {err}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
              
