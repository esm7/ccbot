"""Tests for the fail-closed access gate (handlers/access_gate.py).

Runs real Telegram updates through the real Application from create_bot(),
with every non-gate handler callback replaced by a mock. A disallowed update
must reach none of them. The positive control proves each update kind does
match some handler, so the negative cases are not passing vacuously.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from telegram import Update, User
from telegram.ext import Application, ExtBot, MessageHandler, TypeHandler, filters

from ccbot.config import config
from ccbot.handlers.access_gate import ACCESS_GATE_GROUP, access_gate

ALLOWED_USER = 12345  # Set by the root conftest.
STRANGER = 99999
GROUP_ID = -1001234567890
OTHER_GROUP_ID = -1009999999999
ANON_ADMIN_ID = 1087968824  # Telegram's GroupAnonymousBot.
CHANNEL_BOT_ID = 136817688  # Telegram's Channel_Bot ("send as channel").
BOT_ID = 1


def _user(uid: int) -> dict:
    return {"id": uid, "is_bot": False, "first_name": "U"}


def _chat(chat_id: int, chat_type: str) -> dict:
    chat: dict = {"id": chat_id, "type": chat_type}
    if chat_type != "private":
        chat["title"] = "T"
        chat["is_forum"] = True
    return chat


def _message(uid: int, chat_id: int, chat_type: str, **extra) -> dict:
    msg = {
        "message_id": 1,
        "date": 0,
        "chat": _chat(chat_id, chat_type),
        "from": _user(uid),
    }
    if chat_type != "private":
        msg["message_thread_id"] = 42
        msg["is_topic_message"] = True
    msg.update(extra)
    return msg


def _cmd(name: str) -> dict:
    text = f"/{name}"
    return {
        "text": text,
        "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}],
    }


# Every kind of update a registered handler matches.
MESSAGE_KINDS: dict[str, dict] = {
    "text": {"text": "hello"},
    "start": _cmd("start"),
    "history": _cmd("history"),
    "screenshot": _cmd("screenshot"),
    "esc": _cmd("esc"),
    "unbind": _cmd("unbind"),
    "usage": _cmd("usage"),
    "forwarded_cmd": _cmd("clear"),
    "photo": {
        "photo": [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
    },
    "voice": {"voice": {"file_id": "f", "file_unique_id": "u", "duration": 1}},
    "location": {"location": {"latitude": 1.0, "longitude": 2.0}},
    "sticker": {
        "sticker": {
            "file_id": "f",
            "file_unique_id": "u",
            "type": "regular",
            "width": 1,
            "height": 1,
            "is_animated": False,
            "is_video": False,
        }
    },
    "topic_closed": {"forum_topic_closed": {}},
    "topic_edited": {"forum_topic_edited": {"name": "x"}},
}
KINDS = [*MESSAGE_KINDS, "callback"]


def _update(app: Application, kind: str, uid: int, chat_id: int, chat_type: str):
    if kind == "callback":
        data = {
            "update_id": 1,
            "callback_query": {
                "id": "1",
                "from": _user(uid),
                "chat_instance": "ci",
                "data": "anything",
                # The message a button sits on is the bot's own.
                "message": _message(BOT_ID, chat_id, chat_type, text="x"),
            },
        }
    else:
        data = {
            "update_id": 1,
            "message": _message(uid, chat_id, chat_type, **MESSAGE_KINDS[kind]),
        }
    return Update.de_json(data, app.bot)


@pytest.fixture
def app(monkeypatch) -> tuple[Application, list[AsyncMock]]:
    """The real bot application, with every non-gate callback mocked.

    initialize() is real except for getMe, which is stubbed to avoid the network.
    """
    from ccbot.bot import create_bot

    bot_user = User(id=BOT_ID, is_bot=True, first_name="Mike", username="mike_bot")

    async def fake_get_me(self, *args, **kwargs) -> User:
        self._bot_user = bot_user
        return bot_user

    monkeypatch.setattr(ExtBot, "get_me", fake_get_me)

    application = create_bot()
    mocks: list[AsyncMock] = []
    for group, handlers in application.handlers.items():
        if group == ACCESS_GATE_GROUP:
            continue
        for handler in handlers:
            mock = AsyncMock()
            handler.callback = mock
            mocks.append(mock)
    return application, mocks


def _run(application: Application, update: Update) -> None:
    async def go() -> None:
        await application.initialize()
        try:
            await application.process_update(update)
        finally:
            await application.shutdown()

    asyncio.run(go())


def _called(mocks: list[AsyncMock]) -> bool:
    return any(m.called for m in mocks)


class TestGateRegistration:
    def test_gate_is_alone_in_lowest_group(self, app) -> None:
        application, _ = app
        assert min(application.handlers) == ACCESS_GATE_GROUP
        gate_handlers = application.handlers[ACCESS_GATE_GROUP]
        assert len(gate_handlers) == 1
        handler = gate_handlers[0]
        assert isinstance(handler, TypeHandler)
        assert handler.type is Update
        assert handler.callback is access_gate

    def test_every_other_handler_runs_after_gate(self, app) -> None:
        application, _ = app
        others = [g for g in application.handlers if g != ACCESS_GATE_GROUP]
        assert others and all(g > ACCESS_GATE_GROUP for g in others)


class TestPositiveControl:
    @pytest.mark.parametrize("kind", KINDS)
    def test_allowed_user_in_group_reaches_a_handler(self, app, kind) -> None:
        application, mocks = app
        _run(
            application,
            _update(application, kind, ALLOWED_USER, GROUP_ID, "supergroup"),
        )
        assert _called(mocks), f"{kind} matched no handler; negative tests are vacuous"

    @pytest.mark.parametrize("kind", ["text", "start", "photo", "callback"])
    def test_allowed_user_in_own_private_chat(self, app, kind) -> None:
        application, mocks = app
        _run(
            application,
            _update(application, kind, ALLOWED_USER, ALLOWED_USER, "private"),
        )
        assert _called(mocks)


class TestGateBlocks:
    @pytest.mark.parametrize("kind", KINDS)
    @pytest.mark.parametrize(
        ("uid", "chat_id", "chat_type"),
        [
            pytest.param(STRANGER, GROUP_ID, "supergroup", id="stranger-in-group"),
            pytest.param(STRANGER, STRANGER, "private", id="stranger-private"),
            pytest.param(ANON_ADMIN_ID, GROUP_ID, "supergroup", id="anonymous-admin"),
            pytest.param(CHANNEL_BOT_ID, GROUP_ID, "supergroup", id="send-as-channel"),
            pytest.param(ALLOWED_USER, GROUP_ID, "channel", id="allowed-user-channel"),
            pytest.param(ALLOWED_USER, STRANGER, "private", id="foreign-private-chat"),
        ],
    )
    def test_disallowed_update_reaches_no_handler(
        self, app, kind, uid, chat_id, chat_type
    ) -> None:
        application, mocks = app
        _run(application, _update(application, kind, uid, chat_id, chat_type))
        assert not _called(mocks)

    @pytest.mark.parametrize("kind", KINDS)
    def test_group_outside_allowed_chats_is_blocked(
        self, app, kind, monkeypatch
    ) -> None:
        application, mocks = app
        monkeypatch.setattr(config, "allowed_chats", {GROUP_ID})
        update = _update(application, kind, ALLOWED_USER, OTHER_GROUP_ID, "supergroup")
        _run(application, update)
        assert not _called(mocks)

    def test_group_in_allowed_chats_passes(self, app, monkeypatch) -> None:
        application, mocks = app
        monkeypatch.setattr(config, "allowed_chats", {GROUP_ID})
        _run(
            application,
            _update(application, "text", ALLOWED_USER, GROUP_ID, "supergroup"),
        )
        assert _called(mocks)

    def test_handler_added_later_is_protected(self, app) -> None:
        """A future handler without its own check is still covered by the gate."""
        application, _ = app
        late = AsyncMock()
        application.add_handler(MessageHandler(filters.ALL, late), group=5)
        _run(
            application, _update(application, "text", STRANGER, GROUP_ID, "supergroup")
        )
        assert not late.called
