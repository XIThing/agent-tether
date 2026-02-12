"""Telegram bridge implementation.

Uses python-telegram-bot to create forum topics, send messages with
HTML formatting, and handle approval flows via inline keyboard buttons.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
from typing import Any

from agent_tether.models import ApprovalRequest, CommandDef, Handlers
from agent_tether.platforms.base import BridgeBase
from agent_tether.platforms.telegram.formatting import (
    chunk_telegram_message,
    format_tool_input_full_html,
    format_tool_input_html,
    markdown_to_telegram_html,
)

logger = logging.getLogger("agent_tether.telegram")

_TOPIC_NAME_MAX_LEN = 64
_APPROVAL_TRUNCATE = 120


class TelegramBridge(BridgeBase):
    """Telegram bridge using forum topics for threads.

    Each thread maps to a Telegram forum topic. Approval requests
    use inline keyboard buttons.

    Args:
        token: Telegram bot API token.
        forum_group_id: Telegram forum supergroup chat ID.
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
        token: str,
        forum_group_id: int,
        handlers: Handlers,
        *,
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
            command_prefix="/",
        )
        self._token = token
        self._forum_group_id = forum_group_id
        self._app: Any = None

        # Caches for approval message editing
        self._pending_descriptions: dict[str, tuple[str, str]] = {}
        self._approval_html: dict[str, str] = {}
        self._pending_deny_reason: dict[int, tuple[str, str, str]] = {}
        self._typing_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Platform lifecycle
    # ------------------------------------------------------------------

    async def _platform_start(self) -> None:
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            MessageHandler,
            filters,
        )
        from telegram import BotCommand

        self._app = Application.builder().token(self._token).build()

        # Register command handlers for all registered commands
        for cmd_name in self._commands:
            self._app.add_handler(CommandHandler(cmd_name, self._make_command_handler(cmd_name)))

        # Plain text in supergroups
        self._app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.SUPERGROUP,
                self._handle_message,
            )
        )

        # Approval button callbacks
        self._app.add_handler(
            CallbackQueryHandler(self._handle_callback_query, pattern=r"^approval:")
        )

        await self._app.initialize()

        # Register command menu
        bot_commands = [
            BotCommand(name, cmd.description) for name, cmd in sorted(self._commands.items())
        ]
        await self._app.bot.set_my_commands(bot_commands)

        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram bridge started (group=%s)", self._forum_group_id)

    async def _platform_stop(self) -> None:
        if self._app:
            if self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        # Cancel all typing tasks
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        logger.info("Telegram bridge stopped")

    # ------------------------------------------------------------------
    # Platform send
    # ------------------------------------------------------------------

    async def _platform_send(self, thread_id: str, text: str, **kwargs) -> None:
        if not self._app:
            return
        topic_id = int(thread_id)
        formatted = markdown_to_telegram_html(text)
        for chunk in chunk_telegram_message(formatted):
            try:
                await self._app.bot.send_message(
                    chat_id=self._forum_group_id,
                    message_thread_id=topic_id,
                    text=chunk,
                    parse_mode="HTML",
                )
            except Exception:
                # Fallback to plain text
                try:
                    await self._app.bot.send_message(
                        chat_id=self._forum_group_id,
                        message_thread_id=topic_id,
                        text=text[:4096],
                    )
                except Exception:
                    logger.exception("Failed to send Telegram message (thread=%s)", thread_id)

    # ------------------------------------------------------------------
    # Platform threads
    # ------------------------------------------------------------------

    async def _platform_create_thread(self, name: str) -> str:
        if not self._app:
            raise RuntimeError("Telegram bridge not started")

        topic = await self._app.bot.create_forum_topic(
            chat_id=self._forum_group_id,
            name=name[:128],
            icon_color=7322096,
        )
        topic_id = topic.message_thread_id

        # Send intro and unpin auto-pinned message
        try:
            intro = await self._app.bot.send_message(
                chat_id=self._forum_group_id,
                message_thread_id=topic_id,
                text="Send a message here to interact.",
            )
            try:
                await self._app.bot.unpin_chat_message(
                    chat_id=self._forum_group_id,
                    message_id=intro.message_id,
                )
            except Exception:
                pass
        except Exception:
            pass

        return str(topic_id)

    # ------------------------------------------------------------------
    # Platform approval UI
    # ------------------------------------------------------------------

    async def _platform_send_approval(
        self, thread_id: str, request: ApprovalRequest, formatted_description: str
    ) -> None:
        if not self._app:
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        topic_id = int(thread_id)
        rid = request.request_id
        tool_name = request.title

        description_html, was_truncated = format_tool_input_html(
            request.description, truncate=_APPROVAL_TRUNCATE
        )

        is_task = self._approval.is_never_approved(tool_name)

        if was_truncated:
            self._pending_descriptions[rid] = (tool_name, request.description)

        row_actions = [
            InlineKeyboardButton(
                "Proceed" if is_task else "Allow",
                callback_data=f"approval:{rid}:Allow",
            ),
            InlineKeyboardButton(
                "Cancel" if is_task else "Deny",
                callback_data=f"approval:{rid}:Deny",
            ),
            InlineKeyboardButton(
                "Cancel ✏️" if is_task else "Deny ✏️",
                callback_data=f"approval:{rid}:DenyWithReason",
            ),
        ]

        rows = [row_actions]
        if not is_task:
            row_timers = [
                InlineKeyboardButton(
                    f"Allow {tool_name} (30m)",
                    callback_data=f"approval:{rid}:AllowTool:{tool_name}",
                ),
                InlineKeyboardButton(
                    "Allow All (30m)",
                    callback_data=f"approval:{rid}:AllowAll",
                ),
            ]
            rows.append(row_timers)

            # Directory-scoped timer
            directory = self._approval.get_directory(thread_id)
            if directory:
                import os

                dir_short = os.path.basename(directory) or "dir"
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"Allow {dir_short}/ (30m)",
                            callback_data=f"approval:{rid}:AllowDir",
                        )
                    ]
                )

        if was_truncated:
            rows.append(
                [InlineKeyboardButton("Show All", callback_data=f"approval:{rid}:ShowAll")]
            )

        reply_markup = InlineKeyboardMarkup(rows)
        text = f"⚠️ <b>{html_mod.escape(tool_name)}</b>\n\n{description_html}"
        self._approval_html[rid] = text

        try:
            await self._app.bot.send_message(
                chat_id=self._forum_group_id,
                message_thread_id=topic_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed to send approval request (thread=%s)", thread_id)

    async def _platform_send_choice(self, thread_id: str, request: ApprovalRequest) -> None:
        if not self._app:
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        topic_id = int(thread_id)
        rid = request.request_id

        md = f"⚠️ *{request.title}*\n\n{request.description}"
        html_text = markdown_to_telegram_html(md)
        self._approval_html[rid] = html_text

        rows: list[list] = []
        current: list = []
        for idx, label in enumerate(request.options, start=1):
            current.append(
                InlineKeyboardButton(
                    f"{idx}. {label}",
                    callback_data=f"approval:{rid}:Choose:{idx}",
                )
            )
            if len(current) == 2:
                rows.append(current)
                current = []
        if current:
            rows.append(current)

        try:
            await self._app.bot.send_message(
                chat_id=self._forum_group_id,
                message_thread_id=topic_id,
                text=html_text,
                reply_markup=InlineKeyboardMarkup(rows),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed to send choice request (thread=%s)", thread_id)

    # ------------------------------------------------------------------
    # Platform typing
    # ------------------------------------------------------------------

    async def _platform_typing_start(self, thread_id: str) -> None:
        if not self._app:
            return
        if thread_id in self._typing_tasks:
            return
        topic_id = int(thread_id)
        self._typing_tasks[thread_id] = asyncio.create_task(self._typing_loop(thread_id, topic_id))

    async def _platform_typing_stop(self, thread_id: str) -> None:
        task = self._typing_tasks.pop(thread_id, None)
        if task:
            task.cancel()

    async def _typing_loop(self, thread_id: str, topic_id: int) -> None:
        try:
            while True:
                try:
                    await self._app.bot.send_chat_action(
                        chat_id=self._forum_group_id,
                        message_thread_id=topic_id,
                        action="typing",
                    )
                except Exception:
                    pass
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Auto-approve batch (HTML formatting)
    # ------------------------------------------------------------------

    async def _send_auto_approve_batch(self, thread_id: str, items: list[tuple[str, str]]) -> None:
        if not self._app:
            return
        topic_id = int(thread_id)

        if len(items) == 1:
            tool_name, reason = items[0]
            text = f"✅ <b>{html_mod.escape(tool_name)}</b> — auto-approved ({html_mod.escape(reason)})"
        else:
            lines = [f"✅ Auto-approved {len(items)} tools:"]
            for tool_name, _reason in items:
                lines.append(f"  • {html_mod.escape(tool_name)}")
            lines.append(f"<i>({html_mod.escape(items[0][1])})</i>")
            text = "\n".join(lines)

        try:
            await self._app.bot.send_message(
                chat_id=self._forum_group_id,
                message_thread_id=topic_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Telegram event handlers
    # ------------------------------------------------------------------

    def _make_command_handler(self, cmd_name: str):
        """Create a python-telegram-bot CommandHandler callback for a registered command."""

        async def handler(update: Any, context: Any) -> None:
            if not update.message:
                return
            topic_id = update.message.message_thread_id
            thread_id = str(topic_id) if topic_id else None

            if not thread_id:
                # General topic — only help works without a thread
                if cmd_name == "help":
                    cmd = self._commands.get("help")
                    if cmd:
                        reply = await cmd.handler("", "")
                        if reply:
                            await update.message.reply_text(reply)
                else:
                    await update.message.reply_text("Use this command inside a session topic.")
                return

            args = " ".join(context.args) if context.args else ""
            cmd = self._commands.get(cmd_name)
            if cmd:
                try:
                    reply = await cmd.handler(thread_id, args)
                    if reply:
                        # Try HTML first, fall back to plain text
                        try:
                            await update.message.reply_text(reply, parse_mode="HTML")
                        except Exception:
                            await update.message.reply_text(reply)
                except Exception:
                    logger.exception("Command /%s failed", cmd_name)
                    await update.message.reply_text(f"Command failed: /{cmd_name}")

        return handler

    @staticmethod
    def _display_name(user: Any) -> str:
        if not user:
            return "unknown"
        if user.username:
            return f"@{user.username}"
        parts = [user.first_name or "", user.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or "unknown"

    async def _handle_message(self, update: Any, context: Any) -> None:
        """Handle incoming text messages."""
        if not update.message or not update.message.text:
            return

        topic_id = update.message.message_thread_id
        if not topic_id:
            await update.message.reply_text(
                "💡 Send messages in a topic to interact with an agent."
            )
            return

        thread_id = str(topic_id)
        text = update.message.text.strip()
        username = self._display_name(update.message.from_user)

        # Check for pending "Deny with reason"
        pending_deny = self._pending_deny_reason.pop(topic_id, None)
        if pending_deny:
            p_thread_id, p_request_id, p_username = pending_deny
            reason = text
            pending_req = self._pending.pop(p_thread_id, None)
            if self._handlers.on_approval_response:
                await self._handlers.on_approval_response(
                    p_thread_id, p_request_id, False, reason, None
                )
            await update.message.reply_text(f"❌ Denied by {p_username}: {reason}")
            return

        # Route through base dispatch (approval parsing, commands, input)
        await self._dispatch_message(thread_id, text, username)

    async def _handle_callback_query(self, update: Any, context: Any) -> None:
        """Handle approval button clicks."""
        query = update.callback_query
        if not query:
            return

        await query.answer()

        try:
            parts = query.data.split(":", 2)
            if len(parts) != 3 or parts[0] != "approval":
                return
            request_id = parts[1]
            option = parts[2]
        except Exception:
            return

        topic_id = query.message.message_thread_id
        if not topic_id:
            return
        thread_id = str(topic_id)

        username = self._display_name(query.from_user)
        original_html = self._approval_html.get(request_id, query.message.text or "")

        # Show All — send full untruncated description
        if option == "ShowAll":
            cached = self._pending_descriptions.get(request_id)
            if cached:
                tool_name, raw_desc = cached
                full_html = format_tool_input_full_html(raw_desc)
                full_text = f"⚠️ <b>{html_mod.escape(tool_name)}</b> (full)\n\n{full_html}"
                for part in chunk_telegram_message(full_text):
                    try:
                        await self._app.bot.send_message(
                            chat_id=self._forum_group_id,
                            message_thread_id=topic_id,
                            text=part,
                            parse_mode="HTML",
                        )
                    except Exception:
                        await self._app.bot.send_message(
                            chat_id=self._forum_group_id,
                            message_thread_id=topic_id,
                            text=part,
                        )
            return

        # Choice selection
        if option.startswith("Choose:"):
            pending = self._pending.get(thread_id)
            if not pending or pending.kind != "choice":
                await query.edit_message_text(
                    text=f"{original_html}\n\n❌ Request expired.",
                    parse_mode="HTML",
                )
                return
            try:
                idx = int(option.split(":", 1)[1]) - 1
            except Exception:
                idx = -1
            if idx < 0 or idx >= len(pending.options):
                return
            selected = pending.options[idx]
            self._pending.pop(thread_id, None)
            if self._handlers.on_approval_response:
                await self._handlers.on_approval_response(
                    thread_id, pending.request_id, True, selected, None
                )
            await query.edit_message_text(
                text=f"{original_html}\n\n✅ {html_mod.escape(selected)} by {html_mod.escape(username)}",
                parse_mode="HTML",
            )
            return

        # Deny with reason — prompt
        if option == "DenyWithReason":
            self._pending_deny_reason[topic_id] = (thread_id, request_id, username)
            await query.edit_message_text(
                text=f"{original_html}\n\n✏️ Why? Reply with your reason.",
                parse_mode="HTML",
            )
            return

        # Timer-based approvals
        if option == "AllowAll":
            self._approval.set_allow_all(thread_id)
            allow = True
            display = "Allow All (30m)"
        elif option == "AllowDir":
            directory = self._approval.get_directory(thread_id)
            if directory:
                import os

                self._approval.set_allow_directory(directory)
                dir_short = os.path.basename(directory) or "dir"
                display = f"Allow {dir_short}/ (30m)"
            else:
                self._approval.set_allow_all(thread_id)
                display = "Allow All (30m)"
            allow = True
        elif option.startswith("AllowTool:"):
            tool_name = option.split(":", 1)[1]
            self._approval.set_allow_tool(thread_id, tool_name)
            allow = True
            display = f"Allow {tool_name} (30m)"
        else:
            allow = option.lower() in ("allow", "yes", "approve")
            display = option if allow else "Denied"

        self._pending.pop(thread_id, None)

        if self._handlers.on_approval_response:
            timer = None
            if option == "AllowAll":
                timer = "all"
            elif option == "AllowDir":
                timer = "dir"
            elif option.startswith("AllowTool:"):
                timer = option.split(":", 1)[1]
            await self._handlers.on_approval_response(thread_id, request_id, allow, None, timer)

        if allow:
            await query.edit_message_text(
                text=f"{original_html}\n\n✅ {html_mod.escape(display)} by {html_mod.escape(username)}",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                text=f"{original_html}\n\n❌ Denied by {html_mod.escape(username)}",
                parse_mode="HTML",
            )

        self._approval_html.pop(request_id, None)

    # ------------------------------------------------------------------
    # Thread removal
    # ------------------------------------------------------------------

    async def remove_thread(self, thread_id: str) -> None:
        """Clean up Telegram-specific state on thread removal."""
        task = self._typing_tasks.pop(thread_id, None)
        if task:
            task.cancel()
        await super().remove_thread(thread_id)
