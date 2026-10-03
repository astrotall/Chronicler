from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.messages import ACTION_LABELS
from app.config.constants import DRAFT_CALLBACK_PREFIX, KEYBOARD_ROW_WIDTH
from app.domain.pipeline import PostAction


class DraftCallback(CallbackData, prefix=DRAFT_CALLBACK_PREFIX):
    action: PostAction
    draft_id: str


def draft_keyboard(draft_id: str, actions: Sequence[PostAction]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=ACTION_LABELS[action],
            callback_data=DraftCallback(action=action, draft_id=draft_id).pack(),
        )
        for action in actions
    ]
    rows = [
        buttons[start : start + KEYBOARD_ROW_WIDTH]
        for start in range(0, len(buttons), KEYBOARD_ROW_WIDTH)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
