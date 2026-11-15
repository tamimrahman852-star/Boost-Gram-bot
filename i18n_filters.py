"""aiogram filters that match a button text in ANY language (reply-keyboard taps)."""
from typing import Optional
from aiogram.filters import Filter
from aiogram.types import Message

import i18n
from localization import L10N


class MenuText(Filter):
    """True when the message text is the main-menu button `key` in any language."""

    def __init__(self, key: str):
        self.key = key

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text in L10N.menu_variants(self.key)


class TextVariant(Filter):
    """True when the text is `en` (or its Bangla/translated forms)."""

    def __init__(self, en: str, bn: Optional[str] = None):
        self.en, self.bn = en, bn

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text in i18n.variants(self.en, self.bn)


def is_menu_text(text: Optional[str]) -> bool:
    """Used by the guard middleware: a menu tap always aborts a half-finished input flow."""
    if not text:
        return False
    for key in L10N.T["en"]:
        if key.startswith("menu_") and text in L10N.menu_variants(key):
            return True
    return text in i18n.variants("❌ Cancel", "❌ বাতিল")
