"""timer 模組測試"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from ticket_bot.utils.timer import get_ntp_offset, countdown_activate


def test_ntp_offset_fallback():
    """所有 NTP 伺服器失敗 → 回傳 0"""
    with patch("ticket_bot.utils.timer.ntplib.NTPClient") as mock_cls:
        mock_cls.return_value.request.side_effect = Exception("timeout")
        offset = get_ntp_offset()
        assert offset == 0.0


@pytest.mark.asyncio
async def test_countdown_past_time():
    """開賣時間已過 → 立即執行"""
    called = False

    async def action():
        nonlocal called
        called = True

    past = datetime.now(timezone.utc) - timedelta(seconds=5)

    with patch("ticket_bot.utils.timer.get_ntp_offset", return_value=0.0):
        await countdown_activate(past, action)

    assert called is True


@pytest.mark.asyncio
async def test_countdown_near_future():
    """開賣時間在 0.5 秒後 → 等待後執行"""
    called = False

    async def action():
        nonlocal called
        called = True

    future = datetime.now(timezone.utc) + timedelta(seconds=0.5)

    with patch("ticket_bot.utils.timer.get_ntp_offset", return_value=0.0):
        await countdown_activate(future, action)

    assert called is True
