"""Contextual Bandit — 根據網路延遲選擇最佳 micro-burst 模式

Context = 網路延遲 (ms)，離散化為 bucket。
Arms = 預定義的 burst 時間模式（相對於 sale_time 的偏移量列表，單位 ms）。
每個 (context_bucket, arm) 組合維護獨立的 Beta 分佈。

持久化為 JSON，跨場次累積學習。
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

# 預定義 burst 模式：每個元素是相對 sale_time 的偏移量 (秒)
BURST_PATTERNS: dict[str, list[float]] = {
    "early_triple":   [-0.05, -0.02, 0.0],          # 提前 50ms 起手
    "standard":       [-0.02, 0.0, 0.05],            # 目前預設
    "tight_quad":     [-0.01, 0.0, 0.01, 0.02],      # 密集四連發
    "spread_triple":  [0.0, 0.03, 0.06],              # 等距三連
    "aggressive_five":[-0.03, -0.01, 0.0, 0.02, 0.05],# 五連發
}

# 延遲 bucket 邊界 (ms)
LATENCY_BUCKETS = [
    (0, 20, "ultra_low"),     # <20ms (同機房)
    (20, 50, "low"),          # 20-50ms (同區域)
    (50, 100, "medium"),      # 50-100ms (跨區域)
    (100, 300, "high"),       # 100-300ms (國際)
    (300, float("inf"), "very_high"),  # >300ms
]


def _latency_to_bucket(latency_ms: float) -> str:
    """將延遲值對應到 bucket 名稱"""
    for lo, hi, name in LATENCY_BUCKETS:
        if lo <= latency_ms < hi:
            return name
    return "very_high"


class BurstBandit:
    """Contextual Bandit：根據網路延遲選擇最佳 burst 模式。

    每個 (latency_bucket, pattern_name) 維護 Beta(α, β)，
    用 Thompson Sampling 選擇當前 context 下的最佳 pattern。
    """

    def __init__(
        self,
        patterns: dict[str, list[float]] | None = None,
        persist_path: str | Path = "data/rl/burst_bandit.json",
    ):
        self.patterns = patterns or BURST_PATTERNS
        self.persist_path = Path(persist_path)
        # {bucket_name: {pattern_name: {alpha, beta}}}
        self._params: dict[str, dict[str, dict[str, float]]] = {}
        self._init_params()
        self._load()
        self._last_selection: tuple[str, str] | None = None  # (bucket, pattern_name)

    def _init_params(self):
        """初始化所有 (bucket, pattern) 的 Beta 參數"""
        for _, _, bucket in LATENCY_BUCKETS:
            self._params[bucket] = {}
            for name in self.patterns:
                self._params[bucket][name] = {"alpha": 1.0, "beta": 1.0}

    def _load(self):
        """從磁碟載入歷史"""
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text())
            for bucket, arms in data.items():
                if bucket not in self._params:
                    continue
                for name, params in arms.items():
                    if name in self._params[bucket]:
                        self._params[bucket][name] = params
            logger.info("BurstBandit 歷史載入完成")
        except Exception as e:
            logger.warning("BurstBandit 載入失敗: %s", e)

    def _save(self):
        """持久化"""
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            self.persist_path.write_text(json.dumps(self._params, indent=2))
        except Exception as e:
            logger.warning("BurstBandit 儲存失敗: %s", e)

    def select(self, latency_ms: float) -> tuple[str, list[float]]:
        """根據目前延遲選擇最佳 burst pattern。

        Returns:
            (pattern_name, offsets_list)
        """
        bucket = _latency_to_bucket(latency_ms)
        arms = self._params[bucket]

        samples = {
            name: random.betavariate(p["alpha"], p["beta"])
            for name, p in arms.items()
        }
        best_name = max(samples, key=samples.get)
        self._last_selection = (bucket, best_name)

        logger.info(
            "BurstBandit context=%s(%.0fms) → %s %s (samples: %s)",
            bucket, latency_ms, best_name, self.patterns[best_name],
            {k: f"{v:.3f}" for k, v in samples.items()},
        )
        return best_name, self.patterns[best_name]

    def update(
        self,
        success: bool,
        bucket: str | None = None,
        pattern_name: str | None = None,
    ):
        """回報結果。

        Args:
            success: 是否搶到票
            bucket: 延遲 bucket，None 則用上次 select() 的
            pattern_name: pattern 名稱，None 則用上次 select() 的
        """
        if bucket is None or pattern_name is None:
            if self._last_selection is None:
                return
            bucket, pattern_name = self._last_selection

        if bucket not in self._params or pattern_name not in self._params[bucket]:
            return

        p = self._params[bucket][pattern_name]
        if success:
            p["alpha"] += 1.0
        else:
            p["beta"] += 1.0
        self._save()
        logger.info(
            "BurstBandit 更新 %s/%s success=%s → α=%.0f β=%.0f",
            bucket, pattern_name, success, p["alpha"], p["beta"],
        )

    def stats(self) -> dict:
        """回傳統計"""
        result = {}
        for bucket, arms in self._params.items():
            result[bucket] = {}
            for name, p in arms.items():
                a, b = p["alpha"], p["beta"]
                result[bucket][name] = {
                    "mean": round(a / (a + b), 3),
                    "trials": int(a + b - 2),
                }
        return result
