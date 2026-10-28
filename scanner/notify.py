"""Telegram 推播。例外訊息不會帶出含有 Bot Token 的網址。"""

from __future__ import annotations

import os

import requests

TELEGRAM_LIMIT = 3900


def send_message(text: str) -> None:
    if os.getenv("DRY_RUN") == "1":
        print(text)
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in _chunks(text):
        try:
            response = requests.post(
                url,
                json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                timeout=30,
            )
        except requests.RequestException:
            raise RuntimeError("Telegram 連線失敗") from None
        if not response.ok:
            detail = response.text[:300]
            raise RuntimeError(f"Telegram 回應 {response.status_code}：{detail}")


def _chunks(text: str) -> list[str]:
    if len(text) <= TELEGRAM_LIMIT:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        extra = len(line) + 1
        if current and size + extra > TELEGRAM_LIMIT:
            chunks.append("\n".join(current))
            current = [line]
            size = extra
        else:
            current.append(line)
            size += extra
    if current:
        chunks.append("\n".join(current))
    return chunks
