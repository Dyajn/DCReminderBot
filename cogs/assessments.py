import json
import re
from typing import List, Optional, Tuple

import discord
from discord.ext import commands
from discord import app_commands


def _parse_qas_text(qas_text: str) -> List[Tuple[str, str]]:
    """
    Parse a simple text format into (question, answer) pairs.
    Supported patterns:
      Q: question
      A: answer

    You can repeat this pattern multiple times. Blank lines are allowed.
    """
    lines = [ln.rstrip() for ln in qas_text.splitlines()]
    qas: List[Tuple[str, str]] = []
    q_cur: Optional[str] = None
    a_cur: Optional[str] = None

    def flush():
        nonlocal q_cur, a_cur
        if q_cur is not None and a_cur is not None:
            qas.append((q_cur.strip(), a_cur.strip()))
        q_cur, a_cur = None, None

    for ln in lines:
        if not ln.strip():
            # blank line separates entries
            flush()
            continue
        if ln.lower().startswith("q:"):
            flush()
            q_cur = ln[2:].strip()
        elif ln.lower().startswith("a:"):
            a_cur = ln[2:].strip()
        else:
            # continuation line: attach to the last present (A if exists else Q)
            if a_cur is not None:
                a_cur += "\n" + ln
            elif q_cur is not None:
                q_cur += "\n" + ln
            else:
                # First line without Q:/A: -> treat as question start
                q_cur = ln.strip()
    flush()
    # filter empties
    return [(q, a) for (q, a) in qas if q and a]


class AssessmentsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="assess", description="Assessment topics and Q&A")

    @group.command(name="topic-create", description="Create an assessment topic")
    async def topic_create(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        try:
            self.bot.db.execute(
                "INSERT INTO assessments_topics (guild_id, name, created_by, created_at) VALUES (?, ?, ?, strftime('%s','now'))",
                (interaction.guild_id, name, interaction.user.id),
            )
            await interaction.followup.send(f"Topic created: {name}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Failed to create topic (maybe it exists already?): {e}", ephemeral=True)

    @group.command(name="topic-upsert", description="Create or replace a topic with multiple Q&A in one go")
    @app_commands.describe(
        name="Topic name",
        replace_existing="If topic exists, replace all Q&A (True) or append (False)",
        qas_text="Bulk Q&A text using 'Q: ...' and 'A: ...' pairs (or use qas_json)",
        qas_json="JSON attachment: [{\"question\":\"...\",\"answer\":\"...\"}, ...]"
    )
    async def topic_upsert(
        self,
        interaction: discord.Interaction,
        name: str,
        replace_existing: bool = True,
        qas_text: Optional[str] = None,
        qas_json: Optional[discord.Attachment] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Resolve or create topic
        topic = self.bot.db.query_one(
            "SELECT id FROM assessments_topics WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, name),
        )
        if not topic:
            self.bot.db.execute(
                "INSERT INTO assessments_topics (guild_id, name, created_by, created_at) VALUES (?, ?, ?, strftime('%s','now'))",
                (interaction.guild_id, name, interaction.user.id),
            )
            topic = self.bot.db.query_one(
                "SELECT id FROM assessments_topics WHERE guild_id = ? AND name = ?",
                (interaction.guild_id, name),
            )
        topic_id = topic["id"]

        # Acquire Q&A data
        qas: List[Tuple[str, str]] = []
        if qas_json:
            try:
                raw = await qas_json.read()
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, list):
                    raise ValueError("JSON must be a list of {question, answer} objects")
                for item in data:
                    q = item.get("question") or item.get("q")
                    a = item.get("answer") or item.get("a")
                    if q and a:
                        qas.append((str(q), str(a)))
            except Exception as e:
                return await interaction.followup.send(f"Invalid JSON: {e}", ephemeral=True)
        elif qas_text:
            qas = _parse_qas_text(qas_text)
        else:
            return await interaction.followup.send("Provide either qas_text or qas_json.", ephemeral=True)

        if not qas:
            return await interaction.followup.send("No valid Q&A pairs found.", ephemeral=True)

        # Replace or append
        if replace_existing:
            self.bot.db.execute("DELETE FROM assessments_qas WHERE topic_id = ?", (topic_id,))
        self.bot.db.executemany(
            "INSERT INTO assessments_qas (topic_id, question, answer) VALUES (?, ?, ?)",
            [(topic_id, q, a) for (q, a) in qas],
        )

        await interaction.followup.send(
            f"Topic '{name}' {'replaced' if replace_existing else 'updated'} with {len(qas)} Q&A.",
            ephemeral=True,
        )

    @group.command(name="topic-rename", description="Rename a topic")
    async def topic_rename(self, interaction: discord.Interaction, old_name: str, new_name: str):
        await interaction.response.defer(ephemeral=True)
        t = self.bot.db.query_one(
            "SELECT id FROM assessments_topics WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, old_name),
        )
        if not t:
            return await interaction.followup.send("Topic not found.", ephemeral=True)
        try:
            self.bot.db.execute("UPDATE assessments_topics SET name = ? WHERE id = ?", (new_name, t["id"]))
            await interaction.followup.send(f"Renamed topic to: {new_name}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Failed to rename topic: {e}", ephemeral=True)

    @group.command(name="topic-list", description="List topics")
    async def topic_list(self, interaction: discord.Interaction):
        # Public so everyone can see
        await interaction.response.defer()
        rows = self.bot.db.query(
            "SELECT id, name FROM assessments_topics WHERE guild_id = ? ORDER BY name",
            (interaction.guild_id,),
        )
        if not rows:
            return await interaction.followup.send("No topics yet.")
        embed = discord.Embed(title="Assessment Topics", color=discord.Color.blurple())
        for r in rows:
            embed.add_field(name=f"#{r['id']}", value=r["name"], inline=True)
        await interaction.followup.send(embed=embed)

    @group.command(name="topic-delete", description="Delete a topic and its Q&A")
    async def topic_delete(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        row = self.bot.db.query_one(
            "SELECT id FROM assessments_topics WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, name),
        )
        if not row:
            return await interaction.followup.send("Topic not found.", ephemeral=True)
        self.bot.db.execute("DELETE FROM assessments_topics WHERE id = ?", (row["id"],))
        await interaction.followup.send(f"Deleted topic: {name}", ephemeral=True)

    @group.command(name="qa-get", description="List all Q&A for a topic")
    async def qa_get(self, interaction: discord.Interaction, topic: str):
        # Public so everyone can see
        await interaction.response.defer()
        t = self.bot.db.query_one(
            "SELECT id FROM assessments_topics WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, topic),
        )
        if not t:
            return await interaction.followup.send("Topic not found.")
        rows = self.bot.db.query(
            "SELECT id, question, answer FROM assessments_qas WHERE topic_id = ? ORDER BY id",
            (t["id"],),
        )
        if not rows:
            return await interaction.followup.send("No Q&A in this topic yet.")

        # Chunk into multiple embeds to avoid hitting field/size limits
        MAX_FIELDS = 10
        chunk = []
        embeds = []
        for i, r in enumerate(rows, start=1):
            q = r["question"]
            a = r["answer"]
            name = f"Q{r['id']}: {q}"
            # Discord field value limit ~1024 chars; truncate if necessary
            val = a if len(a) <= 1024 else a[:1000] + "... (truncated)"
            chunk.append((name, val))
            if len(chunk) == MAX_FIELDS:
                e = discord.Embed(title=f"{topic} — Q&A", color=discord.Color.green())
                for nm, vl in chunk:
                    e.add_field(name=nm, value=vl, inline=False)
                embeds.append(e)
                chunk = []
        if chunk:
            e = discord.Embed(title=f"{topic} — Q&A", color=discord.Color.green())
            for nm, vl in chunk:
                e.add_field(name=nm, value=vl, inline=False)
            embeds.append(e)

        # Send sequentially
        first = True
        for e in embeds:
            if first:
                await interaction.followup.send(embed=e)
                first = False
            else:
                await interaction.channel.send(embed=e)

    @group.command(name="qa-add", description="Add a Q&A to a topic")
    async def qa_add(self, interaction: discord.Interaction, topic: str, question: str, answer: str):
        await interaction.response.defer(ephemeral=True)
        t = self.bot.db.query_one(
            "SELECT id FROM assessments_topics WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, topic),
        )
        if not t:
            return await interaction.followup.send("Topic not found. Create it first.", ephemeral=True)
        self.bot.db.execute(
            "INSERT INTO assessments_qas (topic_id, question, answer) VALUES (?, ?, ?)",
            (t["id"], question, answer),
        )
        await interaction.followup.send("Q&A added.", ephemeral=True)

    @group.command(name="qa-edit", description="Edit a Q&A (by ID) in a topic")
    @app_commands.describe(
        qa_id="ID of the Q&A row (see /assess qa-get list)",
        question="New question text (optional)",
        answer="New answer text (optional)"
    )
    async def qa_edit(self, interaction: discord.Interaction, topic: str, qa_id: int, question: Optional[str] = None, answer: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        if not question and not answer:
            return await interaction.followup.send("Nothing to update. Provide question and/or answer.", ephemeral=True)
        t = self.bot.db.query_one(
            "SELECT id FROM assessments_topics WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, topic),
        )
        if not t:
            return await interaction.followup.send("Topic not found.", ephemeral=True)
        row = self.bot.db.query_one(
            "SELECT id FROM assessments_qas WHERE id = ? AND topic_id = ?",
            (qa_id, t["id"]),
        )
        if not row:
            return await interaction.followup.send("Q&A not found for this topic.", ephemeral=True)
        sets = []
        params: List[str] = []
        if question:
            sets.append("question = ?")
            params.append(question)
        if answer:
            sets.append("answer = ?")
            params.append(answer)
        params.extend([qa_id])
        self.bot.db.execute(f"UPDATE assessments_qas SET {', '.join(sets)} WHERE id = ?", tuple(params))
        await interaction.followup.send("Q&A updated.", ephemeral=True)

    @group.command(name="qa-delete", description="Delete a Q&A (by ID) in a topic")
    async def qa_delete(self, interaction: discord.Interaction, topic: str, qa_id: int):
        await interaction.response.defer(ephemeral=True)
        t = self.bot.db.query_one(
            "SELECT id FROM assessments_topics WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, topic),
        )
        if not t:
            return await interaction.followup.send("Topic not found.", ephemeral=True)
        self.bot.db.execute(
            "DELETE FROM assessments_qas WHERE id = ? AND topic_id = ?",
            (qa_id, t["id"]),
        )
        await interaction.followup.send("Q&A deleted.", ephemeral=True)