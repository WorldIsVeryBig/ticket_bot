"""NTP 時間同步與精準倒數啟動 — 支援 RL burst pattern 選擇"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import ntplib

from ticket_bot.rl.burst_bandit import BurstBandit

logger = logging.getLogger(__name__)

NTP_SERVERS = ["pool.ntp.org", "time.google.com", "time.cloudflare.com"]

# 模組層級 burst bandit 實例（跨場次共用）
_burst_bandit = BurstBandit()


def get_burst_bandit() -> BurstBandit:
    """取得全域 BurstBandit 實例供外部回報結果"""
    return _burst_bandit


def get_ntp_offset() -> float:
    """取得本機與 NTP 伺服器的時間偏移量（秒）"""
    client = ntplib.NTPClient()
    for server in NTP_SERVERS:
        try:
            resp = client.request(server, version=3)
            offset = resp.tx_time - time.time()
            logger.info("NTP 同步成功 (%s)，偏移量: %.4f 秒", server, offset)
            return offset
        except Exception:
            logger.warning("NTP 伺服器 %s 連線失敗，嘗試下一個...", server)
    logger.warning("所有 NTP 伺服器都無法連線，使用本機時間 (offset=0)")
    return 0.0


def _measure_latency(host: str = "tixcraft.com", port: int = 443) -> float:
    """測量到目標主機的 TCP 延遲 (ms)"""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        start = time.perf_counter()
        sock.connect((host, port))
        latency_ms = (time.perf_counter() - start) * 1000
        sock.close()
        return latency_ms
    except Exception:
        logger.warning("延遲測量失敗，使用預設 50ms")
        return 50.0


async def countdown_activate(
    sale_time: datetime,
    action,
    latency_ms: float | None = None,
) -> None:
    """
    NTP 同步倒數，精準在 sale_time 執行 action。
    使用 RL contextual bandit 根據網路延遲自動選擇最佳 burst pattern。

    Args:
        sale_time: 開賣時間（需含 timezone）
        action: async callable，到時間時執行
        latency_ms: 網路延遲 (ms)，None 則自動測量
    """
    offset = get_ntp_offset()
    target_ts = sale_time.timestamp()

    def corrected_now() -> float:
        return time.time() + offset

    remaining = target_ts - corrected_now()
    if remaining < 0:
        logger.warning("開賣時間已過 (%.1f 秒前)，立即執行", -remaining)
        await action()
        return

    # 測量延遲 & 選擇 burst pattern
    if latency_ms is None:
        latency_ms = _measure_latency()
    pattern_name, offsets = _burst_bandit.select(latency_ms)
    logger.info(
        "倒數開始，距開賣 %.1f 秒 | 延遲 %.0fms | burst=%s %s",
        remaining, latency_ms, pattern_name, offsets,
    )

    # 粗略等待：距開賣 >2 秒時用 sleep
    while True:
        remaining = target_ts - corrected_now()
        if remaining <= 2.0:
            break
        logger.info("  T-%.1fs", remaining)
        await asyncio.sleep(min(remaining - 2.0, 1.0))

    # 最後 2 秒精密 busy-wait：等到第一個 burst offset 前 1ms
    first_offset = min(offsets)
    wait_until = target_ts + first_offset - 0.001
    while corrected_now() < wait_until:
        await asyncio.sleep(0.001)

    # RL burst：按選定的 pattern 發射
    logger.info("進入 RL burst 模式: %s", pattern_name)
    tasks = []
    for i, offset_sec in enumerate(offsets):
        fire_at = target_ts + offset_sec
        now = corrected_now()
        if fire_at > now:
            # spin-wait 到精確時刻
            while corrected_now() < fire_at:
                pass
        tasks.append(asyncio.create_task(action()))
        logger.debug("  burst #%d fired at T%+.0fms", i, offset_sec * 1000)

    logger.info("RL burst 發射完成! %s", datetime.now(timezone.utc).isoformat())
    await asyncio.gather(*tasks, return_exceptions=True)
