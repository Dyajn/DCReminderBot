import os
import logging
import discord
from discord.ext import commands
from discord import app_commands

from db import Database

# Cogs
from cogs.reminders import RemindersCog
from cogs.announcements import AnnouncementsCog
from cogs.assessments import AssessmentsCog
from cogs.schedules import SchedulesCog
from cogs.admin import AdminCog
from cogs.aliases import AliasesCog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("discord-bot")

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.message_content = True  # Needed for prefix commands

# Temporary sync command (remove after /admin sync works)
@commands.has_guild_permissions(administrator=True)
@commands.command(name="sync")
async def temp_sync(ctx: commands.Context):
    """Temporary prefix command to sync slash commands"""
    try:
        cmds = await ctx.bot.tree.sync(guild=ctx.guild)
        await ctx.reply(f"Synced {len(cmds)} commands for guild: {ctx.guild.name}")
    except Exception as e:
        await ctx.reply(f"Sync failed: {e}")

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.db = Database(os.getenv("DB_PATH", "data/bot.db"))
        self._synced_once = False

    async def setup_hook(self):
        # Load cogs
        await self.add_cog(RemindersCog(self))
        await self.add_cog(AnnouncementsCog(self))
        await self.add_cog(AssessmentsCog(self))
        await self.add_cog(SchedulesCog(self))
        await self.add_cog(AdminCog(self))
        await self.add_cog(AliasesCog(self))
        logger.info("Cogs loaded.")

        # Add temporary sync command
        self.add_command(temp_sync)

        # Simple ping command to verify slash availability
        @app_commands.command(name="ping", description="Check if the bot is alive")
        async def ping(interaction: discord.Interaction):
            await interaction.response.send_message("Pong!", ephemeral=True)
        self.tree.add_command(ping)

    async def on_ready(self):
        # Ensure we sync once per process
        if self._synced_once:
            return
        try:
            if not self.guilds:
                # If not in any guilds, register globally as fallback
                cmds = await self.tree.sync()
                logger.info("Synced %d global commands (bot not in any guild yet).", len(cmds))
            else:
                # Register commands per-guild for immediate availability (no duplicates)
                total = 0
                for guild in self.guilds:
                    cmds = await self.tree.sync(guild=guild)
                    total += len(cmds)
                    logger.info("Synced %d commands for guild: %s (%s)", len(cmds), guild.name, guild.id)
                logger.info("Finished per-guild sync. Total commands synced across guilds: %d", total)
                
            self._synced_once = True
        except Exception as e:
            logger.exception("Failed to sync application commands: %s", e)

def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable not set")

    # Ensure data folder exists
    os.makedirs("data", exist_ok=True)

    bot = Bot()
    bot.run(token)

if __name__ == "__main__":
    main()