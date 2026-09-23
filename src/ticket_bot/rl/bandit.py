"""Thompson Sampling Bandit — 動態調整 captcha confidence threshold

將 [0.1, 1.0] 區間離散化為 N 個 arm，每個 arm 維護 Beta(α, β) 分佈。
每次搶票時從各 arm 取樣，選最高者作為本次 threshold。
提交結果回傳 reward 後更新對應 arm。

持久化為 JSON，跨場次累積學習。
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

# 預設區間：0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9
DEFAULT_ARMS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


class ThresholdBandit:
    """Thompson Sampling 多臂拉霸機：選擇最佳 captcha confidence threshold。

    每個 arm 代表一個離散閾值，使用 Beta 分佈建模成功機率。
    - 成功 = captcha 提交後通過（不被 tixcraft 拒絕）
    - 失敗 = 提交後被拒絕 or 超過時間限制
    """

    def __init__(
        self,
        arms: list[float] | None = None,
        persist_path: str | Path = "data/rl/captcha_bandit.json",
    ):
        self.arms = arms or DEFAULT_ARMS
        self.persist_path = Path(persist_path)
        # Beta 分佈參數：alpha=成功次數+1, beta=失敗次數+1
        self.alpha = {a: 1.0 for a in self.arms}
        self.beta = {a: 1.0 for a in self.arms}
        self._selected_arm: float | None = None
        self._load()

    def _load(self):
        """從磁碟載入歷史統計"""
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text())
            for arm_str, params in data.items():
                arm = float(arm_str)
                if arm in self.alpha:
                    self.alpha[arm] = params["alpha"]
                    self.beta[arm] = params["beta"]
            logger.info("Bandit 歷史載入完成: %d arms", len(data))
        except Exception as e:
            logger.warning("Bandit 載入失敗，使用預設值: %s", e)

    def _save(self):
        """持久化到磁碟"""
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                str(arm): {"alpha": self.alpha[arm], "beta": self.beta[arm]}
                for arm in self.arms
            }
            self.persist_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning("Bandit 儲存失敗: %s", e)

    def select(self) -> float:
        """Thompson Sampling: 從每個 arm 的 Beta 分佈取樣，選最高者。"""
        samples = {
            arm: random.betavariate(self.alpha[arm], self.beta[arm])
            for arm in self.arms
        }
        self._selected_arm = max(samples, key=samples.get)
        logger.info(
            "Bandit 選擇 threshold=%.2f (sampled: %s)",
            self._selected_arm,
            {f"{k:.1f}": f"{v:.3f}" for k, v in samples.items()},
        )
        return self._selected_arm

    def update(self, threshold: float | None = None, success: bool = True):
        """更新 arm 的 Beta 分佈參數。

        Args:
            threshold: 使用的閾值，None 則用上次 select() 的結果
            success: captcha 是否成功通過
        """
        arm = threshold if threshold is not None else self._selected_arm
        if arm is None or arm not in self.alpha:
            return
        if success:
            self.alpha[arm] += 1.0
        else:
            self.beta[arm] += 1.0
        self._save()
        logger.info(
            "Bandit 更新 arm=%.2f success=%s → α=%.0f β=%.0f",
            arm, success, self.alpha[arm], self.beta[arm],
        )

    def stats(self) -> dict[float, dict]:
        """回傳各 arm 的統計資訊"""
        result = {}
        for arm in self.arms:
            a, b = self.alpha[arm], self.beta[arm]
            mean = a / (a + b)
            trials = a + b - 2  # 扣除初始 prior
            result[arm] = {"mean": round(mean, 3), "trials": int(trials), "alpha": a, "beta": b}
        return result
