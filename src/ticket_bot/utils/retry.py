"""tenacity retry 封裝 + RL adaptive retry"""

from __future__ import annotations

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from ticket_bot.rl.adaptive_retry import AdaptiveRetry

# 預設 retry 裝飾器：網路請求用（保留向後相容）
network_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30) + wait_random(0, 2),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)

# 模組層級 RL retry 實例（搶票專用）
_adaptive_retry = AdaptiveRetry()


def get_adaptive_retry() -> AdaptiveRetry:
    """取得全域 AdaptiveRetry 實例"""
    return _adaptive_retry
