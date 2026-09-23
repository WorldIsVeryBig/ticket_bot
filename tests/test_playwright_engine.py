from ticket_bot.browser.playwright_engine import PlaywrightEngine


def test_build_proxy_config_simple():
    assert PlaywrightEngine._build_proxy_config("http://proxy.example:3128") == {
        "server": "http://proxy.example:3128",
    }


def test_build_proxy_config_with_auth():
    assert PlaywrightEngine._build_proxy_config("http://user:pass@proxy.example:3128") == {
        "server": "http://proxy.example:3128",
        "username": "user",
        "password": "pass",
    }
