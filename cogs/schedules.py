# cogs/schedules.py
import discord
from discord.ext import commands
from discord import app_commands

WEEKDAY_MAP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

def parse_day(day: str) -> int:
    k = day.strip().lower()
    if k not in WEEKDAY_MAP:
        raise ValueError("Invalid day. Use one of: Mon/Tue/Wed/Thu/Fri/Sat/Sun")
    return WEEKDAY_MAP[k]

def validate_time(hhmm: str):
    parts = hhmm.split(":")
    if len(parts) != 2:
        raise ValueError("Use HH:MM 24h format")
    h, m = parts
    h = int(h); m = int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Invalid time range")
    return f"{h:02d}:{m:02d}"

class SchedulesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="schedule", description="Weekly schedule")

    @group.command(name="add", description="Add a schedule entry for a day with subject and times")
    async def add(self, interaction: discord.Interaction, day: str, subject: str, start_hhmm: str, end_hhmm: str, notes: str = None):
        await interaction.response.defer(ephemeral=True)
        try:
            weekday = parse_day(day)
            start = validate_time(start_hhmm)
            end = validate_time(end_hhmm)
        except Exception as e:
            return await interaction.followup.send(str(e), ephemeral=True)
        self.bot.db.execute(
            "INSERT INTO schedules (guild_id, weekday, subject, start_time, end_time, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (interaction.guild_id, weekday, subject, start, end, notes or "")
        )
        await interaction.followup.send(f"Added: {subject} on {day.title()} {start}-{end}", ephemeral=True)

    @group.command(name="list", description="Show schedule for a day or the whole week")
    async def list(self, interaction: discord.Interaction, day: str = None):
        await interaction.response.defer()
        if day:
            try:
                wd = parse_day(day)
            except Exception as e:
                return await interaction.followup.send(str(e), ephemeral=True)
            rows = self.bot.db.query(
                "SELECT subject, start_time, end_time, notes FROM schedules WHERE guild_id = ? AND weekday = ? ORDER BY start_time",
                (interaction.guild_id, wd),
            )
            title = f"{day.title()} schedule"
        else:
            rows = self.bot.db.query(
                "SELECT weekday, subject, start_time, end_time, notes FROM schedules WHERE guild_id = ? ORDER BY weekday, start_time",
                (interaction.guild_id,),
            )
            title = "Weekly schedule"
        if not rows:
            return await interaction.followup.send("No schedule entries found.")
        embed = discord.Embed(title=title, color=discord.Color.blue())
        if day:
            for r in rows:
                name = f"{r['start_time']}-{r['end_time']}: {r['subject']}"
                val = r["notes"] or "-"
                embed.add_field(name=name, value=val, inline=False)
        else:
            day_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            by_day = {i: [] for i in range(7)}
            for r in rows:
                by_day[r["weekday"]].append(r)
            for i in range(7):
                entries = by_day[i]
                if not entries:
                    continue
                value = "\n".join(f"{row['start_time']}-{row['end_time']}: {row['subject']}" + (f" — {row['notes']}" if row['notes'] else "") for row in entries)
                embed.add_field(name=day_names[i], value=value, inline=False)
        await interaction.followup.send(embed=embed)

    @group.command(name="clear", description="Clear schedule for a specific day")
    async def clear(self, interaction: discord.Interaction, day: str):
        await interaction.response.defer(ephemeral=True)
        try:
            wd = parse_day(day)
        except Exception as e:
            return await interaction.followup.send(str(e), ephemeral=True)
        self.bot.db.execute("DELETE FROM schedules WHERE guild_id = ? AND weekday = ?", (interaction.guild_id, wd))
        await interaction.followup.send(f"Cleared schedule for {day.title()}.", ephemeral=True)