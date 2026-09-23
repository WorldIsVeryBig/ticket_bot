"""browser factory 測試"""

import pytest

from ticket_bot.browser.factory import create_engine
from ticket_bot.browser.nodriver_engine import NodriverEngine
from ticket_bot.browser.playwright_engine import PlaywrightEngine


def test_create_nodriver():
    engine = create_engine("nodriver")
    assert isinstance(engine, NodriverEngine)


def test_create_playwright():
    engine = create_engine("playwright")
    assert isinstance(engine, PlaywrightEngine)


def test_create_case_insensitive():
    assert isinstance(create_engine("NoDriver"), NodriverEngine)
    assert isinstance(create_engine("PLAYWRIGHT"), PlaywrightEngine)


def test_create_unknown():
    with pytest.raises(ValueError, match="不支援的瀏覽器引擎"):
        create_engine("selenium")
