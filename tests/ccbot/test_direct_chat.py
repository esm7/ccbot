"""Tests for direct chat mode (CCBOT_DIRECT_CHAT).

A plain private chat with the bot acts as one virtual topic with thread_id
DIRECT_CHAT_THREAD_ID (0). Covers thread resolution, the guarantee that 0 is
never sent to Telegram as message_thread_id, chat_id routing, the topic probe
skip, and that the allowlist still gates private chats.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.config import config
from ccbot.session import DIRECT_CHAT_THREAD_ID, SessionManager


@pytest.fixture
def direct_chat(monkeypatch):
    monkeypatch.setattr(config, "direct_chat", True)


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


def _make_update(
    chat_type: str, thread_id: int | None, user_id: int = 1, text: str = "hi"
) -> MagicMock:
    """Build a mock message Update in a chat of the given type."""
    update = MagicMock()
    update.callback_query = None
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.text = text
    update.message.message_thread_id = thread_id
    update.message.chat.type = chat_type
    update.message.chat.send_action = AsyncMock()
    update.effective_chat = MagicMock()
    update.effective_chat.type = chat_type
    update.effective_chat.id = user_id if chat_type == "private" else -100
    return update


def _make_callback_update(chat_type: str, thread_id: int | None) -> MagicMock:
    """Build a mock callback-query Update (no update.message)."""
    update = MagicMock()
    update.message = None
    update.callback_query.message.message_thread_id = thread_id
    update.callback_query.message.chat.type = chat_type
    return update


class TestGetThreadId:
    def test_private_chat_maps_to_direct_thread(self, direct_chat) -> None:
        from ccbot.bot import _get_thread_id

        assert _get_thread_id(_make_update("private", None)) == DIRECT_CHAT_THREAD_ID

    def test_private_chat_callback_maps_to_direct_thread(self, direct_chat) -> None:
        from ccbot.bot import _get_thread_id

        update = _make_callback_update("private", None)
        assert _get_thread_id(update) == DIRECT_CHAT_THREAD_ID

    def test_private_chat_without_setting_is_rejected(self, monkeypatch) -> None:
        from ccbot.bot import _get_thread_id

        monkeypatch.setattr(config, "direct_chat", False)
        assert _get_thread_id(_make_update("private", None)) is None

    @pytest.mark.parametrize("chat_type", ["group", "supergroup"])
    def test_group_without_topic_still_rejected(self, direct_chat, chat_type) -> None:
        from ccbot.bot import _get_thread_id

        assert _get_thread_id(_make_update(chat_type, None)) is None

    def test_general_topic_still_rejected(self, direct_chat) -> None:
        from ccbot.bot import _get_thread_id

        assert _get_thread_id(_make_update("supergroup", 1)) is None

    @pytest.mark.parametrize("chat_type", ["supergroup", "private"])
    def test_named_topic_unchanged(self, direct_chat, chat_type) -> None:
        """Real topics (incl. Bot API 9.3 private-chat topics) keep their id."""
        from ccbot.bot import _get_thread_id

        assert _get_thread_id(_make_update(chat_type, 42)) == 42


class TestThreadIdNeverSent:
    def test_send_kwargs_omits_direct_thread(self) -> None:
        from ccbot.handlers.message_queue import _send_kwargs

        assert _send_kwargs(DIRECT_CHAT_THREAD_ID) == {}
        assert _send_kwargs(None) == {}
        assert _send_kwargs(42) == {"message_thread_id": 42}

    @pytest.mark.asyncio
    async def test_safe_send_omits_direct_thread(self) -> None:
        from ccbot.handlers.message_sender import safe_send

        bot = AsyncMock()
        await safe_send(bot, 1, "x", message_thread_id=DIRECT_CHAT_THREAD_ID)
        assert "message_thread_id" not in bot.send_message.call_args.kwargs

    @pytest.mark.asyncio
    async def test_safe_send_keeps_real_thread(self) -> None:
        from ccbot.handlers.message_sender import safe_send

        bot = AsyncMock()
        await safe_send(bot, -100, "x", message_thread_id=42)
        assert bot.send_message.call_args.kwargs["message_thread_id"] == 42

    @pytest.mark.asyncio
    async def test_send_with_fallback_omits_direct_thread(self) -> None:
        from ccbot.handlers.message_sender import send_with_fallback

        bot = AsyncMock()
        await send_with_fallback(bot, 1, "x", message_thread_id=DIRECT_CHAT_THREAD_ID)
        assert "message_thread_id" not in bot.send_message.call_args.kwargs

    @pytest.mark.asyncio
    async def test_send_with_fallback_keeps_real_thread(self) -> None:
        from ccbot.handlers.message_sender import send_with_fallback

        bot = AsyncMock()
        await send_with_fallback(bot, -100, "x", message_thread_id=42)
        assert bot.send_message.call_args.kwargs["message_thread_id"] == 42

    @pytest.mark.asyncio
    async def test_interactive_ui_omits_direct_thread(
        self, sample_pane_settings: str
    ) -> None:
        from ccbot.handlers import interactive_ui

        bot = AsyncMock()
        bot.send_message.return_value = MagicMock(message_id=999)
        with (
            patch.object(interactive_ui, "tmux_manager") as mock_tmux,
            patch.object(interactive_ui, "session_manager") as mock_sm,
        ):
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock())
            mock_tmux.capture_pane = AsyncMock(return_value=sample_pane_settings)
            mock_sm.resolve_chat_id.return_value = 100
            try:
                await interactive_ui.handle_interactive_ui(
                    bot, user_id=100, window_id="@5", thread_id=DIRECT_CHAT_THREAD_ID
                )
            finally:
                interactive_ui._interactive_mode.clear()
                interactive_ui._interactive_msgs.clear()

        bot.send_message.assert_called_once()
        assert "message_thread_id" not in bot.send_message.call_args.kwargs


class TestResolveChatId:
    def test_direct_thread_routes_to_private_chat(self, mgr: SessionManager) -> None:
        """A group's no-topic chat_id stored as "user:0" must not capture the DM."""
        mgr.set_group_chat_id(100, None, -999)
        assert mgr.group_chat_ids.get("100:0") == -999
        assert mgr.resolve_chat_id(100, DIRECT_CHAT_THREAD_ID) == 100

    def test_real_thread_still_routes_to_group(self, mgr: SessionManager) -> None:
        mgr.set_group_chat_id(100, 42, -999)
        assert mgr.resolve_chat_id(100, 42) == -999


