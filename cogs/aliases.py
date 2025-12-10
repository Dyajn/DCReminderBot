import os
import json
from typing import Dict, Optional, Tuple, List, Union

import discord
from discord.ext import commands
from discord import app_commands


AliasTargetPath = str  # e.g., "project.create", "assess.qa-get", or "ping"


class AliasesCog(commands.Cog):
    """
    Registers alias slash commands based on ./config/command-aliases.json.
    You can:
      - Create multiple alias groups (e.g., /deadline, /ann)
      - Add alias subcommands under those groups (e.g., /deadline add -> project.create)
      - Create top-level single-command aliases (e.g., /pong -> ping)

    Aliases call the original command callbacks, so you keep the same parameters/validation.
    Edit the JSON, restart the bot, and run /admin sync to refresh.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Track created aliases so we can remove on unload
        self._alias_groups: Dict[str, app_commands.Group] = {}
        self._alias_commands: List[app_commands.Command] = []

    # ---------- Config loading ----------

    def _load_config(self) -> Dict:
        """
        Load alias configuration.
        Schema (recommended):
        {
          "groups": {
            "deadline": {
              "description": "Deadline command aliases",
              "commands": {
                "add": "project.create",
                "list": "project.list",
                "delete": "project.delete",
                "add-reminder": "project.add-reminder"
              }
            },
            "assessments": {
              "description": "Assessment aliases",
              "commands": {
                "get-all": "assess.qa-get",
                "topics": "assess.topic-list",
                "add-bulk": "assess.topic-upsert"
              }
            }
          },
          "singles": {
            "pong": "ping"
          }
        }

        Backward-compatible legacy format also supported:
        {
          "group": "deadline",
          "description": "Deadline command aliases",
          "commands": { ... }
        }
        """
        cfg_path = os.path.join(os.getcwd(), "config", "command-aliases.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

        # Normalize legacy format -> groups
        if isinstance(data, dict) and "group" in data and "commands" in data:
            grp_name = data.get("group") or "aliases"
            groups = {
                grp_name: {
                    "description": data.get("description", f"Aliases for {grp_name}"),
                    "commands": data.get("commands", {}),
                }
            }
            return {"groups": groups}

        return data if isinstance(data, dict) else {}

    # ---------- Command resolution ----------

    def _resolve_target(self, path: AliasTargetPath) -> Optional[app_commands.Command]:
        """
        Resolve a path like "project.create" or "ping" to an existing app_commands.Command.
        Returns None if not found.
        """
        # Top-level command
        if "." not in path:
            cmd = self.bot.tree.get_command(path)  # may be a Group or Command
            if isinstance(cmd, app_commands.Command):
                return cmd
            # If it's a group, we can't alias a group as a top-level command directly
            return None

        group_name, sub_name = path.split(".", 1)
        group = self.bot.tree.get_command(group_name)
        if not isinstance(group, app_commands.Group):
            return None
        # Find child command by name
        for child in group.commands:
            if child.name == sub_name and isinstance(child, app_commands.Command):
                return child
        return None

    def _make_alias_command(self, alias_name: str, target: app_commands.Command, description_prefix: str = "Alias of") -> app_commands.Command:
        """
        Create a new app_commands.Command that reuses the target's callback.
        Keeps the same parameters, choice metadata, and validation because those
        are derived from the callback's signature and annotations.
        """
        desc = target.description or ""
        full_desc = f"{description_prefix} /{self._target_path_for(target)}"
        if desc:
            full_desc = f"{full_desc} — {desc}"
        # Use the same bound callback as the target
        cb = target.callback
        return app_commands.Command(name=alias_name, description=full_desc[:100], callback=cb)

    def _target_path_for(self, cmd: app_commands.Command) -> str:
        # Try to reconstruct path for display (group.sub or top-level)
        parent = getattr(cmd, "parent", None)
        if isinstance(parent, app_commands.Group):
            return f"{parent.name}.{cmd.name}"
        return cmd.name

    # ---------- Lifecycle ----------

    async def cog_load(self):
        cfg = self._load_config()
        if not cfg:
            return

        # Build groups
        groups_cfg: Dict[str, Dict[str, Dict[str, AliasTargetPath]]] = cfg.get("groups", {}) or {}
        for group_name, g in groups_cfg.items():
            description = g.get("description") or f"Aliases for {group_name}"
            grp = app_commands.Group(name=group_name, description=description)
            commands_map: Dict[str, AliasTargetPath] = g.get("commands", {}) or {}

            for alias_sub, target_path in commands_map.items():
                target = self._resolve_target(target_path)
                if not target:
                    # Skip unknown targets silently (avoid crashing startup if a target is renamed)
                    continue
                alias_cmd = self._make_alias_command(alias_sub, target)
                try:
                    grp.add_command(alias_cmd)
                except Exception:
                    # Name conflict inside this group; skip
                    continue

            # Register group if it has at least one subcommand
            if grp.commands:
                self.bot.tree.add_command(grp)
                self._alias_groups[group_name] = grp

        # Build single (top-level) command aliases
        singles_cfg: Dict[str, AliasTargetPath] = cfg.get("singles", {}) or {}
        for alias_name, target_path in singles_cfg.items():
            target = self._resolve_target(target_path)
            if not target:
                continue
            alias_cmd = self._make_alias_command(alias_name, target)
            try:
                self.bot.tree.add_command(alias_cmd)
                self._alias_commands.append(alias_cmd)
            except Exception:
                # Name conflict at top-level; skip
                continue

    async def cog_unload(self):
        # Remove added groups
        for name, grp in list(self._alias_groups.items()):
            try:
                self.bot.tree.remove_command(name)
            except Exception:
                pass
        self._alias_groups.clear()

        # Remove added top-level alias commands
        for cmd in list(self._alias_commands):
            try:
                self.bot.tree.remove_command(cmd.name)
            except Exception:
                pass
        self._alias_commands.clear()