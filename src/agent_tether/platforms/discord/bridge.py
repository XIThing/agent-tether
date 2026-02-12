"""Discord bridge implementation.

Uses discord.py for real-time messaging. Threads are Discord channel
threads. Approvals use text-based commands. Supports optional pairing
for user authorization.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from agent_tether.formatting import format_tool_input
from agent_tether.models import ApprovalRequest, CommandDef, Handlers
from agent_tether.platforms.base import BridgeBase
from agent_tether.platforms.discord.pairing import (
    PairingState,
    load_or_create,
    save as save_pairing_state,
)

logger = logging.getLogger("agent_tether.discord")

_MSG_LIMIT = 2000  # Discord message limit


class DiscordBridge(BridgeBase):
    """Discord bridge using channel threads.

    Args:
        bot_token: Discord bot token.
        channel_id: Discord channel ID for the control channel.
        handlers: Event handler callbacks.
        commands: Custom command definitions.
        disabled_commands: Built-in commands to disable.
        data_dir: Directory for persistent state.
        auto_approve_duration: Auto-approve timer duration (seconds).
        never_auto_approve: Tool prefixes never auto-approved.
        flush_delay: Batch notification delay (seconds).
        error_debounce_seconds: Min seconds between error notifications.
        require_pairing: Require pairing before using the bot.
        pairing_code: Optional fixed pairing code.
        allowed_user_ids: Set of always-authorized Discord user IDs.
    """

    def __init__(
        self,
        bot_token: str,
        channel_id: int | None = None,
        *,
        handlers: Handlers,
        commands: dict[str, CommandDef] | None = None,
        disabled_commands: set[str] | None = None,
        data_dir: str | None = None,
        auto_approve_duration: int = 30 * 60,
        never_auto_approve: set[str] | frozenset[str] | None = None,
        flush_delay: float = 1.5,
        error_debounce_seconds: int = 0,
        require_pairing: bool = False,
        pairing_code: str | None = None,
        allowed_user_ids: set[int] | None = None,
    ) -> None:
        super().__init__(
            handlers,
            commands=commands,
            disabled_commands=disabled_commands,
            data_dir=data_dir,
            auto_approve_duration=auto_approve_duration,
            never_auto_approve=never_auto_approve,
            flush_delay=flush_delay,
            error_debounce_seconds=error_debounce_seconds,
            command_prefix="!",
        )
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._client: Any = None
        self._require_pairing = require_pairing
        self._allowed_user_ids = allowed_user_ids or set()

        # Pairing state
        self._pairing_state_path = self._data_dir / "discord_pairing.json"
        self._pairing_state: PairingState | None = None
        self._paired_user_ids: set[int] = set()
        self._pairing_code: str | None = None

        if require_pairing or pairing_code or not channel_id:
            self._pairing_state = load_or_create(
                path=self._pairing_state_path,
                fixed_code=pairing_code,
            )
            self._paired_user_ids = set(self._pairing_state.paired_user_ids)
            self._pairing_code = self._pairing_state.pairing_code
            if not self._channel_id and self._pairing_state.control_channel_id:
                self._channel_id = self._pairing_state.control_channel_id

        # Thread ID mapping: thread_id (str) → discord thread channel ID (int)
        self._thread_ids: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def _is_authorized(self, user_id: int | None) -> bool:
        if not user_id:
            return False
        if user_id in self._allowed_user_ids:
            return True
        if user_id in self._paired_user_ids:
            return True
        if not self._require_pairing and not self._allowed_user_ids and not self._paired_user_ids:
            return True
        return False

    # ------------------------------------------------------------------
    # Platform lifecycle
    # ------------------------------------------------------------------

    async def _platform_start(self) -> None:
        import discord

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            logger.info("Discord client ready: %s", self._client.user)

        @self._client.event
        async def on_message(message):
            await self._handle_discord_message(message)

        asyncio.create_task(self._client.start(self._bot_token))
        logger.info("Discord bridge starting (channel=%s)", self._channel_id)

        if not self._channel_id and self._pairing_code:
            logger.warning(
                "Discord not configured. Run !setup %s in the desired channel.",
                self._pairing_code,
            )
        elif self._require_pairing and self._pairing_code:
            logger.warning("Discord pairing enabled. Code: %s", self._pairing_code)

    async def _platform_stop(self) -> None:
        if self._client:
            await self._client.close()
        logger.info("Discord bridge stopped")

    # ------------------------------------------------------------------
    # Platform send
    # ------------------------------------------------------------------

    async def _platform_send(self, thread_id: str, text: str, **kwargs) -> None:
        if not self._client:
            return
        tid = int(thread_id)
        thread = self._client.get_channel(tid)
        if not thread:
            return
        try:
            for i in range(0, len(text), _MSG_LIMIT):
                await thread.send(text[i : i + _MSG_LIMIT])
        except Exception:
            logger.exception("Failed to send Discord message (thread=%s)", thread_id)

    # ------------------------------------------------------------------
    # Platform threads
    # ------------------------------------------------------------------

    async def _platform_create_thread(self, name: str) -> str:
        if not self._client:
            raise RuntimeError("Discord bridge not started")
        if not self._channel_id:
            raise RuntimeError("Discord channel not configured")

        channel = self._client.get_channel(self._channel_id)
        if not channel:
            raise RuntimeError(f"Discord channel {self._channel_id} not found")

        thread = await channel.create_thread(
            name=name[:100],
            auto_archive_duration=1440,
        )
        thread_id = str(thread.id)
        self._thread_ids[thread_id] = thread.id

        try:
            await thread.send(
                "Session thread. Send a message to provide input. "
                "Use `!stop` to interrupt, `!usage` for token usage."
            )
        except Exception:
            pass

        return thread_id

    # ------------------------------------------------------------------
    # Platform approval UI (text-based)
    # ------------------------------------------------------------------

    async def _platform_send_approval(
        self, thread_id: str, request: ApprovalRequest, formatted_description: str
    ) -> None:
        if not self._client:
            return

        text = (
            f"**⚠️ Approval Required**\n\n**{request.title}**\n\n{formatted_description}\n\n"
            "Reply with `allow`/`proceed`, `deny`/`cancel`, `deny: <reason>`, "
            "`allow all`, or `allow {{tool}}`."
        )
        await self._platform_send(thread_id, text)

    async def _platform_send_choice(self, thread_id: str, request: ApprovalRequest) -> None:
        if not self._client:
            return

        options = "\n".join(f"{i}. {o}" for i, o in enumerate(request.options, start=1))
        text = (
            f"⚠️ **{request.title}**\n\n{request.description}\n\n{options}\n\n"
            "Reply with a number (e.g. `1`) or an exact option label."
        )
        await self._platform_send(thread_id, text)

    # ------------------------------------------------------------------
    # Auto-approve batch (Discord formatting)
    # ------------------------------------------------------------------

    async def _send_auto_approve_batch(self, thread_id: str, items: list[tuple[str, str]]) -> None:
        if not self._client:
            return

        if len(items) == 1:
            tool_name, reason = items[0]
            text = f"✅ **{tool_name}** — auto-approved ({reason})"
        else:
            lines = [f"✅ Auto-approved {len(items)} tools:"]
            for tool_name, _reason in items:
                lines.append(f"  • {tool_name}")
            lines.append(f"*({items[0][1]})*")
            text = "\n".join(lines)

        await self._platform_send(thread_id, text)

    # ------------------------------------------------------------------
    # Discord message handler
    # ------------------------------------------------------------------

    async def _handle_discord_message(self, message: Any) -> None:
        import discord

        if message.author.bot:
            return

        text = message.content.strip()
        if not text:
            return

        user_id = getattr(message.author, "id", None)

        # Setup/pairing always allowed
        if text.lower().startswith(("!pair", "!setup")):
            await self._handle_pairing_command(message, text)
            return

        # Messages in threads
        if isinstance(message.channel, discord.Thread):
            thread_id = str(message.channel.id)

            if text.startswith("!"):
                if not self._is_authorized(user_id):
                    await message.channel.send("🔒 Not authorized.")
                    return
                await self._dispatch_message(thread_id, text, message.author.name)
                return

            if not self._is_authorized(user_id):
                await message.channel.send("🔒 Not authorized.")
                return

            await self._dispatch_message(thread_id, text, message.author.name)
            return

        # Top-level commands in the control channel
        if self._channel_id and message.channel.id == self._channel_id and text.startswith("!"):
            if not self._is_authorized(user_id):
                await message.channel.send("🔒 Not authorized.")
                return
            await self._handle_top_level_command(message, text)
            return

        # Allow !setup in any channel if not configured
        if not self._channel_id and text.lower().startswith("!setup"):
            await self._handle_pairing_command(message, text)

    async def _handle_top_level_command(self, message: Any, text: str) -> None:
        """Handle a command in the main channel."""
        without_prefix = text[1:].strip()
        parts = without_prefix.split(None, 1)
        if not parts:
            return
        cmd_name = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        cmd = self._commands.get(cmd_name)
        if cmd:
            try:
                reply = await cmd.handler("", args)
                if reply:
                    await message.channel.send(reply[:_MSG_LIMIT])
            except Exception:
                logger.exception("Command !%s failed", cmd_name)
                await message.channel.send(f"Command failed: !{cmd_name}")
            return

        if self._handlers.on_command:
            try:
                reply = await self._handlers.on_command("", cmd_name, args)
                if reply:
                    await message.channel.send(reply[:_MSG_LIMIT])
            except Exception:
                logger.exception("Command handler failed for !%s", cmd_name)
            return

        await message.channel.send(
            f"Unknown command: !{cmd_name}\nUse !help for available commands."
        )

    # ------------------------------------------------------------------
    # Pairing commands
    # ------------------------------------------------------------------

    async def _handle_pairing_command(self, message: Any, text: str) -> None:
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "!setup":
            await self._cmd_setup(message, args)
        elif cmd == "!pair":
            await self._cmd_pair(message, args)
        elif cmd == "!pair-status":
            user_id = getattr(message.author, "id", None)
            authorized = self._is_authorized(user_id)
            await message.channel.send(
                f"Pairing required: {self._require_pairing}\n"
                f"Authorized: {authorized}\n"
                f"Your user id: {user_id}"
            )

    async def _cmd_setup(self, message: Any, code: str) -> None:
        if not code:
            await message.channel.send("Usage: `!setup <code>`")
            return

        if not self._pairing_state:
            self._pairing_state = load_or_create(path=self._pairing_state_path, fixed_code=None)
            self._pairing_code = self._pairing_state.pairing_code

        if not self._pairing_code or code != self._pairing_code:
            await message.channel.send("Invalid setup code.")
            return

        channel_id = getattr(message.channel, "id", None)
        if not channel_id:
            await message.channel.send("Could not read channel id.")
            return

        self._channel_id = int(channel_id)
        self._pairing_state.control_channel_id = self._channel_id

        user_id = getattr(message.author, "id", None)
        if user_id:
            self._paired_user_ids.add(int(user_id))
            self._pairing_state.paired_user_ids = set(self._paired_user_ids)

        save_pairing_state(path=self._pairing_state_path, state=self._pairing_state)
        await message.channel.send("✅ Setup complete. This channel is now the control channel.")

    async def _cmd_pair(self, message: Any, code: str) -> None:
        if not code:
            await message.channel.send("Usage: `!pair <code>`")
            return

        if not self._pairing_state:
            self._pairing_state = load_or_create(path=self._pairing_state_path, fixed_code=None)
            self._pairing_code = self._pairing_state.pairing_code

        if not self._pairing_code or code != self._pairing_code:
            await message.channel.send("Invalid pairing code.")
            return

        user_id = getattr(message.author, "id", None)
        if not user_id:
            await message.channel.send("Could not read your user id.")
            return

        self._paired_user_ids.add(int(user_id))
        self._pairing_state.paired_user_ids = set(self._paired_user_ids)
        save_pairing_state(path=self._pairing_state_path, state=self._pairing_state)
        await message.channel.send("✅ Paired. You can now use commands.")
