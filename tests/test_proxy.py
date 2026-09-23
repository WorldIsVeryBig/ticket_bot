"""proxy manager 測試"""

from ticket_bot.config import AppConfig, BrowserConfig, EventConfig, ProxyConfig
from ticket_bot.platforms.tixcraft import TixcraftBot
from ticket_bot.proxy.manager import ProxyManager


def test_no_proxy():
    """proxy 未啟用"""
    cfg = ProxyConfig(enabled=False, servers=[])
    mgr = ProxyManager(cfg)
    assert mgr.available is False
    assert mgr.next() is None
    assert mgr.get_browser_arg() is None
    assert mgr.get_playwright_config() is None


def test_single_proxy():
    """單一 proxy"""
    cfg = ProxyConfig(enabled=True, rotate=False, servers=["http://proxy1:8080"])
    mgr = ProxyManager(cfg)
    assert mgr.available is True
    assert mgr.next() == "http://proxy1:8080"
    assert mgr.next() == "http://proxy1:8080"  # rotate=False 永遠第一個


def test_round_robin():
    """proxy 輪替"""
    cfg = ProxyConfig(enabled=True, rotate=True, servers=["http://a:80", "http://b:80", "http://c:80"])
    mgr = ProxyManager(cfg)
    results = [mgr.next() for _ in range(6)]
    assert results == ["http://a:80", "http://b:80", "http://c:80", "http://a:80", "http://b:80", "http://c:80"]


def test_shared_config_round_robin_across_bots():
    """同一份 config 建多個 bot 時，proxy 也要持續輪替"""
    cfg = AppConfig(
        browser=BrowserConfig(engine="nodriver"),
        proxy=ProxyConfig(enabled=True, rotate=True, servers=["http://a:80", "http://b:80"]),
        events=[EventConfig(name="ITZY", platform="tixcraft", url="https://tixcraft.com/activity/game/26_itzy")],
    )
    event = cfg.events[0]

    first = TixcraftBot(cfg, event)
    second = TixcraftBot(cfg, event)
    third = TixcraftBot(cfg, event)

    assert first._proxy_server == "http://a:80"
    assert second._proxy_server == "http://b:80"
    assert third._proxy_server == "http://a:80"


def test_browser_arg():
    """get_browser_arg 格式"""
    cfg = ProxyConfig(enabled=True, servers=["http://proxy:3128"])
    mgr = ProxyManager(cfg)
    assert mgr.get_browser_arg() == "--proxy-server=http://proxy:3128"


def test_playwright_config_simple():
    """Playwright config — 不含帳密"""
    cfg = ProxyConfig(enabled=True, servers=["http://proxy:3128"])
    mgr = ProxyManager(cfg)
    result = mgr.get_playwright_config()
    assert result == {"server": "http://proxy:3128"}


def test_playwright_config_with_auth():
    """Playwright config — 含帳密"""
    cfg = ProxyConfig(enabled=True, servers=["http://user:pass@proxy:3128"])
    mgr = ProxyManager(cfg)
    result = mgr.get_playwright_config()
    assert result["server"] == "http://proxy:3128"
    assert result["username"] == "user"
    assert result["password"] == "pass"
