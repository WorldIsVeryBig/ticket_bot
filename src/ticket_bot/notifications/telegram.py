"""Telegram Bot API 通知"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def send_telegram(
    bot_token: str,
    chat_id: str,
    event_name: str,
    url: str = "",
    status: str = "搶票成功",
) -> None:
    """發送 Telegram 通知"""
    text = (
        f"<b>{status}</b>\n\n"
        f"活動：{event_name}\n"
    )
    if url:
        text += f"連結：{url}\n"
    if status == "搶票成功":
        text += "\n請在 10 分鐘內完成付款！"

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            api_url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("Telegram 通知已發送")
        else:
            logger.error("Telegram 發送失敗: %s", resp.text)
