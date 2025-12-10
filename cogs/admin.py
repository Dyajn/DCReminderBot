from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands


def _has_manage_guild(interaction: discord.Interaction) -> bool:
    perms = interaction.user.guild_permissions
    return perms.manage_guild or perms.administrator


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="admin", description="Admin utilities")

    @group.command(name="sync", description="Force-sync application commands for this server")
    async def sync(self, interaction: discord.Interaction):
        if not _has_manage_guild(interaction):
            return await interaction.response.send_message("You need Manage Server permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            # Direct guild sync (syncs whatever is currently in the tree)
            cmds = await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(f"Synced {len(cmds)} commands for guild: {interaction.guild.name}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Sync failed: {e}", ephemeral=True)

    @group.command(name="sync-global", description="Sync commands globally (takes up to 1 hour to appear)")
    async def sync_global(self, interaction: discord.Interaction):
        if not _has_manage_guild(interaction):
            return await interaction.response.send_message("You need Manage Server permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            # Global sync
            cmds = await self.bot.tree.sync()
            await interaction.followup.send(f"Synced {len(cmds)} commands globally (may take up to 1 hour to appear)", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Global sync failed: {e}", ephemeral=True)

    @group.command(name="clear-resync", description="Clear guild commands then re-sync (force refresh)")
    async def clear_resync(self, interaction: discord.Interaction):
        if not _has_manage_guild(interaction):
            return await interaction.response.send_message("You need Manage Server permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            # Clear guild commands
            self.bot.tree.clear_commands(guild=interaction.guild)
            await self.bot.tree.sync(guild=interaction.guild)  # push clear
            
            # Re-add all commands from cogs and sync again
            # This forces a fresh registration
            cmds = await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(f"Cleared and re-synced. Commands now: {len(cmds)}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Clear/resync failed: {e}", ephemeral=True)

    @group.command(name="list-commands", description="List all registered commands (for debugging)")
    async def list_commands(self, interaction: discord.Interaction):
        if not _has_manage_guild(interaction):
            return await interaction.response.send_message("You need Manage Server permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        
        # Get all commands from the tree
        all_commands = []
        
        # Top-level commands
        for cmd in self.bot.tree._global_commands.values():
            if isinstance(cmd, app_commands.Command):
                all_commands.append(f"/{cmd.name}")
            elif isinstance(cmd, app_commands.Group):
                for subcmd in cmd.commands:
                    all_commands.append(f"/{cmd.name} {subcmd.name}")
        
        if not all_commands:
            await interaction.followup.send("No commands found in tree.", ephemeral=True)
        else:
            cmd_list = "\n".join(all_commands)
            if len(cmd_list) > 1900:  # Discord message limit
                cmd_list = cmd_list[:1900] + "\n... (truncated)"
            await interaction.followup.send(f"**Registered commands:**\n```\n{cmd_list}\n```", ephemeral=True)