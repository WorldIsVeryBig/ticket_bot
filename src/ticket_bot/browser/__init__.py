"""瀏覽器引擎抽象層"""

from ticket_bot.browser.base import BrowserEngine, PageWrapper
from ticket_bot.browser.factory import create_engine

__all__ = ["BrowserEngine", "PageWrapper", "create_engine"]
