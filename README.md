# Discord Scheduler Bot

A Discord bot that provides:
- Auto scheduling and reminders for project deadlines
- Announcements (restricted to a designated controller)
- Assessment answers (topics with Q&A)
- Weekly schedules per guild
- Daily deadline digest

## Features

1) Project Deadlines and Auto-Reminders
- Create a project with a due date/time, a target role to ping, and a channel to post reminders.
- Default reminder logic:
  - If set ≥ 3 days before due: reminders at 3d, 2d, 1d before due
  - If set 2–3 days before due: reminder at 24h before due
  - If set 12h–2 days before due: reminder at 4h before due
  - Sensible fallbacks for tighter windows (2h, 1h, 30m, or 10m)
- You can also specify custom offsets (e.g., "3d, 12h, 30m").
- Daily digest lists upcoming deadlines for the next 7 days.

2) Announcements
- One designated controller (or server admins/managers by default) can post announcements pinging a specific role in a specified channel.
- Set default role and channel for announcements.

3) Assessment Answers
- Create topics and attach Q&A pairs.
- Retrieve answers by exact match or substring match.

4) Weekly Schedule
- Admins can set entries per day (subject, start-end time, notes).
- Display daily or weekly schedule.

## Requirements

- Python 3.10+
- A Discord bot token with the following intents:
  - Guilds (enabled by default)
- Bot must have permissions to:
  - Send Messages
  - Embed Links
  - Mention Roles (to ping roles)

## Setup

1) Create and invite your bot:
- Go to https://discord.com/developers/applications
- Create an application and bot, copy the token.
- Enable MESSAGE CONTENT intent is not needed. Guilds intent is sufficient.
- Invite the bot to your server with permissions to send messages and mention roles.

2) Clone and install dependencies:
- python -m venv .venv
- source .venv/bin/activate  (Windows: .venv\Scripts\activate)
- pip install -r requirements.txt

3) Configure environment variables:
- DISCORD_TOKEN=your_bot_token
- Optional: DB_PATH=custom/path/to/bot.db (defaults to data/bot.db)

4) Run the bot:
- cd bot
- python main.py

The bot will create an SQLite DB at data/bot.db.

## Commands (Slash)

- /deadlines timezone timezone:"America/New_York"
  - Set guild timezone. Defaults to UTC if not set.

- /deadlines configure channel:#channel time_hhmm:"09:00" timezone:"UTC"
  - Set deadlines digest posting channel and time (HH:MM in your guild timezone).

- /project create
  - name:"Project X"
  - due:"YYYY-MM-DD HH:MM" (interpreted in guild timezone unless you pass the timezone argument)
  - role:@RoleToPing
  - channel:#reminders
  - description:"Optional"
  - timezone:"America/Los_Angeles" (optional)
  - custom_offsets:"3d,2d,24h" (optional, overrides defaults)

- /project list
  - Lists all projects in the guild.

- /project add-reminder project_id:ID offset:"4h"
  - Adds a custom reminder offset to a project.

- /project delete project_id:ID
  - Deletes a project and its reminders.

- /announce set-controller user:@User
  - Sets the single user allowed to post announcements.

- /announce set-defaults role:@Role channel:#channel
  - Sets default role and channel to be used by /announce post.

- /announce post message:"your text" role:@Role? channel:#channel?
  - Posts an announcement (restricted to controller or server managers).
  - If role/channel are not provided, uses defaults.

- /assess topic-create name:"Assessment Lab 2"
  - Creates a topic.

- /assess topic-list
  - Lists topics.

- /assess topic-delete name:"Assessment Lab 2"
  - Deletes a topic and its Q&A.

- /assess qa-add topic:"Assessment Lab 2" question:"Q1" answer:"The answer..."
  - Adds a Q&A to the topic.

- /assess qa-get topic:"Assessment Lab 2" query:"Q1"
  - Retrieves an answer by exact or partial question match.

- /schedule add day:"Mon" subject:"Physics" start_hhmm:"09:00" end_hhmm:"11:00" notes:"Lab"
  - Adds a schedule entry.

- /schedule list
  - Shows the weekly schedule.

- /schedule list day:"Mon"
  - Shows a single day's schedule.

- /schedule clear day:"Tue"
  - Clears a day's schedule.

## Notes

- Timezones: the bot uses IANA timezones (e.g., "UTC", "Europe/London", "Asia/Singapore", "America/Los_Angeles").
- Due date format: "YYYY-MM-DD HH:MM" (24-hour clock).
- Role Pings: The bot mentions roles using <@&role_id>; ensure the bot role is allowed to mention the target role.
- Digest De-duplication: Digests are de-duplicated per bot process per day. If you run multiple instances, you may get duplicates.

## Extending

- Add role-based restrictions around assessment and schedule commands if needed.
- Enhance backups by copying data/bot.db periodically.
- Add per-project custom messages by updating the "message" column in reminders.