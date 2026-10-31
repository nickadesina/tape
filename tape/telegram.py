"""Telegram Bot API: the operator's only notification channel.

Without TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID set, messages are logged
instead of sent, so every job can run in development.
"""

from __future__ import annotations

import logging

import httpx

from .settings import Env

log = logging.getLogger("tape.telegram")
CAPTION_MAX = 1024


class Telegram:
    def __init__(self, env: Env):
        self.token = env.telegram_token
        self.chat = env.telegram_chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat)

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def message(self, text: str) -> None:
        if not self.enabled:
            log.info("[telegram disabled] %s", text)
            return
        r = httpx.post(self._url("sendMessage"), data={"chat_id": self.chat, "text": text,
                                                       "disable_web_page_preview": "true"}, timeout=30)
        r.raise_for_status()

    def photo(self, png: bytes, caption: str) -> None:
        if not self.enabled:
            log.info("[telegram disabled] photo (%d bytes): %s", len(png), caption)
            return
        if len(caption) > CAPTION_MAX:
            caption = caption[: CAPTION_MAX - 1] + "…"
        r = httpx.post(self._url("sendPhoto"), data={"chat_id": self.chat, "caption": caption},
                       files={"photo": ("tape.png", png, "image/png")}, timeout=60)
        r.raise_for_status()
