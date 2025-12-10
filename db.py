# db.py
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS config (
  guild_id INTEGER PRIMARY KEY,
  timezone TEXT DEFAULT 'UTC',
  announcer_user_id INTEGER,
  announce_role_id INTEGER,
  announce_channel_id INTEGER,
  deadlines_channel_id INTEGER,
  deadlines_digest_time TEXT DEFAULT '09:00' -- HH:MM 24h in guild timezone
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  due_ts INTEGER NOT NULL, -- epoch seconds UTC
  tz TEXT NOT NULL, -- timezone used at creation
  role_id INTEGER NOT NULL, -- role to mention
  channel_id INTEGER NOT NULL, -- channel to post reminder
  created_by INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_guild ON projects(guild_id);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  remind_ts INTEGER NOT NULL, -- epoch seconds UTC
  sent INTEGER NOT NULL DEFAULT 0,
  custom INTEGER NOT NULL DEFAULT 0,
  message TEXT,
  FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(sent, remind_ts);

CREATE TABLE IF NOT EXISTS assessments_topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  created_by INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(guild_id, name)
);

CREATE TABLE IF NOT EXISTS assessments_qas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  FOREIGN KEY(topic_id) REFERENCES assessments_topics(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_assess_q_topic ON assessments_qas(topic_id);

CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER NOT NULL,
  weekday INTEGER NOT NULL, -- 0=Mon ... 6=Sun to match Python weekday()
  subject TEXT NOT NULL,
  start_time TEXT NOT NULL, -- "HH:MM"
  end_time TEXT NOT NULL,   -- "HH:MM"
  notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_sched_guild_day ON schedules(guild_id, weekday);
"""

class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self._conn:
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: Tuple = ()) -> sqlite3.Cursor:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        self._conn.commit()
        return cur

    def executemany(self, sql: str, seq_of_params: Iterable[Tuple]) -> sqlite3.Cursor:
        cur = self._conn.cursor()
        cur.executemany(sql, seq_of_params)
        self._conn.commit()
        return cur

    def query(self, sql: str, params: Tuple = ()) -> List[sqlite3.Row]:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    def query_one(self, sql: str, params: Tuple = ()) -> Optional[sqlite3.Row]:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()

    # Convenience helpers
    def upsert_config(self, guild_id: int, **kwargs):
        # Read existing
        row = self.query_one("SELECT guild_id FROM config WHERE guild_id = ?", (guild_id,))
        if row:
            sets = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            params = tuple(kwargs.values()) + (guild_id,)
            self.execute(f"UPDATE config SET {sets} WHERE guild_id = ?", params)
        else:
            cols = ["guild_id"] + list(kwargs.keys())
            placeholders = ",".join(["?"] * len(cols))
            params = (guild_id,) + tuple(kwargs.values())
            self.execute(f"INSERT INTO config ({','.join(cols)}) VALUES ({placeholders})", params)

    def get_config(self, guild_id: int) -> sqlite3.Row:
        row = self.query_one("SELECT * FROM config WHERE guild_id = ?", (guild_id,))
        return row

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass