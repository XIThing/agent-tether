"""Slack bridge implementation with command handling and session threading."""

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
from agent_tether.text_command_bridge import TextCommandBridge
from pathlib import Path

logger = structlog.get_logger(__name__)


class SlackBridge(TextCommandBridge):
    """Slack bridge that routes agent events to Slack threads.

    Commands (in main channel): !help, !status, !list, !attach, !stop, !usage
    Session input: messages in session threads are forwarded as input.
    """

    def __init__(
        self,
        bot_token: str,
        channel_id: str,
        slack_app_token: str | None = None,
        config: BridgeConfig | None = None,
        callbacks: BridgeCallbacks | None = None,
        get_session_directory: GetSessionDirectory | None = None,
        get_session_info: GetSessionInfo | None = None,
        on_session_bound: OnSessionBound | None = None,
    ):
        data_dir = (config or BridgeConfig()).data_dir or "."
        super().__init__(
            config=config,
            callbacks=callbacks,
            get_session_directory=get_session_directory,
            get_session_info=get_session_info,
            on_session_bound=on_session_bound,
            thread_name_path=Path(data_dir) / "slack_threads.json",
        )
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._slack_app_token = slack_app_token
        self._client: Any = None
        self._app: Any = None
        self._thread_ts: dict[str, str] = {}  # session_id -> thread_ts

    def restore_thread_mappings(self, sessions: list[dict] | None = None) -> None:
        """Restore session-to-thread mappings after restart.

        Args:
            sessions: List of session dicts with 'id', 'platform', 'platform_thread_id'.
                      If None, no mappings are restored.
        """
        if not sessions:
            return
        for session in sessions:
            if session.get("platform") == "slack" and session.get("platform_thread_id"):
                self._thread_ts[session["id"]] = session["platform_thread_id"]

    async def start(self) -> None:
        """Initialize Slack client and socket mode."""
        try:
            from slack_sdk.web.async_client import AsyncWebClient
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import (
                AsyncSocketModeHandler,
            )
        except ImportError:
            logger.error(
                "slack_sdk or slack_bolt not installed. Install with: pip install slack-sdk slack-bolt"
            )
            return

        self._client = AsyncWebClient(token=self._bot_token)

        # Check if socket mode is available
        app_token = self._slack_app_token
        if app_token:
            try:
                self._app = AsyncApp(token=self._bot_token)

                @self._app.event("message")
                async def handle_message(event: dict, say: Any) -> None:
                    await self._handle_message(event)

                handler = AsyncSocketModeHandler(self._app, app_token)
                import asyncio

                asyncio.create_task(handler.start_async())

                logger.info(
                    "Slack bridge initialized with socket mode",
                    channel_id=self._channel_id,
                )
            except Exception:
                logger.exception(
                    "Failed to initialize Slack socket mode, falling back to basic mode"
                )
                logger.info(
                    "Slack bridge initialized (basic mode, no input forwarding)",
                    channel_id=self._channel_id,
                )
        else:
            logger.info(
                "Slack bridge initialized (basic mode — set SLACK_APP_TOKEN for commands and input)",
                channel_id=self._channel_id,
            )

    async def stop(self) -> None:
        """Stop Slack client."""
        if self._client:
            await self._client.close()
        logger.info("Slack bridge stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _reply(self, event: dict, text: str) -> None:
        """Send a reply to the channel/thread where the event originated."""
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

    def _session_for_thread(self, thread_ts: str) -> str | None:
        """Look up the session ID for a Slack thread timestamp."""
        for sid, ts in self._thread_ts.items():
            if ts == thread_ts:
                return sid
        return None

    # ------------------------------------------------------------------
    # Message router
    # ------------------------------------------------------------------

    async def _handle_message(self, event: dict) -> None:
        """Route incoming Slack messages to commands or session input."""
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        text = event.get("text", "").strip()
        if not text:
            return

        thread_ts = event.get("thread_ts")

        # Messages in threads -> session input or thread commands
        if thread_ts:
            if text.startswith("!"):
                await self._dispatch_command(event, text)
                return
            session_id = self._session_for_thread(thread_ts)
            if not session_id:
                return
            await self._forward_input(event, session_id, text)
            return

        # Top-level messages starting with ! -> commands
        if text.startswith("!"):
            await self._dispatch_command(event, text)

    async def _dispatch_command(self, event: dict, text: str) -> None:
        """Parse and dispatch a !command."""
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("!help", "!start"):
            await self._cmd_help(event)
        elif cmd in ("!status", "!sessions"):
            await self._cmd_status(event)
        elif cmd == "!list":
            await self._cmd_list(event, args)
        elif cmd == "!attach":
            await self._cmd_attach(event, args)
        elif cmd == "!new":
            await self._cmd_new(event, args)
        elif cmd == "!stop":
            await self._cmd_stop(event)
        elif cmd == "!usage":
            await self._cmd_usage(event)
        else:
            await self._reply(event, f"Unknown command: {cmd}\nUse !help for available commands.")

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _cmd_help(self, event: dict) -> None:
        """Handle !help."""
        text = (
            "Tether Commands:\n\n"
            "!status — List all sessions\n"
            "!list [page|search] — List external sessions (Claude Code, Codex)\n"
            "!attach <number> — Attach to an external session\n"
            "!new [agent] [directory] — Start a new session\n"
            "!stop — Interrupt the session in this thread\n"
            "!usage — Show token usage and cost for this session\n"
            "!help — Show this help\n\n"
            "Send a text message in a session thread to forward it as input."
        )
        await self._reply(event, text)

    async def _cmd_new(self, event: dict, args: str) -> None:
        """Create a new session and Slack thread."""
        thread_ts = event.get("thread_ts")
        base_session_id = self._session_for_thread(thread_ts) if thread_ts else None

        try:
            adapter, directory = await self._parse_new_args(args, base_session_id=base_session_id)
        except ValueError as e:
            await self._reply(event, str(e))
            return
        except Exception as e:
            await self._reply(event, f"Invalid directory: {e}")
            return

        dir_short = directory.rstrip("/").rsplit("/", 1)[-1] or "Session"
        agent_label = (
            self._adapter_label(adapter)
            or self._adapter_label(self._config.default_adapter)
            or "Claude"
        )
        session_name = self._make_external_thread_name(directory=directory, session_id="")

        try:
            await self._create_session_via_api(
                directory=directory,
                platform="slack",
                adapter=adapter,
                session_name=session_name,
            )
        except Exception as e:
            await self._reply(event, f"Failed to create session: {e}")
            return

        await self._reply(event, f"✅ New {agent_label} session created in {dir_short}.")

    async def _cmd_status(self, event: dict) -> None:
        """Handle !status."""
        try:
            sessions = await self._callbacks.list_sessions()
        except Exception:
            logger.exception("Failed to fetch sessions for !status")
            await self._reply(event, "Failed to fetch sessions.")
            return

        if not sessions:
            await self._reply(event, "No sessions.")
            return

        lines = ["Sessions:\n"]
        for s in sessions:
            emoji = self._STATE_EMOJI.get(s.get("state", ""), "❓")
            name = s.get("name") or s.get("id", "")[:12]
            lines.append(f"  {emoji} {name}")
        await self._reply(event, "\n".join(lines))

    async def _cmd_list(self, event: dict, args: str) -> None:
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
            await self._reply(event, "Failed to list external sessions.")
            return

        text, _, _ = self._format_external_page(page)
        await self._reply(event, text)

    async def _cmd_attach(self, event: dict, args: str) -> None:
        """Handle !attach."""
        if not args:
            await self._reply(event, "Usage: !attach <number>\n\nRun !list first.")
            return

        try:
            index = int(args.split()[0]) - 1
        except ValueError:
            await self._reply(event, "Please provide a session number.")
            return

        if not self._cached_external:
            await self._reply(event, "No external sessions cached. Run !list first.")
            return
        if not self._external_view:
            await self._reply(event, "No external sessions listed. Run !list first.")
            return
        if index < 0 or index >= len(self._external_view):
            await self._reply(event, f"Invalid number. Use 1–{len(self._external_view)}.")
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
            if session_id in self._thread_ts:
                await self._reply(event, "Already attached, check the existing thread.")
                return

            # Create thread
            session_name = self._make_external_thread_name(
                directory=external.get("directory", ""),
                session_id=session_id,
            )
            thread_info = await self.create_thread(session_id, session_name)
            try:
                thread_ts = str(thread_info.get("thread_ts") or thread_info.get("thread_id") or "")
                if thread_ts:
                    replay = await self._format_external_replay(
                        external["id"], str(external["runner_type"])
                    )
                    if replay:
                        try:
                            await self._client.chat_postMessage(
                                channel=self._channel_id,
                                thread_ts=thread_ts,
                                text=replay,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to send Slack external session replay",
                                external_id=external["id"],
                            )
            except Exception:
                logger.exception("Failed to replay external session history into Slack thread")

            # Bind platform
            if self._on_session_bound:
                await self._on_session_bound(session_id, "slack", thread_info.get("thread_id"))

            dir_short = external.get("directory", "").rsplit("/", 1)[-1]
            await self._reply(
                event,
                f"✅ Attached to {external['runner_type']} session in {dir_short}\n\n"
                f"A new thread has been created. Send messages there to interact.",
            )

        except Exception as e:
            logger.warning("Failed to attach to external session", error=str(e))
            await self._reply(event, f"Failed to attach: {e}")

    async def _cmd_stop(self, event: dict) -> None:
        """Handle !stop."""
        thread_ts = event.get("thread_ts")
        if not thread_ts:
            await self._reply(event, "Use this command inside a session thread.")
            return

        session_id = self._session_for_thread(thread_ts)
        if not session_id:
            await self._reply(event, "No session linked to this thread.")
            return

        try:
            await self._callbacks.stop_session(session_id)
            await self._reply(event, "⏹️ Session interrupted.")
        except Exception as e:
            logger.exception("Failed to interrupt session")
            await self._reply(event, f"Failed to interrupt: {e}")

    async def _cmd_usage(self, event: dict) -> None:
        """Show token usage for the session in the current thread."""
        thread_ts = event.get("thread_ts")
        if not thread_ts:
            await self._reply(event, "Use this command inside a session thread.")
            return

        session_id = self._session_for_thread(thread_ts)
        if not session_id:
            await self._reply(event, "No session linked to this thread.")
            return

        try:
            usage = await self._fetch_usage(session_id)
            await self._reply(event, f"📊 {self._format_usage_text(usage)}")
        except Exception as e:
            logger.exception("Failed to get usage")
            await self._reply(event, f"Failed to get usage: {e}")

    # ------------------------------------------------------------------
    # Session input forwarding
    # ------------------------------------------------------------------

    async def _forward_input(self, event: dict, session_id: str, text: str) -> None:
        """Forward a user message as session input, handling approvals."""
        pending = self.get_pending_permission(session_id)
        if pending:
            # Choice requests: allow "1"/"2"/... or exact label.
            if pending.kind == "choice":
                selected = self.parse_choice_text(session_id, text)
                if selected:
                    await self._send_input_or_start_via_api(session_id=session_id, text=selected)
                    self.clear_pending_permission(session_id)
                    await self._reply(event, f"✅ Selected: {selected}")
                    return

            parsed = self.parse_approval_text(text)
            if parsed is not None:
                ok, message = await self._handle_approval_text_response(
                    session_id, pending, parsed
                )
                if ok:
                    emoji = "✅" if parsed["allow"] else "❌"
                    await self._reply(event, f"{emoji} {message}")
                else:
                    await self._reply(event, "❌ Failed. Request may have expired.")
                return

        try:
            await self._send_input_or_start_via_api(session_id=session_id, text=text)
            logger.info(
                "Forwarded human input from Slack",
                session_id=session_id,
                user=event.get("user"),
            )
        except Exception:
            logger.exception("Failed to forward human input", session_id=session_id)
            await self._reply(event, "Failed to send input.")

    # ------------------------------------------------------------------
    # Bridge interface (outgoing events)
    # ------------------------------------------------------------------

    async def on_output(self, session_id: str, text: str, metadata: dict | None = None) -> None:
        """Send output text to Slack thread."""
        if not self._client:
            logger.warning("Slack client not initialized")
            return

        thread_ts = self._thread_ts.get(session_id)
        if not thread_ts:
            logger.warning("No Slack thread for session", session_id=session_id)
            return

        try:
            await self._client.chat_postMessage(
                channel=self._channel_id,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception:
            logger.exception("Failed to send Slack message", session_id=session_id)

    async def send_auto_approve_batch(self, session_id: str, items: list[tuple[str, str]]) -> None:
        """Send a batched auto-approve notification to Slack."""
        if not self._client:
            return
        thread_ts = self._thread_ts.get(session_id)
        if not thread_ts:
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
                thread_ts=thread_ts,
                text=text,
            )
        except Exception:
            pass

    async def on_approval_request(self, session_id: str, request: ApprovalRequest) -> None:
        """Send an approval request to Slack thread."""
        if not self._client:
            return

        # Choice requests: send options and let user reply with "1"/"2"/... or the label.
        if request.kind == "choice":
            thread_ts = self._thread_ts.get(session_id)
            if not thread_ts:
                return
            self.set_pending_permission(session_id, request)
            options = "\n".join([f"{i}. {o}" for i, o in enumerate(request.options, start=1)])
            text = (
                f"*⚠️ {request.title}*\n\n{request.description}\n\n{options}\n\n"
                "Reply with a number (e.g. `1`) or an exact option label."
            )
            try:
                await self._client.chat_postMessage(
                    channel=self._channel_id,
                    thread_ts=thread_ts,
                    text=text,
                )
            except Exception:
                logger.exception("Failed to send Slack choice request", session_id=session_id)
            return

        reason: str | None = None
        if request.kind == "permission":
            reason = self.check_auto_approve(session_id, request.title)
        if reason:
            await self._auto_approve(session_id, request, reason=reason)
            self.buffer_auto_approve_notification(session_id, request.title, reason)
            return

        thread_ts = self._thread_ts.get(session_id)
        if not thread_ts:
            return

        self.set_pending_permission(session_id, request)

        formatted = self.format_tool_input_markdown(request.description)
        text = (
            f"*⚠️ Approval Required*\n\n*{request.title}*\n\n{formatted}\n\n"
            "Reply with `allow`/`proceed`, `deny`/`cancel`, `deny: <reason>`, `allow all`, or `allow {tool}`."
        )
        try:
            await self._client.chat_postMessage(
                channel=self._channel_id,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception:
            logger.exception("Failed to send Slack approval request", session_id=session_id)

    async def on_status_change(
        self, session_id: str, status: str, metadata: dict | None = None
    ) -> None:
        """Send status change to Slack thread."""
        if not self._client:
            return

        if status == "error" and not self._should_send_error_status(session_id):
            return

        thread_ts = self._thread_ts.get(session_id)
        if not thread_ts:
            return

        emoji_map = {
            "thinking": ":thought_balloon:",
            "executing": ":gear:",
            "done": ":white_check_mark:",
            "error": ":x:",
        }
        emoji = emoji_map.get(status, ":information_source:")
        text = f"{emoji} Status: {status}"

        try:
            await self._client.chat_postMessage(
                channel=self._channel_id,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception:
            logger.exception("Failed to send Slack status", session_id=session_id)

    async def create_thread(self, session_id: str, session_name: str) -> dict:
        """Create a Slack thread for a session."""
        if not self._client:
            raise RuntimeError("Slack client not initialized")

        try:
            self._reserve_thread_name(session_id, session_name)

            response = await self._client.chat_postMessage(
                channel=self._channel_id,
                text=f"*New Session:* {session_name}",
            )

            if not response["ok"]:
                raise RuntimeError(f"Slack API error: {response}")

            thread_ts = response["ts"]
            self._thread_ts[session_id] = thread_ts

            logger.info(
                "Created Slack thread",
                session_id=session_id,
                thread_ts=thread_ts,
                name=session_name,
            )

            return {
                "thread_id": thread_ts,
                "platform": "slack",
                "thread_ts": thread_ts,
            }

        except Exception as e:
            logger.exception("Failed to create Slack thread", session_id=session_id)
            # Best-effort rollback if thread creation failed.
            if self._thread_names.get(session_id) == session_name:
                self._release_thread_name(session_id)
            raise RuntimeError(f"Failed to create Slack thread: {e}")
