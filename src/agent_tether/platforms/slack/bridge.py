"""Slack bridge implementation.

Uses slack-sdk and optionally slack-bolt (socket mode) for real-time
messaging. Threads are Slack message threads in a configured channel.
Approvals use text-based commands (``allow``, ``deny``, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_tether.formatting import format_tool_input
from agent_tether.models import ApprovalRequest, CommandDef, Handlers
from agent_tether.platforms.base import BridgeBase

logger = logging.getLogger("agent_tether.slack")

_MSG_LIMIT = 4000  # Slack soft limit for message text


class SlackBridge(BridgeBase):
    """Slack bridge using message threads.

    Args:
        bot_token: Slack bot token (xoxb-...).
        channel_id: Slack channel ID to operate in.
        app_token: Optional Slack app token (xapp-...) for socket mode.
        handlers: Event handler callbacks.
        commands: Custom command definitions.
        disabled_commands: Built-in commands to disable.
        data_dir: Directory for persistent state.
        auto_approve_duration: Auto-approve timer duration (seconds).
        never_auto_approve: Tool prefixes never auto-approved.
        flush_delay: Batch notification delay (seconds).
        error_debounce_seconds: Min seconds between error notifications.
    """

    def __init__(
        self,
        bot_token: str,
        channel_id: str,
        *,
        app_token: str | None = None,
        handlers: Handlers,
        commands: dict[str, CommandDef] | None = None,
        disabled_commands: set[str] | None = None,
        data_dir: str | None = None,
        auto_approve_duration: int = 30 * 60,
        never_auto_approve: set[str] | frozenset[str] | None = None,
        flush_delay: float = 1.5,
        error_debounce_seconds: int = 0,
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
        self._app_token = app_token
        self._channel_id = channel_id
        self._client: Any = None
        self._bolt_app: Any = None

    # ------------------------------------------------------------------
    # Platform lifecycle
    # ------------------------------------------------------------------

    async def _platform_start(self) -> None:
        from slack_sdk.web.async_client import AsyncWebClient

        self._client = AsyncWebClient(token=self._bot_token)

        if self._app_token:
            try:
                from slack_bolt.async_app import AsyncApp
                from slack_bolt.adapter.socket_mode.async_handler import (
                    AsyncSocketModeHandler,
                )

                self._bolt_app = AsyncApp(token=self._bot_token)

                @self._bolt_app.event("message")
                async def handle_message(event, say):
                    await self._handle_slack_message(event)

                handler = AsyncSocketModeHandler(self._bolt_app, self._app_token)
                asyncio.create_task(handler.start_async())
                logger.info("Slack bridge started with socket mode (channel=%s)", self._channel_id)
            except Exception:
                logger.exception("Failed to start socket mode, falling back to basic mode")
                logger.info("Slack bridge started in basic mode (channel=%s)", self._channel_id)
        else:
            logger.info(
                "Slack bridge started in basic mode — set app_token for commands and input (channel=%s)",
                self._channel_id,
            )

    async def _platform_stop(self) -> None:
        if self._client:
            await self._client.close()
        logger.info("Slack bridge stopped")

    # ------------------------------------------------------------------
    # Platform send
    # ------------------------------------------------------------------

    async def _platform_send(self, thread_id: str, text: str, **kwargs) -> None:
        if not self._client:
            return
        try:
            # Chunk long messages
            for i in range(0, len(text), _MSG_LIMIT):
                await self._client.chat_postMessage(
                    channel=self._channel_id,
                    thread_ts=thread_id,
                    text=text[i : i + _MSG_LIMIT],
                )
        except Exception:
            logger.exception("Failed to send Slack message (thread=%s)", thread_id)

    # ------------------------------------------------------------------
    # Platform threads
    # ------------------------------------------------------------------

    async def _platform_create_thread(self, name: str) -> str:
        if not self._client:
            raise RuntimeError("Slack bridge not started")

        response = await self._client.chat_postMessage(
            channel=self._channel_id,
            text=f"*New Session:* {name}",
        )
        if not response["ok"]:
            raise RuntimeError(f"Slack API error: {response}")

        thread_ts = response["ts"]
        return thread_ts

    # ------------------------------------------------------------------
    # Platform approval UI (text-based)
    # ------------------------------------------------------------------

    async def _platform_send_approval(
        self, thread_id: str, request: ApprovalRequest, formatted_description: str
    ) -> None:
        if not self._client:
            return

        text = (
            f"*⚠️ Approval Required*\n\n*{request.title}*\n\n{formatted_description}\n\n"
            "Reply with `allow`/`proceed`, `deny`/`cancel`, `deny: <reason>`, "
            "`allow all`, or `allow {{tool}}`."
        )
        try:
            await self._client.chat_postMessage(
                channel=self._channel_id,
                thread_ts=thread_id,
                text=text,
            )
        except Exception:
            logger.exception("Failed to send Slack approval (thread=%s)", thread_id)

    async def _platform_send_choice(self, thread_id: str, request: ApprovalRequest) -> None:
        if not self._client:
            return

        options = "\n".join(f"{i}. {o}" for i, o in enumerate(request.options, start=1))
        text = (
            f"*⚠️ {request.title}*\n\n{request.description}\n\n{options}\n\n"
            "Reply with a number (e.g. `1`) or an exact option label."
        )
        try:
            await self._client.chat_postMessage(
                channel=self._channel_id,
                thread_ts=thread_id,
                text=text,
            )
        except Exception:
            logger.exception("Failed to send Slack choice (thread=%s)", thread_id)

    # ------------------------------------------------------------------
    # Auto-approve batch (Slack formatting)
    # ------------------------------------------------------------------

    async def _send_auto_approve_batch(self, thread_id: str, items: list[tuple[str, str]]) -> None:
        if not self._client:
            return

        if len(items) == 1:
            tool_name, reason = items[0]
            text = f"✅ *{tool_name}* — auto-approved ({reason})"
        else:
            lines = [f"✅ Auto-approved {len(items)} tools:"]
            for tool_name, _reason in items:
                lines.append(f"  • {tool_name}")
            lines.append(f"_({items[0][1]})_")
            text = "\n".join(lines)

        try:
            await self._client.chat_postMessage(
                channel=self._channel_id,
                thread_ts=thread_id,
                text=text,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Slack message handler
    # ------------------------------------------------------------------

    async def _handle_slack_message(self, event: dict) -> None:
        """Route incoming Slack messages."""
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        text = event.get("text", "").strip()
        if not text:
            return

        thread_ts = event.get("thread_ts")
        user = event.get("user")

        # Messages in threads
        if thread_ts:
            thread_id = thread_ts
            await self._dispatch_message(thread_id, text, user)
            return

        # Top-level messages starting with ! → commands (thread_id = "")
        if text.startswith("!"):
            # For top-level commands, we need a reply mechanism
            await self._handle_top_level_command(event, text)

    async def _handle_top_level_command(self, event: dict, text: str) -> None:
        """Handle a command sent in the main channel (not in a thread)."""
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
                    await self._reply_to_event(event, reply)
            except Exception:
                logger.exception("Command !%s failed", cmd_name)
                await self._reply_to_event(event, f"Command failed: !{cmd_name}")
            return

        if self._handlers.on_command:
            try:
                reply = await self._handlers.on_command("", cmd_name, args)
                if reply:
                    await self._reply_to_event(event, reply)
            except Exception:
                logger.exception("Command handler failed for !%s", cmd_name)
            return

        await self._reply_to_event(
            event, f"Unknown command: !{cmd_name}\nUse !help for available commands."
        )

    async def _reply_to_event(self, event: dict, text: str) -> None:
        """Reply to a Slack event in the same channel/thread."""
        if not self._client:
            return
        kwargs: dict = {"channel": event.get("channel", self._channel_id), "text": text}
        thread_ts = event.get("thread_ts") or event.get("ts")
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        try:
            await self._client.chat_postMessage(**kwargs)
        except Exception:
            logger.exception("Failed to send Slack reply")
