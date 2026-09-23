"""Q-Learning Adaptive Retry — 取代固定 exponential backoff

搶票場景跟一般 API 不同：
  - 開賣瞬間伺服器極度壅塞，但恢復快
  - 固定 backoff (2→4→8→16→30s) 會錯過恢復黃金期
  - 需要根據 (重試次數, 距開賣時間, 上次 HTTP 狀態碼) 動態決定等待時間

State: (retry_count_bucket, elapsed_bucket, response_type)
Action: wait_time ∈ {0.1, 0.3, 0.5, 1.0, 2.0, 5.0} 秒
Reward: +1 成功, -0.1 × waited_seconds (時間懲罰)

使用 tabular Q-learning，epsilon-greedy 探索。
持久化 Q-table 為 JSON。
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 動作空間：可選的等待時間 (秒)
WAIT_ACTIONS = [0.1, 0.3, 0.5, 1.0, 2.0, 5.0]

# 重試次數 bucket
RETRY_BUCKETS = ["first", "second", "third", "many"]  # 0, 1, 2, 3+

# 距開賣經過時間 bucket (秒)
ELAPSED_BUCKETS = ["burst", "early", "mid", "late"]  # <5s, 5-15s, 15-60s, >60s

# 上次回應類型
RESPONSE_TYPES = ["success", "server_error", "rate_limit", "timeout", "other_error"]


def _retry_bucket(count: int) -> str:
    if count <= 0:
        return "first"
    elif count == 1:
        return "second"
    elif count == 2:
        return "third"
    return "many"


def _elapsed_bucket(elapsed_sec: float) -> str:
    if elapsed_sec < 5:
        return "burst"
    elif elapsed_sec < 15:
        return "early"
    elif elapsed_sec < 60:
        return "mid"
    return "late"


def _classify_response(status_code: int | None, error: Exception | None) -> str:
    if error is not None:
        if isinstance(error, TimeoutError):
            return "timeout"
        return "other_error"
    if status_code is None:
        return "other_error"
    if 200 <= status_code < 400:
        return "success"
    if status_code == 429:
        return "rate_limit"
    if status_code >= 500:
        return "server_error"
    return "other_error"


class AdaptiveRetry:
    """Q-Learning 自適應重試策略。

    取代固定的 exponential backoff，根據搶票情境動態選擇等待時間。
    """

    def __init__(
        self,
        alpha: float = 0.1,          # 學習率
        gamma: float = 0.95,         # 折扣因子
        epsilon: float = 0.15,       # 探索率
        epsilon_decay: float = 0.995,# 探索衰減
        epsilon_min: float = 0.05,   # 最低探索率
        max_retries: int = 8,        # 最大重試次數
        persist_path: str | Path = "data/rl/retry_qtable.json",
    ):
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_decay = epsilon_decay
        self.epsilon_min = epsilon_min
        self.max_retries = max_retries
        self.persist_path = Path(persist_path)

        # Q-table: {state_key: {action_str: q_value}}
        self._q: dict[str, dict[str, float]] = {}
        self._init_q()
        self._load()

        # 追蹤當前 episode
        self._episode_start: float | None = None
        self._retry_count = 0
        self._last_state: str | None = None
        self._last_action: float | None = None

    def _init_q(self):
        """初始化 Q-table：所有 state-action pair 設為 0"""
        for rb in RETRY_BUCKETS:
            for eb in ELAPSED_BUCKETS:
                for rt in RESPONSE_TYPES:
                    key = f"{rb}|{eb}|{rt}"
                    if key not in self._q:
                        self._q[key] = {str(a): 0.0 for a in WAIT_ACTIONS}

    def _load(self):
        """載入 Q-table"""
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text())
            q_data = data.get("q_table", {})
            for key, actions in q_data.items():
                if key in self._q:
                    for a_str, val in actions.items():
                        if a_str in self._q[key]:
                            self._q[key][a_str] = val
            self.epsilon = data.get("epsilon", self.epsilon)
            logger.info("Q-table 載入完成 (%d states, ε=%.3f)", len(q_data), self.epsilon)
        except Exception as e:
            logger.warning("Q-table 載入失敗: %s", e)

    def _save(self):
        """持久化 Q-table"""
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"q_table": self._q, "epsilon": self.epsilon}
            self.persist_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning("Q-table 儲存失敗: %s", e)

    def _state_key(
        self,
        retry_count: int,
        elapsed_sec: float,
        response_type: str,
    ) -> str:
        return f"{_retry_bucket(retry_count)}|{_elapsed_bucket(elapsed_sec)}|{response_type}"

    def start_episode(self):
        """開始新的搶票 episode（一次完整的搶票嘗試）"""
        self._episode_start = time.time()
        self._retry_count = 0
        self._last_state = None
        self._last_action = None

    def get_wait_time(
        self,
        status_code: int | None = None,
        error: Exception | None = None,
    ) -> float:
        """根據當前狀態選擇等待時間。

        Args:
            status_code: 上次 HTTP 回應碼
            error: 上次的例外（如果有）

        Returns:
            建議等待秒數
        """
        elapsed = time.time() - self._episode_start if self._episode_start else 0
        response_type = _classify_response(status_code, error)
        state = self._state_key(self._retry_count, elapsed, response_type)

        # epsilon-greedy 選擇
        if random.random() < self.epsilon:
            action = random.choice(WAIT_ACTIONS)
            logger.debug("Q-learning 探索: state=%s action=%.1fs", state, action)
        else:
            q_vals = self._q.get(state, {})
            if q_vals:
                action = float(max(q_vals, key=q_vals.get))
            else:
                action = random.choice(WAIT_ACTIONS)
            logger.debug("Q-learning 利用: state=%s action=%.1fs", state, action)

        self._last_state = state
        self._last_action = action
        self._retry_count += 1
        return action

    def update(
        self,
        success: bool,
        status_code: int | None = None,
        error: Exception | None = None,
    ):
        """更新 Q-table。

        在每次重試後呼叫，或在最終成功/失敗時呼叫。

        Args:
            success: 這次請求是否成功
            status_code: 新的 HTTP 回應碼
            error: 新的例外
        """
        if self._last_state is None or self._last_action is None:
            return

        old_state = self._last_state
        action_str = str(self._last_action)

        # 計算 reward
        time_penalty = -0.1 * self._last_action  # 等越久懲罰越重
        if success:
            reward = 1.0 + time_penalty
        else:
            reward = time_penalty

        # 計算新狀態的 max Q（用於 TD update）
        elapsed = time.time() - self._episode_start if self._episode_start else 0
        response_type = _classify_response(status_code, error)
        new_state = self._state_key(self._retry_count, elapsed, response_type)
        new_q_vals = self._q.get(new_state, {})
        max_next_q = max(new_q_vals.values()) if new_q_vals else 0.0

        # Q-learning update: Q(s,a) ← Q(s,a) + α[r + γ·max Q(s',a') - Q(s,a)]
        old_q = self._q.get(old_state, {}).get(action_str, 0.0)
        if success:
            # terminal state，no future reward
            new_q = old_q + self.alpha * (reward - old_q)
        else:
            new_q = old_q + self.alpha * (reward + self.gamma * max_next_q - old_q)

        if old_state in self._q:
            self._q[old_state][action_str] = new_q

        # 衰減探索率
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

        logger.info(
            "Q-update: s=%s a=%.1fs r=%.2f Q: %.3f→%.3f (ε=%.3f)",
            old_state, self._last_action, reward, old_q, new_q, self.epsilon,
        )
        self._save()

    @property
    def should_retry(self) -> bool:
        """是否還能重試"""
        return self._retry_count < self.max_retries

    def stats(self) -> dict:
        """回傳 Q-table 統計：每個 state 的最佳 action"""
        result = {}
        for state, actions in self._q.items():
            if not actions:
                continue
            best_action = max(actions, key=actions.get)
            best_q = actions[best_action]
            if best_q != 0.0:  # 只顯示有學習過的
                result[state] = {
                    "best_action": f"{float(best_action):.1f}s",
                    "q_value": round(best_q, 3),
                }
        return result
