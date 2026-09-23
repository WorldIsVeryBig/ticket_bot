"""retry 裝飾器測試"""

import pytest

from ticket_bot.utils.retry import network_retry


def test_network_retry_success():
    """第一次就成功 → 直接回傳"""
    call_count = 0

    @network_retry
    def good():
        nonlocal call_count
        call_count += 1
        return "ok"

    assert good() == "ok"
    assert call_count == 1


def test_network_retry_eventual_success():
    """前幾次失敗後成功"""
    call_count = 0

    @network_retry
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("retry me")
        return "recovered"

    assert flaky() == "recovered"
    assert call_count == 3


def test_network_retry_exhausted():
    """5 次都失敗 → 拋出原始例外"""

    @network_retry
    def always_fail():
        raise TimeoutError("always timeout")

    with pytest.raises(TimeoutError, match="always timeout"):
        always_fail()


def test_network_retry_non_retryable():
    """非網路錯誤 → 立即拋出"""

    @network_retry
    def value_err():
        raise ValueError("not retryable")

    with pytest.raises(ValueError, match="not retryable"):
        value_err()