class TestBindingPersistence:
    def test_direct_thread_binding_survives_state_roundtrip(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(config, "state_file", tmp_path / "state.json")
        first = SessionManager()
        first.bind_thread(100, DIRECT_CHAT_THREAD_ID, "@3", window_name="notes")
        second = SessionManager()
        assert second.get_window_for_thread(100, DIRECT_CHAT_THREAD_ID) == "@3"


class TestTopicProbe:
    @pytest.mark.asyncio
    async def test_probe_skips_direct_thread(self) -> None:
        """The forum-topic probe must never run against the private chat."""
        from ccbot.handlers import status_polling

        bot = AsyncMock()
        with (
            patch.object(status_polling, "session_manager") as mock_sm,
            patch.object(status_polling, "tmux_manager") as mock_tmux,
            patch.object(status_polling, "update_status_message", AsyncMock()),
            patch.object(
                status_polling.asyncio,
                "sleep",
                AsyncMock(side_effect=asyncio.CancelledError),
            ),
        ):
            mock_sm.iter_thread_bindings.return_value = [
                (100, DIRECT_CHAT_THREAD_ID, "@3"),
                (100, 42, "@4"),
            ]
            mock_sm.resolve_chat_id.side_effect = lambda uid, tid: -999
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock())
            with pytest.raises(asyncio.CancelledError):
                await status_polling.status_poll_loop(bot)

        probed = [
            c.kwargs["message_thread_id"]
            for c in bot.unpin_all_forum_topic_messages.call_args_list
        ]
        assert probed == [42]
        mock_sm.unbind_thread.assert_not_called()


class TestTextHandlerInPrivateChat:
    @pytest.mark.asyncio
    async def test_unbound_private_chat_offers_session_setup(self, direct_chat) -> None:
        """First message in the DM opens the browser instead of a topic error."""
        from ccbot import bot as bot_mod

        update = _make_update("private", None, user_id=12345)
        context = MagicMock()
        context.bot = AsyncMock()
        context.user_data = {}
        with (
            patch.object(bot_mod, "session_manager") as mock_sm,
            patch.object(bot_mod, "tmux_manager") as mock_tmux,
            patch.object(bot_mod, "safe_reply", new_callable=AsyncMock) as reply,
        ):
            mock_sm.get_window_for_thread.return_value = None
            mock_sm.iter_thread_bindings.return_value = []
            mock_tmux.list_windows = AsyncMock(return_value=[])
            await bot_mod.text_handler(update, context)

        assert context.user_data["_pending_thread_id"] == DIRECT_CHAT_THREAD_ID
        assert context.user_data["_pending_thread_text"] == "hi"
        assert "named topic" not in reply.call_args.args[1]
        mock_sm.set_group_chat_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_bound_private_chat_forwards_to_window(self, direct_chat) -> None:
        from ccbot import bot as bot_mod

        update = _make_update("private", None, user_id=12345)
        context = MagicMock()
        context.bot = AsyncMock()
        context.user_data = {}
        with (
            patch.object(bot_mod, "session_manager") as mock_sm,
            patch.object(bot_mod, "tmux_manager") as mock_tmux,
            patch.object(bot_mod, "safe_reply", new_callable=AsyncMock),
            patch.object(bot_mod, "enqueue_status_update", new_callable=AsyncMock),
        ):
            mock_sm.get_window_for_thread.return_value = "@3"
            mock_tmux.find_window_by_id = AsyncMock(return_value=MagicMock())
            mock_tmux.capture_pane = AsyncMock(return_value="")
            mock_sm.send_to_window = AsyncMock(return_value=(True, "ok"))
            await bot_mod.text_handler(update, context)

        mock_sm.get_window_for_thread.assert_called_with(12345, DIRECT_CHAT_THREAD_ID)
        mock_sm.send_to_window.assert_called_once_with("@3", "hi")

    @pytest.mark.asyncio
    async def test_unauthorized_user_in_private_chat_is_ignored(
        self, direct_chat
    ) -> None:
        """Direct chat mode must not open the bot to anyone outside ALLOWED_USERS."""
        from ccbot import bot as bot_mod

        update = _make_update("private", None, user_id=99999)
        context = MagicMock()
        context.bot = AsyncMock()
        context.user_data = {}
        with (
            patch.object(bot_mod, "session_manager") as mock_sm,
            patch.object(bot_mod, "tmux_manager") as mock_tmux,
            patch.object(bot_mod, "safe_reply", new_callable=AsyncMock),
        ):
            mock_sm.send_to_window = AsyncMock()
            mock_tmux.list_windows = AsyncMock(return_value=[])
            await bot_mod.text_handler(update, context)

        mock_sm.send_to_window.assert_not_called()
        mock_sm.bind_thread.assert_not_called()
        mock_sm.get_window_for_thread.assert_not_called()
        mock_tmux.list_windows.assert_not_called()
        assert context.user_data == {}
