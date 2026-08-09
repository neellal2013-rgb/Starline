import datetime
import discord
from discord.ext import commands

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------
TICKET_CHANNEL_ID = 1522926258974097488
MAX_DAILY_TICKETS = 5

SUPPORT_ROLE_IDS = [
    903669125077938226,
    920514422475198475,
    921339694824955916,
    915634037081657414,
    903670211239116860,
    915806692736917555,
    1535642421231099955,
]

# Track daily ticket creation: {user_id: {"count": int, "date": datetime.date}}
user_ticket_tracker = {}

# -------------------------------------------------------------------
# Ticket Controls (Inside Ticket Channel)
# -------------------------------------------------------------------
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="btn_close_ticket",
    )
    async def close_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        closing_embed = discord.Embed(
            title="Ticket Closing",
            description="This channel will be permanently deleted in **5 seconds**.",
            color=discord.Color.red(),
        )
        closing_embed.set_footer(text="System Automated Cleanup")
        await interaction.response.send_message(embed=closing_embed)
        
        await discord.utils.sleep_until(
            discord.utils.utcnow() + datetime.timedelta(seconds=5)
        )
        await interaction.channel.delete()

    @discord.ui.button(
        label="Claim Ticket",
        style=discord.ButtonStyle.secondary,
        emoji="✋",
        custom_id="btn_claim_ticket",
    )
    async def claim_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Verify if user has support permissions
        user_roles = [r.id for r in interaction.user.roles]
        if not any(role_id in user_roles for role_id in SUPPORT_ROLE_IDS):
            await interaction.response.send_message(
                "Only support staff can claim tickets.", ephemeral=True
            )
            return

        claim_embed = discord.Embed(
            title="Ticket Claimed",
            description=f"This ticket is now being handled by {interaction.user.mention}.",
            color=discord.Color.gold(),
        )
        claim_embed.set_timestamp(discord.utils.utcnow())
        
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.message.edit(view=self)
        await interaction.response.send_message(embed=claim_embed)


# -------------------------------------------------------------------
# Panel Dropdown & Launcher
# -------------------------------------------------------------------
class TicketSelectMenu(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="General Support",
                description="Get help with general questions and server issues.",
                emoji="❓",
                value="General",
            ),
            discord.SelectOption(
                label="Player Report",
                description="Report a user breaking server rules.",
                emoji="⚠️",
                value="Report",
            ),
            discord.SelectOption(
                label="Partnership",
                description="Apply for partnerships or collaborations.",
                emoji="🤝",
                value="Partner",
            ),
        ]
        super().__init__(
            placeholder="Select a category to open a ticket...",
            min_values=1,
            max_values=1,
            custom_id="ticket_category_select",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild
        today = datetime.date.today()
        category_chosen = self.values[0]

        # Enforce rate limits per user per day
        user_data = user_ticket_tracker.get(user.id, {"count": 0, "date": today})
        if user_data["date"] != today:
            user_data = {"count": 0, "date": today}

        if user_data["count"] >= MAX_DAILY_TICKETS:
            limit_embed = discord.Embed(
                title="Limit Reached",
                description=f"You have already created **{MAX_DAILY_TICKETS} tickets** today. Please wait until tomorrow.",
                color=discord.Color.dark_red(),
            )
            await interaction.response.send_message(embed=limit_embed, ephemeral=True)
            return

        user_data["count"] += 1
        user_ticket_tracker[user.id] = user_data

        # Configure permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, attach_files=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_channels=True
            ),
        }

        for role_id in SUPPORT_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                )

        # Create channel
        channel_name = f"{category_chosen.lower()}-{user.name}"
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=interaction.channel.category,
        )

        # Notify user
        notify_embed = discord.Embed(
            description=f"Your **{category_chosen}** ticket has been created: {ticket_channel.mention}",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=notify_embed, ephemeral=True)

        # Welcome Card inside ticket
        welcome_embed = discord.Embed(
            title=f"📥 {category_chosen} Ticket",
            description=(
                "Support will be with you shortly.\n"
                "To close this press the close button"
            ),
            color=discord.Color.blurple(),
        )
        welcome_embed.add_field(
            name="Ticket Details",
            value=f"• **Opened By:** {user.mention}\n• **Category:** {category_chosen}\n• **Time:** <t:{int(discord.utils.utcnow().timestamp())}:F>",
            inline=False,
        )
        welcome_embed.set_thumbnail(url=user.display_avatar.url)
        welcome_embed.set_footer(
            text=f"User ID: {user.id}", icon_url=guild.icon.url if guild.icon else None
        )

        await ticket_channel.send(
            content=f"{user.mention} welcome",
            embed=welcome_embed,
            view=TicketControlView(),
        )


class TicketLauncherContainer(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelectMenu())


# -------------------------------------------------------------------
# Cog Definition
# -------------------------------------------------------------------
class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # Register persistent views so buttons/dropdowns function across restarts
        self.bot.add_view(TicketLauncherContainer())
        self.bot.add_view(TicketControlView())

        channel = self.bot.get_channel(TICKET_CHANNEL_ID)
        if channel:
            # Check for existing panel to prevent duplicates on restart
            async for message in channel.history(limit=15):
                if message.author == self.bot.user and message.components:
                    return

            panel_embed = discord.Embed(
                title="🎫 Support Ticket Desk",
                description=(
                    "Need assistance or have questions? Select a topic below to get in touch with our team.\n\n"
                    "📌 **Rules:**\n"
                    "• Please provide clear details upon opening a ticket.\n"
                    "• Limit of **5 tickets** per user per day.\n"
                    "• Do not open tickets for non-support reasons."
                ),
                color=discord.Color.dark_theme(),
            )
            if channel.guild.icon:
                panel_embed.set_thumbnail(url=channel.guild.icon.url)
            panel_embed.set_footer(text="Official Guild Support System")

            await channel.send(embed=panel_embed, view=TicketLauncherContainer())


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
              
