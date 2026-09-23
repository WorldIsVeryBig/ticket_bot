"""根據設定建立對應的瀏覽器引擎"""

from __future__ import annotations

from ticket_bot.browser.base import BrowserEngine


def create_engine(engine_name: str) -> BrowserEngine:
    """
    工廠函式：根據名稱建立瀏覽器引擎。

    Args:
        engine_name: "nodriver" 或 "playwright"
    """
    name = engine_name.lower().strip()

    if name == "nodriver":
        from ticket_bot.browser.nodriver_engine import NodriverEngine
        return NodriverEngine()

    if name == "playwright":
        from ticket_bot.browser.playwright_engine import PlaywrightEngine
        return PlaywrightEngine()

    raise ValueError(
        f"不支援的瀏覽器引擎: {engine_name!r}，可選: nodriver, playwright"
    )
