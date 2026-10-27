"""Single fail-closed access gate in front of every other handler.

Registered in handler group ACCESS_GATE_GROUP (-1), which python-telegram-bot
runs before group 0 where all other handlers live. An update from a sender not
in ALLOWED_USERS, or from a chat not allowed, raises ApplicationHandlerStop,
so no later handler ever sees it. Any handler added later, including by an
upstream merge, is therefore protected without needing its own check. The
per-handler checks in bot.py stay as a second layer.

Chat rules:
  - private: only the sender's own chat (chat.id == user.id).
  - group/supergroup: if CCBOT_ALLOWED_CHATS is set, only those chat IDs;
    otherwise any group (the sender check still applies).
  - anything else (channels, unknown types): rejected.

Rejections are silent: no reply is sent, so the bot does not confirm its
existence to strangers.
"""

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from ..config import config

logger = logging.getLogger(__name__)

ACCESS_GATE_GROUP = -1


def is_update_allowed(update: Update) -> bool:
    """Return True only if both the sender and the chat are allowed."""
    user = update.effective_user
    if user is None or not config.is_user_allowed(user.id):
        return False

    chat = update.effective_chat
    if chat is None:
        # Only callback queries on inline-mode messages lack a chat; ccbot
        # never sends those, so treat it as foreign.
        return False
    if chat.type == "private":
        return chat.id == user.id
    if chat.type in ("group", "supergroup"):
        return not config.allowed_chats or chat.id in config.allowed_chats
    return False


async def access_gate(update: object, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop processing of any update that is not allowed."""
    if isinstance(update, Update) and is_update_allowed(update):
        return
    if isinstance(update, Update):
        user = update.effective_user
        chat = update.effective_chat
        logger.warning(
            "Access gate dropped update %s (user=%s, chat=%s type=%s)",
            update.update_id,
            user.id if user else None,
            chat.id if chat else None,
            chat.type if chat else None,
        )
    else:
        logger.warning("Access gate dropped non-Update object: %r", type(update))
    raise ApplicationHandlerStop
