"""Discord bridge implementation with command handling and session threading."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from agent_tether.base import (
    ApprovalRequest,
    BridgeCallbacks,
    BridgeConfig,
    GetSessionDirectory,
    GetSessionInfo,
    OnSessionBound,
    _EXTERNAL_MAX_FETCH,
)
from agent_tether.discord.pairing_state import (
    DiscordPairingState,
    load_or_create as load_pairing_state,
    save as save_pairing_state,
)
from agent_tether.text_command_bridge import TextCommandBridge

logger = structlog.get_logger(__name__)

_DISCORD_MSG_LIMIT = 2000


@dataclass
class DiscordConfig:
    """Discord-specific configuration."""

    require_pairing: bool = False
    allowed_user_ids: list[int] | None = None
    pairing_code: str | None = None


class DiscordBridge(TextCommandBridge):
    """Discord bridge that routes agent events to Discord threads.

    Commands (in main channel): !help, !status, !list, !attach, !stop, !usage
    Session input: messages in session threads are forwarded as input.
    """

    def __init__(
        self,
        bot_token: str,
        channel_id: int,
        discord_config: DiscordConfig | None = None,
        config: BridgeConfig | None = None,
        callbacks: BridgeCallbacks | None = None,
        get_session_directory: GetSessionDirectory | None = None,
        get_session_info: GetSessionInfo | None = None,
        on_session_bound: OnSessionBound | None = None,
    ):
        dc = discord_config or DiscordConfig()
        data_dir = (config or BridgeConfig()).data_dir or "."
        super().__init__(
            config=config,
            callbacks=callbacks,
            get_session_directory=get_session_directory,
            get_session_info=get_session_info,
            on_session_bound=on_session_bound,
            thread_name_path=Path(data_dir) / "discord_threads.json",
        )
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._client: Any = None
        self._thread_ids: dict[str, int] = {}  # session_id -> thread_id
        # Pairing / allowlist
        self._pairing_required = dc.require_pairing
        self._allowed_user_ids = dc.allowed_user_ids or []
        self._pairing_state_path = Path(data_dir) / "discord_pairing.json"
        self._pairing_state: DiscordPairingState | None = None
        self._paired_user_ids: set[int] = set()
        self._pairing_code: str | None = None
        # Background typing indicator loops: session_id -> asyncio.Task
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._fixed_pairing_code = dc.pairing_code

        fixed_code = self._fixed_pairing_code
        if self._pairing_required or fixed_code:
            self._pairing_state = load_pairing_state(
                path=self._pairing_state_path,
                fixed_code=fixed_code,
            )
            self._paired_user_ids = set(self._pairing_state.paired_user_ids)
            self._pairing_code = self._pairing_state.pairing_code
            if not self._channel_id and self._pairing_state.control_channel_id:
                self._channel_id = int(self._pairing_state.control_channel_id)
        elif not self._channel_id:
            # Even without explicit pairing requirement, if the channel isn't set we
            # still want a setup code to prevent random users from configuring it.
            self._pairing_state = load_pairing_state(
                path=self._pairing_state_path,
                fixed_code=fixed_code,
            )
            self._paired_user_ids = set(self._pairing_state.paired_user_ids)
            self._pairing_code = self._pairing_state.pairing_code
            if self._pairing_state.control_channel_id:
                self._channel_id = int(self._pairing_state.control_channel_id)

    def restore_thread_mappings(self, sessions: list[dict] | None = None) -> None:
        """Restore session-to-thread mappings after restart.

        Args:
            sessions: List of session dicts with 'id', 'platform', 'platform_thread_id'.
                      If None, no mappings are restored.
        """
        if not sessions:
            return
        for session in sessions:
            if session.get("platform") == "discord" and session.get("platform_thread_id"):
                try:
                    self._thread_ids[session["id"]] = int(session["platform_thread_id"])
                except (ValueError, TypeError):
                    pass

    async def start(self) -> None:
        """Initialize and start Discord client."""
        try:
            import discord
        except ImportError:
            logger.error("discord.py not installed. Install with: pip install discord.py")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready() -> None:
            logger.info("Discord client ready", user=self._client.user)

        @self._client.event
        async def on_message(message: Any) -> None:
            await self._handle_message(message)

        asyncio.create_task(self._client.start(self._bot_token))

        logger.info("Discord bridge initialized and starting", channel_id=self._channel_id)
        if not self._channel_id and self._pairing_code:
            logger.warning(
                "Discord bridge not configured with a control channel. Run !setup <code> in the desired channel.",
                code=self._pairing_code,
            )
        elif self._pairing_required and self._pairing_code:
            logger.warning(
                "Discord pairing enabled. DM the bot: !pair <code>",
                code=self._pairing_code,
            )

    async def stop(self) -> None:
        """Stop Discord client."""
        if self._client:
            await self._client.close()
        logger.info("Discord bridge stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_authorized_user_id(self, user_id: int | None) -> bool:
        """Check if a Discord user is authorized to use this bridge."""
        if not user_id:
            return False
        if int(user_id) in self._allowed_user_ids:
            return True
        if int(user_id) in self._paired_user_ids:
            return True

        # Backwards-compatible default: if pairing isn't required and no allowlist
        # is configured and no-one has paired yet, allow all users in the channel.
        if not self._pairing_required and not self._allowed_user_ids and not self._paired_user_ids:
            return True
        return False

    async def _send_not_paired(self, message: Any) -> None:
        """Send a 'not authorized' message."""
        if not self._pairing_required:
            await message.channel.send(
                "🔒 Not authorized. Pairing is not required, but an allowlist/pairing may be configured."
            )
            return
        await message.channel.send(
            "🔒 Not paired. DM the bot: `!pair <code>` (pairing code is in the Tether server logs)."
        )

    def _ensure_pairing_state_loaded(self) -> None:
        """Lazily load or create the pairing state file."""
        if self._pairing_state:
            return
        fixed_code = self._fixed_pairing_code
        self._pairing_state = load_pairing_state(
            path=self._pairing_state_path,
            fixed_code=fixed_code,
        )
        self._paired_user_ids = set(self._pairing_state.paired_user_ids)
        self._pairing_code = self._pairing_state.pairing_code
        if not self._channel_id and self._pairing_state.control_channel_id:
            self._channel_id = int(self._pairing_state.control_channel_id)

    def _session_for_thread(self, thread_id: int) -> str | None:
        """Look up the session ID for a Discord thread ID."""
        for sid, tid in self._thread_ids.items():
            if tid == thread_id:
                return sid
        return None

    # ------------------------------------------------------------------
    # Message router
    # ------------------------------------------------------------------

    async def _handle_message(self, message: Any) -> None:
        """Route incoming Discord messages to commands or session input."""
        try:
            import discord
        except ImportError:
            return

        if message.author.bot:
            return

        text = message.content.strip()
        if not text:
            return

        # Setup/pairing commands are allowed even when not authorized.
        if text.lower().startswith(("!pair", "!setup")):
            await self._dispatch_command(message, text)
            return

        # Messages in threads -> session input or thread commands
        if isinstance(message.channel, discord.Thread):
            if text.startswith("!"):
                await self._dispatch_command(message, text)
                return
            session_id = self._session_for_thread(message.channel.id)
            if not session_id:
                return
            if not self._is_authorized_user_id(getattr(message.author, "id", None)):
                await self._send_not_paired(message)
                return
            await self._forward_input(message, session_id, text)
            return

        # Messages in the configured channel starting with ! -> commands
        if self._channel_id and message.channel.id == self._channel_id and text.startswith("!"):
            await self._dispatch_command(message, text)
            return

        # If not configured, allow running !setup in any non-thread channel.
        if not self._channel_id and text.lower().startswith("!setup"):
            await self._dispatch_command(message, text)

    async def _dispatch_command(self, message: Any, text: str) -> None:
        """Parse and dispatch a !command."""
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd not in (
            "!help",
            "!start",
            "!pair",
            "!pair-status",
            "!setup",
        ) and not self._is_authorized_user_id(getattr(message.author, "id", None)):
            await self._send_not_paired(message)
            return

        if cmd in ("!help", "!start"):
            await self._cmd_help(message)
        elif cmd in ("!status", "!sessions"):
            await self._cmd_status(message)
        elif cmd == "!list":
            await self._cmd_list(message, args)
        elif cmd == "!attach":
            await self._cmd_attach(message, args)
        elif cmd == "!new":
            await self._cmd_new(message, args)
        elif cmd == "!stop":
            await self._cmd_stop(message)
        elif cmd == "!usage":
            await self._cmd_usage(message)
        elif cmd == "!pair":
            await self._cmd_pair(message, args)
        elif cmd == "!pair-status":
            await self._cmd_pair_status(message)
        elif cmd == "!setup":
            await self._cmd_setup(message, args)
        else:
            await message.channel.send(
                f"Unknown command: {cmd}\nUse !help for available commands."
            )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _cmd_help(self, message: Any) -> None:
        """Handle !help."""
        text = (
            "Tether Commands:\n\n"
            "!status — List all sessions\n"
            "!list [page|search] — List external sessions (Claude Code, Codex)\n"
            "!attach <number> [force] — Attach to an external session\n"
            "!new [agent] [directory] — Start a new session\n"
            "!stop — Interrupt the session in this thread\n"
            "!usage — Show token usage and cost for this session\n"
            "!setup <code> — Configure this channel as the control channel and pair you\n"
            "!pair <code> — Pair your Discord user to authorize commands\n"
            "!pair-status — Show whether you are authorized\n"
            "!help — Show this help\n\n"
            "Send a text message in a session thread to forward it as input."
        )
        await message.channel.send(text)

    async def _cmd_setup(self, message: Any, args: str) -> None:
        """Configure the current channel as the bot's control channel."""
        code = (args or "").strip()
        if not code:
            await message.channel.send("Usage: `!setup <code>`")
            return

        self._ensure_pairing_state_loaded()
        if not self._pairing_code or code != self._pairing_code:
            await message.channel.send("Invalid setup code.")
            return

        channel_id = getattr(getattr(message, "channel", None), "id", None)
        if not channel_id:
            await message.channel.send("Could not read this channel id.")
            return

        self._channel_id = int(channel_id)
        assert self._pairing_state is not None
        self._pairing_state.control_channel_id = self._channel_id

        # Pair the caller as well (so they can immediately use the bot).
        user_id = getattr(getattr(message, "author", None), "id", None)
        if user_id:
            self._paired_user_ids.add(int(user_id))
            self._pairing_state.paired_user_ids = set(self._paired_user_ids)

        save_pairing_state(path=self._pairing_state_path, state=self._pairing_state)

        await message.channel.send(
            "✅ Setup complete. This channel is now the control channel. Try `!help`."
        )

    async def _cmd_pair(self, message: Any, args: str) -> None:
        """Handle !pair to authorize a Discord user."""
        guild = getattr(message, "guild", None)
        channel_id = getattr(getattr(message, "channel", None), "id", None)
        if guild is not None and channel_id != self._channel_id:
            return

        if not (self._pairing_required or self._fixed_pairing_code):
            await message.channel.send(
                "Pairing is not enabled. Set `DISCORD_REQUIRE_PAIRING=1` to enforce it."
            )
            return

        code = (args or "").strip()
        if not code:
            await message.channel.send("Usage: `!pair <code>`")
            return

        self._ensure_pairing_state_loaded()
        if not self._pairing_code or code != self._pairing_code:
            await message.channel.send("Invalid pairing code.")
            return

        user_id = getattr(message.author, "id", None)
        if not user_id:
            await message.channel.send("Could not read your Discord user id.")
            return

        self._paired_user_ids.add(int(user_id))
        assert self._pairing_state is not None
        self._pairing_state.paired_user_ids = set(self._paired_user_ids)
        save_pairing_state(path=self._pairing_state_path, state=self._pairing_state)

        await message.channel.send("✅ Paired. You can now use Tether commands.")

    async def _cmd_pair_status(self, message: Any) -> None:
        """Handle !pair-status."""
        user_id = getattr(getattr(message, "author", None), "id", None)
        authorized = self._is_authorized_user_id(user_id)
        await message.channel.send(
            f"Pairing required: {self._pairing_required}\n"
            f"Authorized: {authorized}\n"
            f"Your user id: {user_id}"
        )

    async def _cmd_new(self, message: Any, args: str) -> None:
        """Create a new session and Discord thread."""
        try:
            import discord
        except ImportError:
            return

        base_session_id: str | None = None
        if isinstance(message.channel, discord.Thread):
            base_session_id = self._session_for_thread(message.channel.id)

        try:
            adapter, directory = await self._parse_new_args(args, base_session_id=base_session_id)
        except ValueError as e:
            await message.channel.send(str(e))
            return
        except Exception as e:
            await message.channel.send(f"Invalid directory: {e}")
            return

        dir_short = directory.rstrip("/").rsplit("/", 1)[-1] or "Session"
        agent_label = (
            self._adapter_label(adapter)
            or self._adapter_label(self._config.default_adapter)
            or "Claude"
        )
        session_name = self._make_external_thread_name(directory=directory, session_id="")

        try:
            session = await self._create_session_via_api(
                directory=directory,
                platform="discord",
                adapter=adapter,
                session_name=session_name,
            )
        except Exception as e:
            await message.channel.send(f"Failed to create session: {e}")
            return

        await message.channel.send(f"✅ New {agent_label} session created in {dir_short}.")
        try:
            thread_id = int(session.get("platform_thread_id") or 0)
        except Exception:
            thread_id = 0
        if thread_id:
            await message.channel.send(f"🧵 Open thread: <#{thread_id}>")

    async def _cmd_status(self, message: Any) -> None:
        """Handle !status."""
        try:
            sessions = await self._callbacks.list_sessions()
        except Exception:
            logger.exception("Failed to fetch sessions for !status")
            await message.channel.send("Failed to fetch sessions.")
            return

        if not sessions:
            await message.channel.send("No sessions.")
            return

        lines = ["Sessions:\n"]
        for s in sessions:
            emoji = self._STATE_EMOJI.get(s.get("state", ""), "❓")
            name = s.get("name") or s.get("id", "")[:12]
            lines.append(f"  {emoji} {name}")
        await message.channel.send("\n".join(lines))

    async def _cmd_list(self, message: Any, args: str) -> None:
        """Handle !list."""
        page, query = self._parse_list_args(args)

        try:
            self._cached_external = await self._callbacks.list_external_sessions(
                limit=_EXTERNAL_MAX_FETCH
            )
            if not args:
                self._set_external_view(None)
            else:
                self._set_external_view(query)
        except Exception:
            logger.exception("Failed to fetch external sessions")
            await message.channel.send("Failed to list external sessions.")
            return

        text, _, _ = self._format_external_page(page)
        await message.channel.send(text)

    async def _cmd_attach(self, message: Any, args: str) -> None:
        """Handle !attach."""
        if not args:
            await message.channel.send("Usage: !attach <number> [force]\n\nRun !list first.")
            return

        parts = args.split()
        force = len(parts) > 1 and parts[-1].lower() == "force"

        try:
            index = int(parts[0]) - 1
        except ValueError:
            await message.channel.send("Please provide a session number.")
            return

        if not self._cached_external:
            await message.channel.send("No external sessions cached. Run !list first.")
            return
        if not self._external_view:
            await message.channel.send("No external sessions listed. Run !list first.")
            return
        if index < 0 or index >= len(self._external_view):
            await message.channel.send(f"Invalid number. Use 1–{len(self._external_view)}.")
            return

        external = self._external_view[index]

        try:
            session = await self._callbacks.attach_external(
                external_id=external["id"],
                runner_type=external["runner_type"],
                directory=external["directory"],
            )
            session_id = session["id"]

            # Check if already has a thread
            existing_thread_id = self._thread_ids.get(session_id)
            if existing_thread_id:
                if force:
                    logger.info(
                        "Force-recreating thread",
                        session_id=session_id,
                        thread_id=existing_thread_id,
                    )
                    self._thread_ids.pop(session_id, None)
                    self._release_thread_name(session_id)
                else:
                    # Verify the thread is still accessible
                    thread_ok = False
                    try:
                        thread = self._client.get_channel(existing_thread_id)
                        if thread is not None:
                            thread_ok = True
                    except Exception:
                        pass

                    if thread_ok:
                        await message.channel.send(
                            f"Already attached. Open thread: <#{existing_thread_id}>\n"
                            "Use `!attach <number> force` to recreate the thread."
                        )
                        return
                    else:
                        logger.info(
                            "Existing thread is stale, will recreate",
                            session_id=session_id,
                            thread_id=existing_thread_id,
                        )
                        self._thread_ids.pop(session_id, None)
                        self._release_thread_name(session_id)

            # Create thread
            session_name = self._make_external_thread_name(
                directory=external.get("directory", ""),
                session_id=session_id,
            )
            thread_info = await self.create_thread(session_id, session_name)
            try:
                thread_id = int(thread_info.get("thread_id") or 0)
                if thread_id:
                    await message.channel.send(f"🧵 Open thread: <#{thread_id}>")
                    replay = await self._format_external_replay(
                        external["id"],
                        str(external["runner_type"]),
                        content_limit=300,
                        thinking_limit=150,
                        total_limit=1900,
                    )
                    if replay:
                        try:
                            thread = self._client.get_channel(thread_id)
                            if thread:
                                await thread.send(replay)
                        except Exception:
                            logger.exception(
                                "Failed to send Discord external session replay",
                                external_id=external["id"],
                            )
            except Exception:
                logger.exception("Failed to replay external session history into Discord thread")

            # Bind platform
            if self._on_session_bound:
                await self._on_session_bound(session_id, "discord", thread_info.get("thread_id"))

            dir_short = external.get("directory", "").rsplit("/", 1)[-1]
            await message.channel.send(
                f"✅ Attached to {external['runner_type']} session in {dir_short}\n\n"
                f"A new thread has been created. Send messages there to interact."
            )

        except Exception as e:
            logger.warning("Failed to attach to external session", error=str(e))
            await message.channel.send(f"Failed to attach: {e}")

    async def _cmd_stop(self, message: Any) -> None:
        """Handle !stop."""
        import discord

        if not isinstance(message.channel, discord.Thread):
            await message.channel.send("Use this command inside a session thread.")
            return
        if not self._is_authorized_user_id(getattr(message.author, "id", None)):
            await self._send_not_paired(message)
            return

        session_id = self._session_for_thread(message.channel.id)
        if not session_id:
            await message.channel.send("No session linked to this thread.")
            return

        try:
            await self._callbacks.stop_session(session_id)
            await message.channel.send("⏹️ Session interrupted.")
        except Exception as e:
            logger.exception("Failed to interrupt session")
            await message.channel.send(f"Failed to interrupt: {e}")

    async def _cmd_usage(self, message: Any) -> None:
        """Show token usage for the session in the current thread."""
        try:
            import discord
        except ImportError:
            return

        if not isinstance(message.channel, discord.Thread):
            await message.channel.send("Use this command inside a session thread.")
            return

        session_id = self._session_for_thread(message.channel.id)
        if not session_id:
            await message.channel.send("No session linked to this thread.")
            return

        try:
            usage = await self._fetch_usage(session_id)
            await message.channel.send(f"📊 {self._format_usage_text(usage)}")
        except Exception as e:
            logger.exception("Failed to get usage")
            await message.channel.send(f"Failed to get usage: {e}")

    # ------------------------------------------------------------------
    # Session input forwarding
    # ------------------------------------------------------------------

    async def _forward_input(self, message: Any, session_id: str, text: str) -> None:
        """Forward a user message as session input, handling approvals."""
        if not self._is_authorized_user_id(getattr(message.author, "id", None)):
            await self._send_not_paired(message)
            return

        pending = self.get_pending_permission(session_id)
        if pending:
            # Choice requests: allow "1"/"2"/... or exact label.
            if pending.kind == "choice":
                selected = self.parse_choice_text(session_id, text)
                if selected:
                    await self._send_input_or_start_via_api(session_id=session_id, text=selected)
                    self.clear_pending_permission(session_id)
                    await message.channel.send(f"✅ Selected: {selected}")
                    return

            parsed = self.parse_approval_text(text)
            if parsed is not None:
                ok, msg = await self._handle_approval_text_response(session_id, pending, parsed)
                if ok:
                    emoji = "✅" if parsed["allow"] else "❌"
                    await message.channel.send(f"{emoji} {msg}")
                else:
                    await message.channel.send("❌ Failed. Request may have expired.")
                return

        try:
            await self._send_input_or_start_via_api(session_id=session_id, text=text)
            logger.info(
                "Forwarded human input from Discord",
                session_id=session_id,
                thread_id=message.channel.id,
                username=message.author.name,
            )
        except Exception:
            logger.exception("Failed to forward human input", session_id=session_id)
            await message.channel.send("Failed to send input.")

    # ------------------------------------------------------------------
    # Typing indicator
    # ------------------------------------------------------------------

    async def _typing_loop(self, session_id: str, thread_id: int) -> None:
        """Send typing indicator every 8s until cancelled.

        Discord's typing indicator lasts for ~10 seconds, so we refresh
        it every 8 seconds to maintain a continuous indicator.
        """
        try:
            while True:
                try:
                    thread = self._client.get_channel(thread_id)
                    if thread:
                        await thread.typing()
                except Exception:
                    logger.debug(
                        "Failed to send Discord typing indicator",
                        session_id=session_id,
                        thread_id=thread_id,
                    )
                await asyncio.sleep(8)
        except asyncio.CancelledError:
            pass

    def _stop_typing(self, session_id: str) -> None:
        """Cancel the typing indicator task for a session."""
        task = self._typing_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    async def on_typing(self, session_id: str) -> None:
        """Start a repeating typing indicator for the session."""
        if not self._client:
            return

        thread_id = self._thread_ids.get(session_id)
        if not thread_id:
            return

        # Already running for this session
        if session_id in self._typing_tasks:
            return

        self._typing_tasks[session_id] = asyncio.create_task(
            self._typing_loop(session_id, thread_id)
        )

    async def on_typing_stopped(self, session_id: str) -> None:
        """Stop the typing indicator for the session."""
        self._stop_typing(session_id)

    # ------------------------------------------------------------------
    # Bridge interface (outgoing events)
    # ------------------------------------------------------------------

    async def on_output(self, session_id: str, text: str, metadata: dict | None = None) -> None:
        """Send output text to Discord thread."""
        if not self._client:
            logger.warning("Discord client not initialized")
            return

        thread_id = self._thread_ids.get(session_id)
        if not thread_id:
            logger.warning("No Discord thread for session", session_id=session_id)
            return

        try:
            thread = self._client.get_channel(thread_id)
            if thread:
                # Discord has a 2000 char limit per message
                for i in range(0, len(text), _DISCORD_MSG_LIMIT):
                    await thread.send(text[i : i + _DISCORD_MSG_LIMIT])
        except Exception:
            logger.exception("Failed to send Discord message", session_id=session_id)

    async def send_auto_approve_batch(self, session_id: str, items: list[tuple[str, str]]) -> None:
        """Send a batched auto-approve notification to Discord."""
        if not self._client:
            return
        thread_id = self._thread_ids.get(session_id)
        if not thread_id:
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

        try:
            thread = self._client.get_channel(thread_id)
            if thread:
                await thread.send(text[:_DISCORD_MSG_LIMIT])
        except Exception:
            pass

    async def on_approval_request(self, session_id: str, request: ApprovalRequest) -> None:
        """Send an approval request to Discord thread."""
        if not self._client:
            return

        # Choice requests: send options and let user reply with "1"/"2"/... or the label.
        if request.kind == "choice":
            thread_id = self._thread_ids.get(session_id)
            if not thread_id:
                return
            thread = self._client.get_channel(thread_id)
            if not thread:
                return

            self.set_pending_permission(session_id, request)
            options = "\n".join([f"{i}. {o}" for i, o in enumerate(request.options, start=1)])
            text = (
                f"⚠️ **{request.title}**\n\n{request.description}\n\n{options}\n\n"
                "Reply with a number (e.g. `1`) or an exact option label."
            )
            try:
                await thread.send(text)
            except Exception:
                logger.exception("Failed to send Discord choice request", session_id=session_id)
            return

        reason: str | None = None
        if request.kind == "permission":
            reason = self.check_auto_approve(session_id, request.title)
        if reason:
            await self._auto_approve(session_id, request, reason=reason)
            self.buffer_auto_approve_notification(session_id, request.title, reason)
            return

        thread_id = self._thread_ids.get(session_id)
        if not thread_id:
            return

        self.set_pending_permission(session_id, request)

        formatted = self.format_tool_input_markdown(request.description)
        text = (
            f"**⚠️ Approval Required**\n\n**{request.title}**\n\n{formatted}\n\n"
            "Reply with `allow`/`proceed`, `deny`/`cancel`, `deny: <reason>`, `allow all`, or `allow {tool}`."
        )
        try:
            thread = self._client.get_channel(thread_id)
            if thread:
                await thread.send(text)
        except Exception:
            logger.exception("Failed to send Discord approval request", session_id=session_id)

    async def on_status_change(
        self, session_id: str, status: str, metadata: dict | None = None
    ) -> None:
        """Send status change to Discord thread."""
        if not self._client:
            return

        if status == "error" and not self._should_send_error_status(session_id):
            return

        thread_id = self._thread_ids.get(session_id)
        if not thread_id:
            return

        emoji_map = {
            "thinking": "💭",
            "executing": "⚙️",
            "done": "✅",
            "error": "❌",
        }
        emoji = emoji_map.get(status, "ℹ️")
        text = f"{emoji} Status: {status}"

        try:
            thread = self._client.get_channel(thread_id)
            if thread:
                await thread.send(text)
        except Exception:
            logger.exception("Failed to send Discord status", session_id=session_id)

    async def create_thread(self, session_id: str, session_name: str) -> dict:
        """Create a Discord thread for a session."""
        if not self._client:
            raise RuntimeError("Discord client not initialized")

        try:
            self._reserve_thread_name(session_id, session_name)

            channel = self._client.get_channel(self._channel_id)
            if not channel:
                raise RuntimeError(f"Discord channel {self._channel_id} not found")

            thread = await channel.create_thread(
                name=session_name[:100],  # Discord limit
                auto_archive_duration=1440,  # 24 hours
            )

            thread_id = thread.id
            self._thread_ids[session_id] = thread_id
            try:
                await thread.send(
                    "Tether session thread.\n"
                    "Send a message here to provide input. Use `!stop` to interrupt, `!usage` for token usage."
                )
            except Exception:
                # Thread creation succeeded; welcome message is best-effort.
                pass

            logger.info(
                "Created Discord thread",
                session_id=session_id,
                thread_id=thread_id,
                name=session_name,
            )

            return {
                "thread_id": str(thread_id),
                "platform": "discord",
            }

        except Exception as e:
            logger.exception("Failed to create Discord thread", session_id=session_id)
            # Best-effort rollback if thread creation failed.
            if self._thread_names.get(session_id) == session_name:
                self._release_thread_name(session_id)
            raise RuntimeError(f"Failed to create Discord thread: {e}")

    async def on_session_removed(self, session_id: str) -> None:
        """Clean up when a session is deleted."""
        self._stop_typing(session_id)
        await super().on_session_removed(session_id)
