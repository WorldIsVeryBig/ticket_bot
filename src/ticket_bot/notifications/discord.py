"""Discord Webhook 通知"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def send_discord(
    webhook_url: str,
    event_name: str,
    url: str = "",
    status: str = "搶票成功",
    platform: str = "tixcraft",
) -> None:
    """發送 Discord Rich Embed 通知"""
    color = 0x00C851 if status == "搶票成功" else 0x03B2F8

    description = f"狀態：**{status}**"
    if url:
        description += f"\n[前往購票]({url})"
    if status == "搶票成功":
        description += "\n\n請在 10 分鐘內完成付款！"

    payload = {
        "username": "Ticket Bot",
        "embeds": [
            {
                "title": event_name,
                "description": description,
                "color": color,
                "fields": [
                    {"name": "平台", "value": platform, "inline": True},
                    {"name": "狀態", "value": status, "inline": True},
                ],
            }
        ],
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            logger.info("Discord 通知已發送")
        else:
            logger.error("Discord 發送失敗: %s %s", resp.status_code, resp.text)
