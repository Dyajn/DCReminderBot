import discord
from discord.ext import commands
from discord import app_commands


def is_controller(interaction: discord.Interaction, announcer_user_id: int) -> bool:
    # If controller is set, enforce; else allow Manage Guild
    if announcer_user_id:
        return interaction.user.id == announcer_user_id
    perms = interaction.user.guild_permissions
    return perms.manage_guild or perms.administrator


class AnnouncementsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="announce", description="Announcements")

    @group.command(name="set-controller", description="Set the single user allowed to post announcements")
    async def set_controller(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer(ephemeral=True)
        self.bot.db.upsert_config(interaction.guild_id, announcer_user_id=user.id)
        await interaction.followup.send(
            f"Announcements controller set to {user.mention}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group.command(name="set-defaults", description="Set default role and channel for announcements")
    async def set_defaults(self, interaction: discord.Interaction, role: discord.Role, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        self.bot.db.upsert_config(interaction.guild_id, announce_role_id=role.id, announce_channel_id=channel.id)
        await interaction.followup.send(
            f"Defaults set: role {role.mention}, channel {channel.mention}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group.command(name="post", description="Post an announcement to a channel (can ping a role or everyone)")
    async def post(self, interaction: discord.Interaction, message: str, role: discord.Role = None, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        cfg = self.bot.db.get_config(interaction.guild_id) or {}
        if not is_controller(interaction, cfg["announcer_user_id"] if cfg else 0):
            return await interaction.followup.send("You are not authorized to post announcements.", ephemeral=True)

        role_id = role.id if role else (cfg["announce_role_id"] if cfg else None)
        channel_id = channel.id if channel else (cfg["announce_channel_id"] if cfg else None)
        if not channel_id:
            return await interaction.followup.send("No channel specified and default is not set.", ephemeral=True)

        ch = interaction.guild.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)

        # Build message: include role mention if provided/defaulted. User message may contain @everyone/@here.
        prefix = f"<@&{role_id}> " if role_id else ""
        content = f"{prefix}{message}"

        await ch.send(
            content=content,
            # Allow role pings and @everyone/@here. User mentions remain disabled.
            allowed_mentions=discord.AllowedMentions(roles=True, everyone=True, users=False),
        )
        await interaction.followup.send("Announcement posted.", ephemeral=True)