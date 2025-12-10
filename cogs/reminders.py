import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import discord
from discord.ext import commands, tasks
from discord import app_commands

from zoneinfo import ZoneInfo
import re

def parse_timezone(tz_name: Optional[str]):
    # Return a tzinfo for the given IANA name; special-case UTC so tzdata isn't required
    name = (tz_name or "UTC").strip()
    if name.upper() in ("UTC", "Z"):
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        # Fallback to UTC if tzdata missing or invalid timezone name
        return timezone.utc

DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)

def parse_offset_str(offset_str: str) -> List[int]:
    # Accept comma-separated durations like "3d, 24h, 4h, 30m"
    out = []
    for part in offset_str.split(","):
        p = part.strip()
        if not p:
            continue
        m = DURATION_RE.match(p)
        if not m:
            raise ValueError(f"Invalid duration: {p} (use s,m,h,d,w)")
        val = int(m.group(1))
        unit = m.group(2).lower()
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        out.append(val * mult)
    # Ensure unique and sorted descending (largest lead time first)
    out = sorted(set(out), reverse=True)
    return out

def default_offsets(now_utc: int, due_utc: int) -> List[int]:
    # Safety margin logic as described:
    # - If >= 3 days: 3d, 2d, 1d
    # - If 2-3 days: 24h
    # - If 12h-2d: 4h
    # - Additional sensible fallbacks
    lead = due_utc - now_utc
    day = 86400
    hour = 3600
    if lead >= 3 * day:
        return [3 * day, 2 * day, day]
    elif 2 * day <= lead < 3 * day:
        return [day]
    elif 12 * hour <= lead < 2 * day:
        return [4 * hour]
    elif 4 * hour <= lead < 12 * hour:
        return [2 * hour]
    elif hour <= lead < 4 * hour:
        return [hour]
    elif 30 * 60 <= lead < hour:
        return [30 * 60]
    else:
        # If less than 30 minutes away, attempt a 10-minute warning; if not possible, immediate
        return [10 * 60] if lead > 10 * 60 else [max(lead - 60, 60)]

def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")

def discord_ts(epoch: int, style: str = "F") -> str:
    # <t:epoch:style>
    return f"<t:{epoch}:{style}>"

class RemindersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._reminder_loop.start()
        self._digest_loop.start()

    def cog_unload(self):
        self._reminder_loop.cancel()
        self._digest_loop.cancel()

    projects = app_commands.Group(name="project", description="Project deadlines and reminders")
    deadlines = app_commands.Group(name="deadlines", description="Deadline settings and digest")

    @projects.command(name="create", description="Create a project deadline and auto-schedule reminders")
    @app_commands.describe(
        name="Project name",
        due="Due date/time in 'YYYY-MM-DD HH:MM' (server timezone) unless timezone specified",
        role="Role to mention for reminders",
        channel="Channel to post reminders",
        description="Optional description",
        timezone="Timezone like 'UTC' or 'America/New_York'",
        custom_offsets="Custom reminder offsets e.g. '3d,2d,24h' (overrides defaults)"
    )
    async def project_create(
        self,
        interaction: discord.Interaction,
        name: str,
        due: str,
        role: discord.Role,
        channel: discord.TextChannel,
        description: Optional[str] = None,
        timezone: Optional[str] = None,
        custom_offsets: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild_id = interaction.guild_id
        if guild_id is None:
            return await interaction.followup.send("This command must be used in a server.", ephemeral=True)

        # Resolve timezone: provided -> config -> UTC
        cfg = self.bot.db.get_config(guild_id) or {}
        tz = timezone or (cfg["timezone"] if cfg else "UTC")
        tzinfo = parse_timezone(tz)

        # Parse due string in provided timezone
        try:
            due_local = datetime.strptime(due, "%Y-%m-%d %H:%M").replace(tzinfo=tzinfo)
        except ValueError:
            return await interaction.followup.send("Invalid due format. Use 'YYYY-MM-DD HH:MM' (24h).", ephemeral=True)

        due_utc = int(due_local.timestamp())
        now_utc = int(datetime.now(tzinfo).timestamp())

        if due_utc <= now_utc + 60:
            return await interaction.followup.send("Due time must be at least 1 minute in the future.", ephemeral=True)

        # Compute offsets
        try:
            if custom_offsets:
                offsets = parse_offset_str(custom_offsets)
            else:
                offsets = default_offsets(now_utc, due_utc)
        except ValueError as e:
            return await interaction.followup.send(str(e), ephemeral=True)

        # Compute reminder timestamps (must be before due and after now)
        reminder_ts = []
        for off in offsets:
            ts = due_utc - off
            if ts > now_utc + 30:  # at least 30 seconds from now
                reminder_ts.append(ts)
        # Always ensure at least one reminder; if none valid, try 5 minutes before due
        if not reminder_ts:
            fallback = max(due_utc - 300, now_utc + 60)
            if fallback < due_utc:
                reminder_ts.append(fallback)

        # Insert project
        cur = self.bot.db.execute(
            "INSERT INTO projects (guild_id, name, description, due_ts, tz, role_id, channel_id, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                name,
                description or "",
                due_utc,
                tz,
                role.id,
                channel.id,
                interaction.user.id,
                int(time.time()),
            ),
        )
        project_id = cur.lastrowid

        # Insert reminders
        rows = [(project_id, ts, 0, 1 if custom_offsets else 0, None) for ts in reminder_ts]
        self.bot.db.executemany(
            "INSERT INTO reminders (project_id, remind_ts, sent, custom, message) VALUES (?, ?, ?, ?, ?)",
            rows,
        )

        # Compose summary
        lines = [
            f"Project created: {name}",
            f"Due: {discord_ts(due_utc, 'F')} ({tz})",
            f"Role: {role.mention}",
            f"Channel: {channel.mention}",
            f"Reminders: {', '.join(discord_ts(ts, 'R') for ts in reminder_ts)}",
        ]
        if description:
            lines.append(f"Description: {description}")

        await interaction.followup.send("\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @projects.command(name="list", description="List project deadlines")
    async def project_list(self, interaction: discord.Interaction):
        # Public so everyone can see
        await interaction.response.defer()
        guild_id = interaction.guild_id
        rows = self.bot.db.query(
            "SELECT p.id, p.name, p.due_ts, p.tz, p.role_id, p.channel_id "
            "FROM projects p WHERE p.guild_id = ? ORDER BY p.due_ts ASC",
            (guild_id,),
        )
        if not rows:
            return await interaction.followup.send("No projects found.", allowed_mentions=discord.AllowedMentions.none())

        out = []
        for r in rows:
            out.append(f"#{r['id']}: {r['name']} — due {discord_ts(r['due_ts'],'F')} ({r['tz']})")
        await interaction.followup.send("\n".join(out), allowed_mentions=discord.AllowedMentions.none())

    @projects.command(name="add-reminder", description="Add a custom reminder offset to a project")
    @app_commands.describe(project_id="Project ID", offset="Offset like '3d' or '4h'")
    async def project_add_reminder(self, interaction: discord.Interaction, project_id: int, offset: str):
        await interaction.response.defer(ephemeral=True)
        proj = self.bot.db.query_one("SELECT * FROM projects WHERE id = ? AND guild_id = ?", (project_id, interaction.guild_id))
        if not proj:
            return await interaction.followup.send("Project not found.", ephemeral=True)
        try:
            offs = parse_offset_str(offset)
        except ValueError as e:
            return await interaction.followup.send(str(e), ephemeral=True)
        due_utc = proj["due_ts"]
        now_utc = int(time.time())
        inserted = []
        for off in offs:
            ts = due_utc - off
            if ts > now_utc + 30:
                self.bot.db.execute("INSERT INTO reminders (project_id, remind_ts, sent, custom, message) VALUES (?, ?, 0, 1, NULL)", (project_id, ts))
                inserted.append(ts)
        if not inserted:
            return await interaction.followup.send("No valid reminders could be added (too close to due).", ephemeral=True)
        await interaction.followup.send(f"Added reminders at: {', '.join(discord_ts(ts, 'F') for ts in inserted)}", ephemeral=True)

    @projects.command(name="delete", description="Delete a project and its reminders")
    async def project_delete(self, interaction: discord.Interaction, project_id: int):
        await interaction.response.defer(ephemeral=True)
        row = self.bot.db.query_one("SELECT id FROM projects WHERE id = ? AND guild_id = ?", (project_id, interaction.guild_id))
        if not row:
            return await interaction.followup.send("Project not found.", ephemeral=True)
        self.bot.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await interaction.followup.send(f"Deleted project #{project_id}.", ephemeral=True)

    @deadlines.command(name="configure", description="Set deadlines digest channel and daily time")
    @app_commands.describe(
        channel="Channel to post daily deadlines digest",
        time_hhmm="Daily time HH:MM (24h) in your guild timezone",
        timezone="Timezone like 'UTC' or 'Asia/Singapore'"
    )
    async def deadlines_configure(self, interaction: discord.Interaction, channel: discord.TextChannel, time_hhmm: str, timezone: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        # Validate time string
        try:
            datetime.strptime(time_hhmm, "%H:%M")
        except ValueError:
            return await interaction.followup.send("Invalid time format. Use HH:MM (24h).", ephemeral=True)
        if timezone:
            # Validate tz
            _ = parse_timezone(timezone)
        self.bot.db.upsert_config(interaction.guild_id, deadlines_channel_id=channel.id, deadlines_digest_time=time_hhmm, timezone=timezone or (self.bot.db.get_config(interaction.guild_id)["timezone"] if self.bot.db.get_config(interaction.guild_id) else "UTC"))
        await interaction.followup.send(f"Deadlines digest configured for {channel.mention} at {time_hhmm} ({timezone or 'unchanged timezone'}).", ephemeral=True)

    @deadlines.command(name="timezone", description="Set guild timezone (affects due parsing and digests)")
    @app_commands.describe(timezone="Timezone like 'UTC' or 'America/Los_Angeles'")
    async def deadlines_timezone(self, interaction: discord.Interaction, timezone: str):
        await interaction.response.defer(ephemeral=True)
        _ = parse_timezone(timezone)
        self.bot.db.upsert_config(interaction.guild_id, timezone=timezone)
        await interaction.followup.send(f"Timezone updated to {timezone}.", ephemeral=True)

    @tasks.loop(seconds=30.0)
    async def _reminder_loop(self):
        # Check for due reminders within the last minute and next 30 seconds to be robust
        now = int(time.time())
        window_start = now - 60
        window_end = now + 30
        rows = self.bot.db.query(
            "SELECT r.id as rid, r.project_id, r.remind_ts, p.name, p.due_ts, p.role_id, p.channel_id, p.description "
            "FROM reminders r JOIN projects p ON p.id = r.project_id "
            "WHERE r.sent = 0 AND r.remind_ts BETWEEN ? AND ? ORDER BY r.remind_ts ASC",
            (window_start, window_end),
        )
        if not rows:
            return
        for r in rows:
            try:
                channel = self.bot.get_channel(r["channel_id"])
                if not isinstance(channel, discord.TextChannel):
                    # try fetch
                    channel = await self.bot.fetch_channel(r["channel_id"])
                role_mention = f"<@&{r['role_id']}>"
                due_str = discord_ts(r["due_ts"], "F")
                embed = discord.Embed(
                    title=f"Reminder: {r['name']}",
                    description=r["description"] or "",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Due", value=due_str, inline=True)
                embed.set_footer(text="Stay on track!")
                await channel.send(
                    content=f"{role_mention} Reminder for '{r['name']}'",
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )
                self.bot.db.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (r["rid"],))
            except Exception as e:
                # Log and mark as sent to avoid tight loop
                print(f"Failed to send reminder {r['rid']}: {e}")
                self.bot.db.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (r["rid"],))

    @_reminder_loop.before_loop
    async def _before_reminder_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1.0)
    async def _digest_loop(self):
        # Post daily digest at configured time per guild
        await self.bot.wait_until_ready()
        # Use timezone.utc so we don't depend on tzdata for UTC
        now_utc = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            cfg = self.bot.db.get_config(guild.id)
            if not cfg or not cfg["deadlines_channel_id"]:
                continue
            tz = parse_timezone(cfg["timezone"] if cfg["timezone"] else "UTC")
            hhmm = (cfg["deadlines_digest_time"] or "09:00")
            try:
                digest_h, digest_m = map(int, hhmm.split(":"))
            except Exception:
                digest_h, digest_m = 9, 0
            guild_now = now_utc.astimezone(tz)
            # Fire at the minute match
            if guild_now.hour == digest_h and guild_now.minute == digest_m:
                # Debounce per process per day
                key = f"{guild.id}:{guild_now.strftime('%Y-%m-%d')}"
                if getattr(self, "_digest_cache", None) is None:
                    self._digest_cache = set()
                if key in self._digest_cache:
                    continue
                self._digest_cache.add(key)
                try:
                    channel = self.bot.get_channel(cfg["deadlines_channel_id"])
                    if not isinstance(channel, discord.TextChannel):
                        channel = await self.bot.fetch_channel(cfg["deadlines_channel_id"])
                    # Upcoming 7 days
                    start_ts = int(guild_now.timestamp())
                    end_ts = int((guild_now + timedelta(days=7)).timestamp())
                    rows = self.bot.db.query(
                        "SELECT id, name, due_ts, tz FROM projects WHERE guild_id = ? AND due_ts BETWEEN ? AND ? ORDER BY due_ts ASC",
                        (guild.id, start_ts, end_ts),
                    )
                    if not rows:
                        continue
                    embed = discord.Embed(
                        title="Upcoming Deadlines (next 7 days)",
                        color=discord.Color.blurple(),
                        timestamp=guild_now,
                    )
                    for r in rows:
                        embed.add_field(
                            name=f"#{r['id']}: {r['name']}",
                            value=f"Due {discord_ts(r['due_ts'],'F')} ({r['tz']})",
                            inline=False,
                        )
                    await channel.send(embed=embed)
                except Exception as e:
                    print(f"Failed to send digest in guild {guild.id}: {e}")

    @_digest_loop.before_loop
    async def _before_digest_loop(self):
        await self.bot.wait_until_ready()