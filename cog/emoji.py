"""
Emoji Management Cog for discord.py
-----------------------------------
A feature-rich cog designed for managing, stealing, backing up, restoring,
and auditing custom server emojis and application developer portal emojis.

Author: Neel
Version: 2.0.0
"""

import io
import re
import math
import zipfile
import asyncio
import logging
import typing
from datetime import datetime
from dataclasses import dataclass

import aiohttp
from PIL import Image, ImageSequence, ImageOps

import discord
from discord import app_commands
from discord.ext import commands

# =============================================================================
# LOGGING SETUP & CONSTANTS
# =============================================================================

logger = logging.getLogger("EmojiManagementCog")
logger.setLevel(logging.INFO)

# Custom Emojis & Channel IDs
EMOJI_TICK = "<a:Tick:1535660962684870798>"
EMOJI_CROSS = "<:Cross:1535661936434479124>"
EMOJI_ARROW = "<a:Arrow:1535641409074372628>"

SERVER_MSG_CHANNEL_ID = 917378312832180224
AUDIT_LOG_CHANNEL_ID = 1535687126966861856

# Regex Patterns
RANGE_PATTERN = re.compile(r"^(\d+)\s*-\s*(\d+)$")
SINGLE_PATTERN = re.compile(r"^(\d+)$")
EMOJI_NAME_CLEANER = re.compile(r"[^a-zA-Z0-9_]")
CUSTOM_EMOJI_REGEX = re.compile(r"<a?:([a-zA-Z0-9_]+):(\d+)>")

MAX_EMOJI_FILE_SIZE = 256 * 1024  # 256 KB Discord limit
MAX_EMOJI_DIMENSION = (128, 128)  # Recommended maximum dimensions


# =============================================================================
# CUSTOM EXCEPTIONS
# =============================================================================

class EmojiException(Exception):
    """Base exception for emoji cog operations."""
    pass


class EmojiFetchError(EmojiException):
    """Raised when an external or portal emoji fails to download."""
    pass


class EmojiProcessingError(EmojiException):
    """Raised when PIL fails to resample or process image bytes."""
    pass


class EmojiSlotLimitError(EmojiException):
    """Raised when a guild does not have enough emoji slots remaining."""
    pass


# =============================================================================
# DATA STRUCTURES & TYPINGS
# =============================================================================

@dataclass
class EmojiSlotInfo:
    static_used: int
    static_limit: int
    animated_used: int
    animated_limit: int

    @property
    def static_available(self) -> int:
        return max(0, self.static_limit - self.static_used)

    @property
    def animated_available(self) -> int:
        return max(0, self.animated_limit - self.animated_used)


@dataclass
class ProcessedEmojiResult:
    name: str
    image_bytes: bytes
    is_animated: bool
    file_extension: str


# =============================================================================
# IMAGE & UTILITY HELPERS
# =============================================================================

def sanitize_emoji_name(name: str) -> str:
    """
    Cleans a string to adhere to Discord's emoji naming constraints.
    Must be alphanumeric + underscores, 2 to 32 characters long.
    """
    cleaned = EMOJI_NAME_CLEANER.sub("_", name).strip("_")
    if len(cleaned) < 2:
        cleaned = f"emoji_{cleaned}"
    return cleaned[:32]


def calculate_guild_emoji_slots(guild: discord.Guild) -> EmojiSlotInfo:
    """
    Calculates total used and max available static/animated emoji slots
    based on the guild's Server Boost level.
    """
    boost_tier = guild.premium_tier

    # Base limits per tier
    limits = {
        0: 50,
        1: 100,
        2: 150,
        3: 250
    }

    base_limit = limits.get(boost_tier, 50)
    static_used = sum(1 for e in guild.emojis if not e.animated)
    animated_used = sum(1 for e in guild.emojis if e.animated)

    return EmojiSlotInfo(
        static_used=static_used,
        static_limit=base_limit,
        animated_used=animated_used,
        animated_limit=base_limit
    )


