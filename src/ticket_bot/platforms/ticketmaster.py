"""Ticketmaster Discovery API 監控 — 事件搜尋與票務追蹤"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from ticket_bot.config import AppConfig

logger = logging.getLogger(__name__)

BASE_URL = "https://app.ticketmaster.com/discovery/v2"
# API 限制：5,000 calls/day, 5 requests/second
REQUEST_INTERVAL = 0.25  # 每秒最多 4 次（保守）


class TicketmasterMonitor:
    """Ticketmaster Discovery API 事件監控器"""

    def __init__(self, config: AppConfig):
        self.api_key = config.ticketmaster_api_key
        if not self.api_key:
            raise ValueError("缺少 TICKETMASTER_API_KEY，請在 .env 中設定")
        self.config = config

    async def search_events(
        self,
        keyword: str,
        city: str = "",
        country_code: str = "",
        size: int = 20,
    ) -> list[dict]:
        """搜尋活動"""
        params = {
            "keyword": keyword,
            "apikey": self.api_key,
            "size": size,
        }
        if city:
            params["city"] = city
        if country_code:
            params["countryCode"] = country_code

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{BASE_URL}/events.json", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

        events = data.get("_embedded", {}).get("events", [])
        logger.info("搜尋 '%s' 找到 %d 個活動", keyword, len(events))
        return events

    async def get_event_detail(self, event_id: str) -> dict:
        """取得單一活動詳情"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/events/{event_id}.json",
                params={"apikey": self.api_key},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()

    async def check_onsale_status(self, event_id: str) -> dict:
        """檢查活動售票狀態"""
        detail = await self.get_event_detail(event_id)
        dates = detail.get("dates", {})
        sales = detail.get("sales", {})
        status = dates.get("status", {}).get("code", "unknown")

        public_sale = sales.get("public", {})
        onsale_start = public_sale.get("startDateTime", "")

        price_ranges = detail.get("priceRanges", [])
        prices = []
        for pr in price_ranges:
            prices.append({
                "type": pr.get("type", ""),
                "min": pr.get("min"),
                "max": pr.get("max"),
                "currency": pr.get("currency", ""),
            })

        return {
            "event_id": event_id,
            "name": detail.get("name", ""),
            "status": status,
            "onsale_start": onsale_start,
            "prices": prices,
        }

    @staticmethod
    def format_event(event: dict) -> str:
        """格式化活動資訊為可讀文字"""
        name = event.get("name", "未知")
        dates = event.get("dates", {}).get("start", {})
        date_str = dates.get("localDate", "")
        time_str = dates.get("localTime", "")
        venue = ""
        embedded = event.get("_embedded", {})
        venues = embedded.get("venues", [])
        if venues:
            venue = venues[0].get("name", "")

        status = event.get("dates", {}).get("status", {}).get("code", "")
        return f"{name} | {date_str} {time_str} | {venue} | 狀態: {status}"

    async def monitor_keywords(
        self,
        keywords: list[str],
        interval: float = 60.0,
        on_found=None,
    ) -> None:
        """
        持續監控關鍵字，發現新活動或狀態變更時觸發 callback。

        Args:
            keywords: 要監控的關鍵字列表
            interval: 檢查間隔（秒）
            on_found: async callback(event_info: dict)
        """
        seen_events: dict[str, str] = {}  # event_id -> last_status
        logger.info("開始監控 %d 個關鍵字，間隔 %.0f 秒", len(keywords), interval)

        while True:
            for kw in keywords:
                try:
                    events = await self.search_events(kw)
                    for ev in events:
                        eid = ev.get("id", "")
                        status = ev.get("dates", {}).get("status", {}).get("code", "")
                        prev_status = seen_events.get(eid)

                        if prev_status is None or prev_status != status:
                            seen_events[eid] = status
                            info = {
                                "event_id": eid,
                                "name": ev.get("name", ""),
                                "status": status,
                                "url": ev.get("url", ""),
                                "formatted": self.format_event(ev),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                            if prev_status is not None:
                                logger.info("狀態變更: %s → %s | %s", prev_status, status, info["name"])
                            else:
                                logger.info("發現活動: %s | %s", status, info["name"])

                            if on_found:
                                await on_found(info)

                    await asyncio.sleep(REQUEST_INTERVAL)
                except httpx.HTTPStatusError as e:
                    logger.error("API 錯誤: %s", e)
                    await asyncio.sleep(5)
                except Exception:
                    logger.exception("監控迴圈錯誤")
                    await asyncio.sleep(5)

            await asyncio.sleep(interval)