def validate_and_process_image(image_bytes: bytes) -> typing.Tuple[bytes, str]:
    """
    Inspects and optimizes image bytes to meet Discord's 256KB & 128x128 limit.
    Handles static images (PNG) and animated images (GIF).
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        raise EmojiProcessingError(f"Invalid image format: {e}")

    is_animated = getattr(img, "is_animated", False)

    # Return untouched if already within ideal bounds
    if len(image_bytes) <= MAX_EMOJI_FILE_SIZE and img.size[0] <= 128 and img.size[1] <= 128:
        return image_bytes, ("gif" if is_animated else "png")

    output = io.BytesIO()

    if is_animated:
        try:
            frames = []
            durations = []

            for frame in ImageSequence.Iterator(img):
                frame_copy = frame.copy().convert("RGBA")
                frame_copy.thumbnail(MAX_EMOJI_DIMENSION, Image.Resampling.LANCZOS)
                frames.append(frame_copy)
                durations.append(frame.info.get("duration", 100))

            first_frame = frames[0]
            first_frame.save(
                output,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                optimize=True,
                loop=0,
                duration=durations
            )
            return output.getvalue(), "gif"
        except Exception as err:
            logger.warning(f"GIF processing optimization failed, falling back to static conversion: {err}")
            img.seek(0)

    # Static Image Processing
    img = img.convert("RGBA")
    img.thumbnail(MAX_EMOJI_DIMENSION, Image.Resampling.LANCZOS)
    img.save(output, format="PNG", optimize=True)
    return output.getvalue(), "png"


async def fetch_developer_portal_emojis(bot: commands.Bot) -> typing.List[typing.Dict[str, typing.Any]]:
    """
    Fetches registered application-level emojis from Discord's Developer Portal REST endpoint.
    """
    url = f"https://discord.com/api/v10/applications/{bot.user.id}/emojis"
    headers = {
        "Authorization": f"Bot {bot.http.token}",
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("items", [])
            logger.error(f"Failed to fetch portal emojis. API HTTP Status: {resp.status}")
            return []


# =============================================================================
# INTERACTIVE UI VIEWS & PAGINATORS
# =============================================================================

class DynamicPaginatorView(discord.ui.View):
    """
    Interactive paginator view for displaying collections of emojis.
    """
    def __init__(self, author_id: int, pages: typing.List[discord.Embed], timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.pages = pages
        self.current_page = 0
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == len(self.pages) - 1)
        self.page_indicator.label = f"{self.current_page + 1}/{len(self.pages)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(f"{EMOJI_CROSS} You cannot control this page menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="🗑️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()
        self.stop()


class EmojiSelectionDropdown(discord.ui.Select):
    """
    Select dropdown menu for choosing specific emojis to steal/delete.
    """
    def __init__(self, emoji_options: typing.List[typing.Tuple[str, str, bool]]):
        options = [
            discord.SelectOption(
                label=name[:25],
                value=f"{name}:{e_id}:{'1' if animated else '0'}",
                description=f"ID: {e_id}",
                emoji="🎞️" if animated else "🖼️"
            )
            for name, e_id, animated in emoji_options[:25]
        ]
        super().__init__(placeholder="Select emojis to import...", min_values=1, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view.selected_items = self.values
        self.view.stop()


class EmojiStealerView(discord.ui.View):
    """
    View wrapping the selection menu for stealing emojis dynamically.
    """
    def __init__(self, author_id: int, emoji_options: typing.List[typing.Tuple[str, str, bool]], timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.selected_items: typing.List[str] = []
        self.dropdown = EmojiSelectionDropdown(emoji_options)
        self.add_item(self.dropdown)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(f"{EMOJI_CROSS} You cannot interact with this menu.", ephemeral=True)
            return False
        return True


class RenameEmojiModal(discord.ui.Modal, title="Rename Emoji"):
    """
    Modal dialog allowing staff to quickly rename a custom emoji.
    """
    new_name = discord.ui.TextInput(
        label="New Emoji Name",
        placeholder="e.g. awesome_pepe",
        min_length=2,
        max_length=32,
        required=True
    )

    def __init__(self, emoji: discord.Emoji):
        super().__init__()
        self.emoji = emoji

    async def on_submit(self, interaction: discord.Interaction):
        clean_name = sanitize_emoji_name(self.new_name.value)
        old_name = self.emoji.name
        try:
            await self.emoji.edit(name=clean_name, reason=f"Renamed by {interaction.user}")
            embed = discord.Embed(
                title=f"{EMOJI_TICK} Emoji Renamed",
                description=f"Successfully updated `{old_name}` to `{clean_name}` {self.emoji}",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except discord.HTTPException as err:
            await interaction.response.send_message(f"{EMOJI_CROSS} Failed to rename emoji: {err}", ephemeral=True)


# =============================================================================
# COG IMPLEMENTATION
# =============================================================================

class EmojiSystem(commands.Cog):
    """
    Main Discord Cog controlling emoji management logic.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: typing.Optional[aiohttp.ClientSession] = None

    async def cog_load(self):
        """Lifecycle listener called when the cog is loaded."""
        self.session = aiohttp.ClientSession()
        logger.info("EmojiSystem Cog loaded & HTTP session opened.")

    async def cog_unload(self):
        """Lifecycle listener called when the cog is unloaded."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("EmojiSystem Cog unloaded & HTTP session closed.")

    async def send_audit_log(self, guild: discord.Guild, embed: discord.Embed):
        """Helper to dispatch structured logs to the audit log channel."""
        channel = self.bot.get_channel(AUDIT_LOG_CHANNEL_ID) or guild.get_channel(AUDIT_LOG_CHANNEL_ID)
        if channel and isinstance(channel, discord.TextChannel):
            try:
                await channel.send(embed=embed)
            except discord.HTTPException as err:
                logger.warning(f"Could not dispatch log to Audit Channel ({AUDIT_LOG_CHANNEL_ID}): {err}")

    # =========================================================================
    # COMMAND: EMOJI ADD (DEVELOPER PORTAL IMPORT)
    # =========================================================================

    @app_commands.command(
        name="emoji-add",
        description="Add emoji(s) from the developer portal via single index or index range (e.g. 1 or 1-20)"
    )
    @app_commands.describe(number="Provide a single index '1' or range '1-20'")
    async def emoji_add_slash(self, interaction: discord.Interaction, number: str):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(f"{EMOJI_CROSS} Command must be used in a server.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.manage_expressions:
            await interaction.response.send_message(f"{EMOJI_CROSS} You require `Manage Expressions` permission.", ephemeral=True)
            return

        clean_input = number.strip()
        single_match = SINGLE_PATTERN.match(clean_input)
        range_match = RANGE_PATTERN.match(clean_input)

        if not single_match and not range_match:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} Invalid syntax. Examples:\n• `/emoji-add number:1`\n• `/emoji-add number:1-15`",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        portal_emojis = await fetch_developer_portal_emojis(self.bot)
        if not portal_emojis:
            await interaction.followup.send(f"{EMOJI_CROSS} No Developer Portal emojis were found.")
            return

        if single_match:
            start_idx = end_idx = int(single_match.group(1))
        else:
            start_idx = int(range_match.group(1))
            end_idx = int(range_match.group(2))

        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx

        if start_idx < 1 or end_idx > len(portal_emojis):
            await interaction.followup.send(
                f"{EMOJI_CROSS} Range out of bounds. Valid index range: `1` to `{len(portal_emojis)}`."
            )
            return

        target_items = portal_emojis[start_idx - 1:end_idx]
        slot_info = calculate_guild_emoji_slots(guild)

        # Pre-validate server capacity
        req_static = sum(1 for item in target_items if not item.get("animated", False))
        req_animated = sum(1 for item in target_items if item.get("animated", False))

        if req_static > slot_info.static_available or req_animated > slot_info.animated_available:
            await interaction.followup.send(
                f"{EMOJI_CROSS} Insufficient emoji slots!\n"
                f"Required: `{req_static}` static, `{req_animated}` animated.\n"
                f"Available: `{slot_info.static_available}` static, `{slot_info.animated_available}` animated."
            )
            return

        successful_imports: typing.List[discord.Emoji] = []
        failed_imports: typing.List[typing.Tuple[str, str]] = []

        for item in target_items:
            e_id = item.get("id")
            e_name = sanitize_emoji_name(item.get("name", "emoji"))
            is_animated = item.get("animated", False)
            ext = "gif" if is_animated else "png"
            cdn_url = f"https://cdn.discordapp.com/emojis/{e_id}.{ext}"

            try:
                async with self.session.get(cdn_url) as resp:
                    if resp.status != 200:
                        failed_imports.append((e_name, f"HTTP Status {resp.status}"))
                        continue
                    raw_bytes = await resp.read()

                processed_bytes, _ = await asyncio.to_thread(validate_and_process_image, raw_bytes)
                created_emoji = await guild.create_custom_emoji(
                    name=e_name,
                    image=processed_bytes,
                    reason=f"Added via /emoji-add by {interaction.user} ({interaction.user.id})"
                )
                successful_imports.append(created_emoji)

            except discord.HTTPException as http_err:
                failed_imports.append((e_name, f"Discord API Error: {http_err.text}"))
            except Exception as exc:
                failed_imports.append((e_name, f"Error: {str(exc)}"))

            # Respect rate limits
            await asyncio.sleep(1.2)

        # Construct response embed
        status_color = discord.Color.green() if successful_imports else discord.Color.red()
        embed = discord.Embed(
            title=f"{EMOJI_TICK} Emoji Batch Import Result",
            color=status_color,
            timestamp=datetime.utcnow()
        )
        embed.description = f"Processed items **{start_idx}** to **{end_idx}**."

        if successful_imports:
            formatted_emojis = " ".join([str(e) for e in successful_imports[:25]])
            if len(successful_imports) > 25:
                formatted_emojis += f" *(+{len(successful_imports) - 25} more)*"
            embed.add_field(name=f"Successfully Added ({len(successful_imports)})", value=formatted_emojis, inline=False)

        if failed_imports:
            failed_fmt = "\n".join([f"• **{n}**: {r}" for n, r in failed_imports[:10]])
            embed.add_field(name=f"Failed Imports ({len(failed_imports)})", value=failed_fmt, inline=False)

        await interaction.followup.send(embed=embed)

        # Audit Log Dispatch
        audit_embed = discord.Embed(
            title="Emoji Import Audit Log",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        audit_embed.add_field(name="Executor", value=f"{interaction.user.mention} (`{interaction.user.id}`)")
        audit_embed.add_field(name="Import Range", value=f"`{start_idx}` - `{end_idx}`")
        audit_embed.add_field(name="Results", value=f"Added: `{len(successful_imports)}` | Failed: `{len(failed_imports)}`")
        await self.send_audit_log(guild, audit_embed)

    # =========================================================================
    # COMMAND: EMOJI STEAL
    # =========================================================================

    @app_commands.command(
        name="emoji-steal",
        description="Steal or clone custom emojis from a raw text message into your server"
    )
    @app_commands.describe(emojis="Paste custom emojis or message containing custom emojis")
    async def emoji_steal_slash(self, interaction: discord.Interaction, emojis: str):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(f"{EMOJI_CROSS} Command must be used in a server.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.manage_expressions:
            await interaction.response.send_message(f"{EMOJI_CROSS} Missing permissions (`Manage Expressions`).", ephemeral=True)
            return

        matches = CUSTOM_EMOJI_REGEX.findall(emojis)
        if not matches:
            await interaction.response.send_message(
                f"{EMOJI_CROSS} No valid custom emojis were detected in your input string.",
                ephemeral=True
            )
            return

        # De-duplicate matches
        unique_matches: typing.List[typing.Tuple[str, str, bool]] = []
        seen_ids = set()

        for name, e_id in matches:
            if e_id not in seen_ids:
                seen_ids.add(e_id)
                is_animated = f"<a:{name}:{e_id}>" in emojis
                unique_matches.append((name, e_id, is_animated))

        # Show menu if more than 5 emojis detected
        selected_emojis = unique_matches
        if len(unique_matches) > 5:
            view = EmojiStealerView(interaction.user.id, unique_matches)
            await interaction.response.send_message(
                f"{EMOJI_ARROW} Found **{len(unique_matches)}** emojis. Select which ones you want to steal:",
                view=view
            )
            await view.wait()

            if not view.selected_items:
                return

            selected_emojis = []
            for item_val in view.selected_items:
                s_name, s_id, s_anim = item_val.split(":")
                selected_emojis.append((s_name, s_id, s_anim == "1"))

        else:
            await interaction.response.defer()

        successful: typing.List[discord.Emoji] = []
        failed: typing.List[str] = []

        for name, e_id, is_animated in selected_emojis:
            clean_name = sanitize_emoji_name(name)
            ext = "gif" if is_animated else "png"
            cdn_url = f"https://cdn.discordapp.com/emojis/{e_id}.{ext}"

            try:
                async with self.session.get(cdn_url) as resp:
                    if resp.status == 200:
                        raw_data = await resp.read()
                        processed_data, _ = await asyncio.to_thread(validate_and_process_image, raw_data)
                        created = await guild.create_custom_emoji(
                            name=clean_name,
                            image=processed_data,
                            reason=f"Stolen by {interaction.user}"
                        )
                        successful.append(created)
                    else:
                        failed.append(clean_name)
            except Exception as e:
                logger.error(f"Error stealing emoji {clean_name}: {e}")
                failed.append(clean_name)

            await asyncio.sleep(1.0)

        result_embed = discord.Embed(
            title=f"{EMOJI_TICK} Emoji Steal Complete",
            color=discord.Color.green() if successful else discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        if successful:
            result_embed.add_field(name="Imported Emojis", value=" ".join([str(e) for e in successful]), inline=False)
        if failed:
            result_embed.add_field(name="Failed Emojis", value=", ".join([f"`{f}`" for f in failed]), inline=False)

        if interaction.response.is_done():
            await interaction.followup.send(embed=result_embed)
        else:
            await interaction.edit_original_response(content=None, embed=result_embed, view=None)

    # =========================================================================
    # COMMAND: EMOJI LIST
    # =========================================================================

    @app_commands.command(
        name="emoji-list",
        description="Display an interactive paginated list of current server or portal emojis"
    )
    @app_commands.choices(source=[
        app_commands.Choice(name="Server Emojis", value="server"),
        app_commands.Choice(name="Developer Portal Emojis", value="portal")
    ])
    async def emoji_list_slash(self, interaction: discord.Interaction, source: str = "server"):
        await interaction.response.defer()

        pages: typing.List[discord.Embed] = []

        if source == "server":
            guild = interaction.guild
            if not guild or not guild.emojis:
                await interaction.followup.send(f"{EMOJI_CROSS} This server has no custom emojis.")
                return

            emoji_chunks = [guild.emojis[i:i + 10] for i in range(0, len(guild.emojis), 10)]
            for idx, chunk in enumerate(emoji_chunks):
                embed = discord.Embed(
                    title=f"Server Emojis for {guild.name}",
                    color=discord.Color.blurple(),
                    timestamp=datetime.utcnow()
                )
                description = ""
                for e in chunk:
                    description += f"{e} | `{e.name}` | ID: `{e.id}`\n"
                embed.description = description
                embed.set_footer(text=f"Page {idx + 1} of {len(emoji_chunks)}")
                pages.append(embed)

        else:
            portal_emojis = await fetch_developer_portal_emojis(self.bot)
            if not portal_emojis:
                await interaction.followup.send(f"{EMOJI_CROSS} No portal emojis found.")
                return

            chunks = [portal_emojis[i:i + 10] for i in range(0, len(portal_emojis), 10)]
            for idx, chunk in enumerate(chunks):
                embed = discord.Embed(
                    title="Developer Portal Application Emojis",
                    color=discord.Color.gold(),
                    timestamp=datetime.utcnow()
                )
                description = ""
                for real_idx, item in enumerate(chunk, start=(idx * 10) + 1):
                    e_id = item.get("id")
                    e_name = item.get("name")
                    is_animated = item.get("animated", False)
                    formatted = f"<a:{e_name}:{e_id}>" if is_animated else f"<:{e_name}:{e_id}>"
                    description += f"`#{real_idx}` {formatted} | `{e_name}` | ID: `{e_id}`\n"

                embed.description = description
                embed.set_footer(text=f"Page {idx + 1} of {len(chunks)}")
                pages.append(embed)

        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0])
        else:
            view = DynamicPaginatorView(interaction.user.id, pages)
            await interaction.followup.send(embed=pages[0], view=view)

    # =========================================================================
    # COMMAND: EMOJI BACKUP & RESTORE
    # =========================================================================

    @app_commands.command(name="emoji-backup", description="Package all server custom emojis into a downloadable .zip archive")
    async def emoji_backup_slash(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(f"{EMOJI_CROSS} Command must be used in a server.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(f"{EMOJI_CROSS} Administrator permission required.", ephemeral=True)
            return

        if not guild.emojis:
            await interaction.response.send_message(f"{EMOJI_CROSS} No custom emojis available to back up.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        zip_buffer = io.BytesIO()
        successful_backups = 0

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for emoji in guild.emojis:
                ext = "gif" if emoji.animated else "png"
                file_name = f"{sanitize_emoji_name(emoji.name)}_{emoji.id}.{ext}"

                try:
                    async with self.session.get(emoji.url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            zf.writestr(file_name, data)
                            successful_backups += 1
                except Exception as err:
                    logger.error(f"Failed backing up emoji {emoji.name}: {err}")

        zip_buffer.seek(0)
        archive_name = f"{guild.id}_emoji_backup.zip"
        file = discord.File(fp=zip_buffer, filename=archive_name)

        embed = discord.Embed(
            title=f"{EMOJI_TICK} Emoji Backup Complete",
            description=f"Successfully packaged **{successful_backups}/{len(guild.emojis)}** custom emojis.",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @app_commands.command(name="emoji-restore", description="Restore emojis from an uploaded zip archive")
    @app_commands.describe(archive="Upload a valid emoji backup .zip file")
    async def emoji_restore_slash(self, interaction: discord.Interaction, archive: discord.Attachment):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(f"{EMOJI_CROSS} Command must be used in a server.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(f"{EMOJI_CROSS} Administrator permission required.", ephemeral=True)
            return

        if not archive.filename.endswith(".zip"):
            await interaction.response.send_message(f"{EMOJI_CROSS} Provided attachment must be a `.zip` archive.", ephemeral=True)
            return

        await interaction.response.defer()

        zip_data = await archive.read()
        successful, failed = [], []

        try:
            with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zf:
                for file_info in zf.infolist():
                    if file_info.is_dir():
                        continue

                    raw_name = file_info.filename.split("/")[-1].split(".")[0]
                    clean_name = sanitize_emoji_name(raw_name.split("_")[0])
                    img_bytes = zf.read(file_info)

                    try:
                        processed_bytes, _ = await asyncio.to_thread(validate_and_process_image, img_bytes)
                        created = await guild.create_custom_emoji(
                            name=clean_name,
                            image=processed_bytes,
                            reason=f"Restored from backup by {interaction.user}"
                        )
                        successful.append(created)
                    except Exception as e:
                        failed.append((clean_name, str(e)))

                    await asyncio.sleep(1.0)
        except zipfile.BadZipFile:
            await interaction.followup.send(f"{EMOJI_CROSS} Corrupted or invalid zip file uploaded.")
            return

        embed = discord.Embed(
            title=f"{EMOJI_TICK} Restore Complete",
            color=discord.Color.green() if successful else discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Restored Emojis", value=f"`{len(successful)}` emojis added successfully.")
        if failed:
            embed.add_field(name="Failed Restores", value=f"`{len(failed)}` files could not be imported.")

        await interaction.followup.send(embed=embed)

    # =========================================================================
    # COMMAND: EMOJI STATS & INFO
    # =========================================================================

    @app_commands.command(name="emoji-stats", description="Check current guild emoji slot limits and availability statistics")
    async def emoji_stats_slash(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(f"{EMOJI_CROSS} Command must be used in a server.", ephemeral=True)
            return

        slots = calculate_guild_emoji_slots(guild)

        embed = discord.Embed(
            title=f"Emoji Statistics - {guild.name}",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

        static_bar = f"`[{'█' * math.ceil(slots.static_used / slots.static_limit * 10)}{'░' * (10 - math.ceil(slots.static_used / slots.static_limit * 10))}]`"
        animated_bar = f"`[{'█' * math.ceil(slots.animated_used / slots.animated_limit * 10)}{'░' * (10 - math.ceil(slots.animated_used / slots.animated_limit * 10))}]`"

        embed.add_field(
            name="Static Emoji Slots",
            value=f"{static_bar}\nUsed: **{slots.static_used}** / **{slots.static_limit}**\nAvailable: **{slots.static_available}**",
            inline=True
        )
        embed.add_field(
            name="Animated Emoji Slots",
            value=f"{animated_bar}\nUsed: **{slots.animated_used}** / **{slots.animated_limit}**\nAvailable: **{slots.animated_available}**",
            inline=True
        )
        embed.add_field(name="Server Boost Tier", value=f"Tier **{guild.premium_tier}**", inline=False)

        await interaction.response.send_message(embed=embed)


# =============================================================================
# COG SETUP
# =============================================================================

async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiSystem(bot))
